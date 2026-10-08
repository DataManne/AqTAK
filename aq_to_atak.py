import json
import os
import socket
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.sax.saxutils import escape

import requests

CONFIG_PATH = Path("/data/aqtak.conf")
DEFAULT_CONFIG_RELOAD_SECONDS = 5

OPENAQ_API_KEY = os.environ.get("OPENAQ_API_KEY", "")
if not OPENAQ_API_KEY:
    raise RuntimeError("OPENAQ_API_KEY is not set in the environment")


# ---------------------------------------------------------------------------
# AQI (China HJ 633-2012) collapsed to the four ATAK affiliation colours.
# The colour in ATAK comes from the affiliation part of the CoT type:
#   a-f = blue, a-n = green, a-u = yellow, a-h = red
# ---------------------------------------------------------------------------
# (max AQI, colour, CoT type, label, advice)
LEVELS = [
    (50, "blue", "a-f-G-E-S", "Excellent", "No restrictions"),
    (100, "green", "a-n-G-E-S", "Good", "Acceptable; unusually sensitive people take care"),
    (150, "yellow", "a-u-G-E-S", "Lightly polluted", "Sensitive groups limit prolonged outdoor exposure"),
    (float("inf"), "red", "a-h-G-E-S", "Moderately polluted or worse", "Everyone limit outdoor exposure"),
]

# Concentration breakpoints and the AQI value at each one.
# PM and gases in ug/m3, CO in mg/m3. PM/SO2/NO2/CO use 24h tables, O3 the 8h table.
IAQI = [0, 50, 100, 150, 200, 300, 400, 500]
AQI_TABLES = {
    "pm25": ([0, 35, 75, 115, 150, 250, 350, 500], IAQI),
    "pm10": ([0, 50, 150, 250, 350, 420, 500, 600], IAQI),
    "so2": ([0, 50, 150, 475, 800, 1600, 2100, 2620], IAQI),
    "no2": ([0, 40, 80, 180, 280, 565, 750, 940], IAQI),
    "co": ([0, 2, 4, 14, 24, 36, 48, 60], IAQI),
    "o3": ([0, 100, 160, 215, 265, 800], [0, 50, 100, 150, 200, 300]),
}
MOLAR_MASS = {"so2": 64.07, "no2": 46.01, "o3": 48.0, "co": 28.01}
PARTICULATES = {"pm25", "pm10"}


class Config:
    def __init__(self):
        self.data = {}
        self.last_mtime = None
        self.load()

    def default_values(self):
        return {
            "locations": "",
            "poll_interval": "10",
            "stale_minutes": "10",
            "cot_type_fallback": "a-f-G-E-S",
            "cot_host": "127.0.0.1",
            "cot_port": "8087",
            "callsign_prefix": "AQ",
            "config_reload": "5",
            "database": "/data/aqtak-{last_edit}.db",
            "log_level": "INFO",
            "openaq_api": "https://api.openaq.org/v3",
            "enable_aqi_colors": "true",
            "max_data_age_minutes": "60",
        }

    def load(self):
        if not CONFIG_PATH.exists():
            print(f"WARNING: {CONFIG_PATH} does not exist; using defaults")
            self.data = self.default_values()
            self.last_mtime = None
            return

        try:
            self.last_mtime = CONFIG_PATH.stat().st_mtime
            parsed = self.default_values()
            with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
                for line in fh.read().splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    parsed[key.strip()] = value.strip()
            self.data = parsed
        except Exception as exc:
            print(f"ERROR reading config: {exc}")
            self.data = self.default_values()
            self.last_mtime = None

    def get(self, key, default=None):
        return self.data.get(key, default)

    def get_int(self, key, default=0):
        try:
            return int(self.get(key, default))
        except (TypeError, ValueError):
            return default

    def get_bool(self, key, default=True):
        val = str(self.get(key, default)).lower()
        return val in ("true", "1", "yes", "on")

    def get_locations(self):
        raw = self.get("locations", "")
        ids = []
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                ids.append(int(part))
            except ValueError:
                print(f"WARNING: invalid location id in config: {part}")
        return ids

    def get_database_path(self):
        template = self.get("database", "/data/aqtak-{last_edit}.db")
        if CONFIG_PATH.exists():
            stamp = datetime.fromtimestamp(CONFIG_PATH.stat().st_mtime).strftime("%Y%m%d-%H%M%S")
        else:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        return template.replace("{last_edit}", stamp)

    def has_changed(self):
        if not CONFIG_PATH.exists():
            return False
        try:
            return CONFIG_PATH.stat().st_mtime != self.last_mtime
        except Exception:
            return False


class StationDB:
    def __init__(self, db_path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _init_schema(self):
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS stations (
                location_id INTEGER PRIMARY KEY,
                name TEXT,
                latitude REAL,
                longitude REAL,
                provider TEXT,
                sensors_json TEXT,
                loaded_at TEXT
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS measurements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                location_id INTEGER,
                observed_at TEXT,
                parameter TEXT,
                value REAL,
                units TEXT
            )
            """
        )
        self.conn.commit()

    def save_station(self, station):
        self.conn.execute(
            """
            INSERT OR REPLACE INTO stations
            (location_id, name, latitude, longitude, provider, sensors_json, loaded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                station["location_id"],
                station["name"],
                station["lat"],
                station["lon"],
                station["provider"],
                json.dumps(station["sensors"]),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self.conn.commit()

    def save_measurement(self, location_id, parameter, value, units, observed_at=None):
        if observed_at is None:
            observed_at = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """
            INSERT INTO measurements (location_id, observed_at, parameter, value, units)
            VALUES (?, ?, ?, ?, ?)
            """,
            (location_id, observed_at, parameter, value, units),
        )
        self.conn.commit()

    def get_all_station_ids(self):
        """Get all location IDs currently in the database."""
        cursor = self.conn.execute("SELECT location_id FROM stations")
        return set(row[0] for row in cursor.fetchall())

    def cleanup_location(self, location_id):
        """Remove all data for a location no longer in config."""
        self.conn.execute("DELETE FROM measurements WHERE location_id = ?", (location_id,))
        self.conn.execute("DELETE FROM stations WHERE location_id = ?", (location_id,))
        self.conn.commit()
        print(f"  Cleaned up location {location_id}: deleted all stations and measurements")

    def close(self):
        if self.conn:
            self.conn.close()


def api_get(api_base, path):
    headers = {"X-API-Key": OPENAQ_API_KEY}
    response = requests.get(f"{api_base}{path}", headers=headers, timeout=15)
    response.raise_for_status()
    return response.json()


def build_station(api_base, location_id):
    results = api_get(api_base, f"/locations/{location_id}").get("results", [])
    if not results:
        raise RuntimeError(f"OpenAQ location {location_id} returned no data")
    location = results[0]

    coords = location.get("coordinates", {})
    lat = coords.get("latitude")
    lon = coords.get("longitude")
    if lat is None or lon is None:
        raise RuntimeError(f"Location {location_id} has no coordinates")

    name = location.get("name") or f"OpenAQ {location_id}"
    provider_name = (location.get("provider") or {}).get("name", "OpenAQ")

    sensors = {}
    for sensor in location.get("sensors", []):
        sensor_id = sensor.get("id")
        parameter = sensor.get("parameter", {})
        parameter_name = parameter.get("name")
        if sensor_id is None or parameter_name is None:
            continue
        sensors[sensor_id] = {"name": parameter_name, "units": parameter.get("units")}

    print(f"Loaded location {location_id}: {name} ({lat}, {lon})")
    print(f"  Provider: {provider_name}")
    print(f"  Sensors: {', '.join(s['name'] for s in sensors.values())}")
    return {
        "location_id": location_id,
        "name": name,
        "lat": lat,
        "lon": lon,
        "provider": provider_name,
        "sensors": sensors,
    }


def load_stations(api_base, location_ids):
    stations = []
    for location_id in location_ids:
        try:
            stations.append(build_station(api_base, location_id))
        except Exception as exc:
            print(f"ERROR loading location {location_id}: {exc}")
    return stations


def get_latest_measurements(api_base, station, config):
    """Fetch latest measurements, filtering out data older than max_data_age_minutes."""
    results = api_get(api_base, f"/locations/{station['location_id']}/latest").get("results", [])
    now = datetime.now(timezone.utc)
    max_age = timedelta(minutes=config.get_int("max_data_age_minutes", 60))
    values = {}
    
    for item in results:
        sensor = station["sensors"].get(item.get("sensorsId"))
        if not sensor:
            continue
        
        # Check data age
        observed = item.get("dateObserved")
        if observed:
            try:
                obs_time = datetime.fromisoformat(observed.replace("Z", "+00:00"))
                age = now - obs_time
                if age > max_age:
                    age_minutes = int(age.total_seconds() / 60)
                    print(f"  Skipping {sensor['name']}: data {age_minutes}m old (max {config.get_int('max_data_age_minutes', 60)}m)")
                    continue
            except (ValueError, TypeError):
                print(f"  Warning: could not parse dateObserved for {sensor['name']}")
        
        values[sensor["name"]] = {"value": item.get("value"), "units": sensor.get("units")}
    
    print(f"{station['name']} measurements: {values}")
    return values


def sanitize_for_xml(text):
    if not text:
        return text
    for char, repl in {"\u00b5": "u", "\u03bc": "u", "\u00b3": "3", "\u00b2": "2", "\u00b0": "deg"}.items():
        text = text.replace(char, repl)
    return text


def to_standard_units(param, value, units):
    """Convert to ug/m3 (mg/m3 for CO). Returns None if the units are unknown."""
    u = (units or "").lower().replace("\u00b5", "u").replace("\u03bc", "u").replace(" ", "")
    if u == "ug/m3":
        ug = value
    elif u == "mg/m3":
        ug = value * 1000
    elif u == "ppm" and param in MOLAR_MASS:
        ug = value * MOLAR_MASS[param] / 24.45 * 1000
    elif u == "ppb" and param in MOLAR_MASS:
        ug = value * MOLAR_MASS[param] / 24.45
    else:
        return None
    return ug / 1000 if param == "co" else ug


def calculate_iaqi(param, value, units):
    """Individual AQI for one pollutant, or None if not an AQI pollutant."""
    table = AQI_TABLES.get(param.lower())
    if table is None or value is None:
        return None
    conc = to_standard_units(param.lower(), max(float(value), 0.0), units)
    if conc is None:
        print(f"  Skipping {param}: unsupported units '{units}'")
        return None
    bps, aqis = table
    if conc >= bps[-1]:
        return aqis[-1]
    for i in range(1, len(bps)):
        if conc <= bps[i]:
            lo_c, hi_c = bps[i - 1], bps[i]
            lo_a, hi_a = aqis[i - 1], aqis[i]
            return int(round(lo_a + (conc - lo_c) * (hi_a - lo_a) / (hi_c - lo_c)))
    return aqis[-1]


def get_highest_aqi(data):
    """Highest individual AQI across all pollutants: (aqi, parameter) or (None, None)."""
    best, best_param = None, None
    for param, m in data.items():
        aqi = calculate_iaqi(param, m.get("value"), m.get("units"))
        if aqi is not None and (best is None or aqi > best):
            best, best_param = aqi, param
    return best, best_param


def get_level(aqi):
    for level in LEVELS:
        if aqi <= level[0]:
            return level
    return LEVELS[-1]


PARAMETER_NAMES = {
    "pm1": "PM1", "pm25": "PM2.5", "pm10": "PM10",
    "relativehumidity": "Humidity", "temperature": "Temperature",
    "um003": "UM003", "um005": "UM005", "um010": "UM010", "pm03_count": "PM0.3 Count",
    "o3": "O3", "no2": "NO2", "so2": "SO2", "co": "CO",
}


def make_cot(config, station, data):
    now = datetime.now(timezone.utc)
    stale_time = now + timedelta(minutes=config.get_int("stale_minutes", 10))
    time_string = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    stale_string = stale_time.strftime("%Y-%m-%dT%H:%M:%SZ")

    cot_type = config.get("cot_type_fallback", "a-f-G-E-S")
    remarks_lines = []

    if config.get_bool("enable_aqi_colors", True):
        aqi, param = get_highest_aqi(data)
        if aqi is None:
            remarks_lines.append("AQI: n/a (no AQI pollutants reported)")
        else:
            _, color, level_type, label, advice = get_level(aqi)
            cot_type = level_type
            shown = PARAMETER_NAMES.get(param, param.upper())
            remarks_lines.append(f"AQI: {aqi} {label} ({color.upper()})")
            remarks_lines.append(f"Driver: {shown}")
            remarks_lines.append(f"Advice: {advice}")
            if aqi > 50:
                if param in PARTICULATES:
                    remarks_lines.append("Mask: N95/FFP2 reduces particulate exposure")
                else:
                    remarks_lines.append("Mask: ordinary masks do NOT stop this gas")
        remarks_lines.append("")

    for parameter, m in data.items():
        display = PARAMETER_NAMES.get(parameter, parameter.replace("_", " ").upper())
        units = sanitize_for_xml(m.get("units"))
        remarks_lines.append(f"{display}: {m.get('value')} {units}" if units else f"{display}: {m.get('value')}")

    remarks = "\n".join(remarks_lines) + f"\nSource: {station['provider']}\nLocation: {station['name']}"

    uid = f"openaq.{station['location_id']}"
    callsign = f"{config.get('callsign_prefix', 'AQ')} {station['name']}"

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<event version="2.0"
       uid="{escape(uid)}"
       type="{cot_type}"
       how="m-g"
       time="{time_string}"
       start="{time_string}"
       stale="{stale_string}">
    <point lat="{station['lat']}" lon="{station['lon']}" hae="0" ce="10" le="10"/>
    <detail>
        <contact callsign="{escape(callsign)}"/>
        <remarks>{escape(remarks)}</remarks>
    </detail>
</event>
"""


class StationManager:
    def __init__(self):
        self.config = Config()
        self.stations = []
        self.db = None
        self.api_base = self.config.get("openaq_api")
        self.lock = threading.Lock()
        self.ready = False

    def reload(self):
        try:
            config = Config()
            api_base = config.get("openaq_api")
            location_ids = config.get_locations()
            stations = load_stations(api_base, location_ids)
            if location_ids and not stations:
                raise RuntimeError("no configured locations could be loaded")

            db = StationDB(config.get_database_path())
            for station in stations:
                db.save_station(station)

            # Cleanup: delete data for locations no longer in config
            db_location_ids = db.get_all_station_ids()
            config_location_ids = set(location_ids)
            removed_ids = db_location_ids - config_location_ids
            if removed_ids:
                print(f"Cleaning up removed locations: {removed_ids}")
                for loc_id in removed_ids:
                    db.cleanup_location(loc_id)

            with self.lock:
                if self.db is not None:
                    self.db.close()
                self.db = db
                self.config = config
                self.stations = stations
                self.api_base = api_base
                self.ready = True
            print(f"Loaded {len(stations)} of {len(location_ids)} configured locations")
            print(f"AQI colours: {'enabled' if config.get_bool('enable_aqi_colors') else 'disabled'}")
            print(f"Max data age: {config.get_int('max_data_age_minutes', 60)} minutes")
        except Exception as exc:
            print(f"ERROR: reload() failed: {exc}")

    def get_config(self):
        with self.lock:
            return self.config

    def snapshot(self):
        with self.lock:
            return self.config, list(self.stations), self.db, self.api_base, self.ready

    def check_reload(self):
        if self.get_config().has_changed():
            print("Configuration changed; reloading...")
            self.reload()

    def send_all(self, conn):
        config, stations, db, api_base, _ = self.snapshot()
        if not stations:
            print("WARNING: no stations to send")
            return
        for station in stations:
            try:
                data = get_latest_measurements(api_base, station, config)
                if not data:
                    print(f"  No valid measurements for {station['location_id']} (all stale or filtered)")
                    continue
                for parameter, m in data.items():
                    db.save_measurement(station["location_id"], parameter, m.get("value"), m.get("units"))
                cot = make_cot(config, station, data)
            except Exception as exc:
                print(f"ERROR updating station {station['location_id']}: {exc}")
                continue
            conn.sendall(cot.encode("utf-8"))  # socket errors propagate -> reconnect
            print(f"CoT sent: openaq.{station['location_id']}")


def main():
    manager = StationManager()

    def initial_load():
        while True:
            manager.reload()
            if manager.snapshot()[4]:
                return
            time.sleep(30)

    threading.Thread(target=initial_load, daemon=True).start()

    def config_watchdog():
        while True:
            time.sleep(manager.get_config().get_int("config_reload", DEFAULT_CONFIG_RELOAD_SECONDS))
            try:
                manager.check_reload()
            except Exception as exc:
                print(f"ERROR during config reload check: {exc}")

    threading.Thread(target=config_watchdog, daemon=True).start()

    print("AQTAK v2 ready (loading stations in background)")

    while True:
        config, _, _, _, ready = manager.snapshot()
        if not ready:
            time.sleep(1)
            continue

        host = config.get("cot_host", "127.0.0.1")
        port = config.get_int("cot_port", 8087)
        print(f"Connecting to Taky at {host}:{port}")
        try:
            with socket.create_connection((host, port), timeout=10) as conn:
                conn.settimeout(None)
                print(f"Connected to Taky at {host}:{port}")
                while True:
                    manager.send_all(conn)
                    time.sleep(manager.get_config().get_int("poll_interval", 10))
                    new = manager.get_config()
                    if (new.get("cot_host"), new.get_int("cot_port", 8087)) != (host, port):
                        print("Taky address changed; reconnecting")
                        break
        except KeyboardInterrupt:
            print("Shutting down")
            break
        except OSError as exc:
            print(f"Connection failed: {exc}")
            time.sleep(5)


if __name__ == "__main__":
    main()
