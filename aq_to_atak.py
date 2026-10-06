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

# Configuration
CONFIG_PATH = Path("/data/aqtak.conf")
DEFAULT_CONFIG_RELOAD_SECONDS = 5

OPENAQ_API_KEY = os.environ.get("OPENAQ_API_KEY", "")
if not OPENAQ_API_KEY:
    raise RuntimeError("OPENAQ_API_KEY is not set in the environment")


# AQI color mapping (China standard, 4-color simplified)
# Maps AQI ranges to CoT type and visual description
AQI_COLORS = {
    "blue": {
        "range": (0, 35),
        "label": "Excellent",
        "cot_type": "a-h-G-E-S",
        "description": "Air quality is good; suitable for all outdoor activities"
    },
    "green": {
        "range": (36, 75),
        "label": "Good",
        "cot_type": "a-h-G-E-S",
        "description": "Air quality is acceptable; most people can engage in outdoor activities"
    },
    "yellow": {
        "range": (76, 150),
        "label": "Lightly Polluted",
        "cot_type": "a-h-G-E-S",
        "description": "Lightly polluted; sensitive groups should limit prolonged outdoor exposure"
    },
    "red": {
        "range": (151, 600),
        "label": "Heavily Polluted",
        "cot_type": "a-h-G-E-S",
        "description": "Heavily polluted; public should limit outdoor exposure; masks with PM2.5 filter recommended"
    },
}

# Pollutant AQI conversion formulas (China standard)
# Maps pollutant name to (breakpoints, AQI_breakpoints, units_expected)
POLLUTANT_AQI_PARAMS = {
    "pm25": {  # PM2.5 (µg/m³)
        "breakpoints": [35, 75, 115, 150, 250, 500],
        "aqi_breakpoints": [50, 100, 150, 200, 300, 500],
        "units": ["µg/m³", "ug/m³", "μg/m³"],
    },
    "pm10": {  # PM10 (µg/m³)
        "breakpoints": [50, 150, 250, 350, 420, 600],
        "aqi_breakpoints": [50, 100, 150, 200, 300, 500],
        "units": ["µg/m³", "ug/m³", "μg/m³"],
    },
    "o3": {  # Ozone (µg/m³)
        "breakpoints": [120, 160, 200, 1200],
        "aqi_breakpoints": [50, 100, 150, 500],
        "units": ["µg/m³", "ug/m³", "μg/m³"],
    },
    "no2": {  # Nitrogen dioxide (µg/m³)
        "breakpoints": [40, 80, 120, 180, 280, 565],
        "aqi_breakpoints": [50, 100, 150, 200, 300, 500],
        "units": ["µg/m³", "ug/m³", "μg/m³"],
    },
    "so2": {  # Sulfur dioxide (µg/m³)
        "breakpoints": [50, 150, 475, 800, 1600, 2620],
        "aqi_breakpoints": [50, 100, 150, 200, 300, 500],
        "units": ["µg/m³", "ug/m³", "μg/m³"],
    },
    "co": {  # Carbon monoxide (mg/m³)
        "breakpoints": [2, 4, 14, 24, 36, 60],
        "aqi_breakpoints": [50, 100, 150, 200, 300, 500],
        "units": ["mg/m³"],
    },
}


class Config:
    def __init__(self):
        self.data = {}
        self.last_mtime = None
        self.load()

    def default_values(self):
        return {
            "locations": "3400936",
            "poll_interval": "10",
            "stale_minutes": "10",
            "cot_type": "a-f-G-E-S-E",
            "cot_host": "0.0.0.0",
            "cot_port": "9000",
            "callsign_prefix": "AQ",
            "config_reload": "5",
            "database": "/data/aqtak-{last_edit}.db",
            "log_level": "INFO",
            "openaq_api": "https://api.openaq.org/v3",
            "enable_aqi_colors": "true",
        }

    def load(self):
        if not CONFIG_PATH.exists():
            print(f"WARNING: {CONFIG_PATH} does not exist; using defaults")
            self.data = self.default_values()
            self.last_mtime = None
            return

        try:
            self.last_mtime = CONFIG_PATH.stat().st_mtime
            with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
                raw = fh.read().splitlines()

            parsed = {}
            for line in raw:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
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
        val = self.get(key, str(default)).lower()
        return val in ["true", "1", "yes", "on"]

    def get_locations(self):
        raw = self.get("locations", "")
        if not raw:
            return []
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
            current = CONFIG_PATH.stat().st_mtime
            return current != self.last_mtime
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

    def save_measurement(self, location_id, parameter, value, units):
        self.conn.execute(
            """
            INSERT INTO measurements (location_id, observed_at, parameter, value, units)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                location_id,
                datetime.now(timezone.utc).isoformat(),
                parameter,
                value,
                units,
            ),
        )
        self.conn.commit()

    def close(self):
        if self.conn:
            self.conn.close()


def api_get(api_base, path):
    headers = {"X-API-Key": OPENAQ_API_KEY}
    url = f"{api_base}{path}"
    response = requests.get(url, headers=headers, timeout=15)
    response.raise_for_status()
    return response.json()


def get_location(api_base, location_id):
    data = api_get(api_base, f"/locations/{location_id}")
    results = data.get("results", [])
    if not results:
        raise RuntimeError(f"OpenAQ location {location_id} returned no data")
    return results[0]


def build_station(api_base, location_id):
    location = get_location(api_base, location_id)

    coords = location.get("coordinates", {})
    lat = coords.get("latitude")
    lon = coords.get("longitude")
    if lat is None or lon is None:
        raise RuntimeError(f"Location {location_id} has no coordinates")

    name = location.get("name") or f"OpenAQ {location_id}"
    provider_obj = location.get("provider", {})
    provider_name = provider_obj.get("name", "OpenAQ")

    sensors = {}
    for sensor in location.get("sensors", []):
        sensor_id = sensor.get("id")
        parameter = sensor.get("parameter", {})
        parameter_name = parameter.get("name")
        units = parameter.get("units")
        if sensor_id is None or parameter_name is None:
            continue
        sensors[sensor_id] = {
            "name": parameter_name,
            "units": units,
        }

    station = {
        "location_id": location_id,
        "name": name,
        "lat": lat,
        "lon": lon,
        "provider": provider_name,
        "sensors": sensors,
    }

    print(f"Loaded location {location_id}: {name} ({lat}, {lon})")
    print(f"  Provider: {provider_name}")
    sensor_names = ", ".join(sensor["name"] for sensor in sensors.values())
    print(f"  Sensors: {sensor_names}")
    return station


def load_stations(api_base, location_ids):
    stations = []
    for location_id in location_ids:
        try:
            stations.append(build_station(api_base, location_id))
        except Exception as exc:
            print(f"ERROR loading location {location_id}: {exc}")
    return stations


def get_latest_measurements(api_base, station):
    location_id = station["location_id"]
    data = api_get(api_base, f"/locations/{location_id}/latest")
    results = data.get("results", [])

    values = {}
    for item in results:
        sensor_id = item.get("sensorsId")
        value = item.get("value")
        sensor = station["sensors"].get(sensor_id)
        if not sensor:
            continue
        parameter = sensor["name"]
        values[parameter] = {
            "value": value,
            "units": sensor.get("units"),
        }

    print(f"{station['name']} measurements: {values}")
    return values


def sanitize_for_xml(text):
    """Remove/replace Unicode characters that break XML parsing"""
    if not text:
        return text
    replacements = {
        'µ': 'u',
        '³': '3',
        '²': '2',
        '°': 'deg',
        '→': '->',
        '←': '<-',
    }
    result = text
    for char, replacement in replacements.items():
        result = result.replace(char, replacement)
    return result


def calculate_aqi(pollutant_name, value, units):
    """
    Calculate AQI based on China's standard for a given pollutant.
    Returns AQI value (0-600+) or None if calculation fails.
    """
    pollutant_name = pollutant_name.lower().replace(" ", "")
    
    if pollutant_name not in POLLUTANT_AQI_PARAMS:
        return None
    
    params = POLLUTANT_AQI_PARAMS[pollutant_name]
    
    # Check if units match expected
    if units and not any(expected in units for expected in params["units"]):
        print(f"  Warning: {pollutant_name} units {units} don't match expected {params['units']}")
    
    breakpoints = params["breakpoints"]
    aqi_breakpoints = params["aqi_breakpoints"]
    
    # Find which bracket the value falls into
    for i, bp in enumerate(breakpoints):
        if value <= bp:
            # Calculate AQI using linear interpolation
            if i == 0:
                # Below first breakpoint
                aqi = (aqi_breakpoints[i] / breakpoints[i]) * value
            else:
                # Between breakpoints
                x1, x2 = breakpoints[i - 1], breakpoints[i]
                y1, y2 = aqi_breakpoints[i - 1], aqi_breakpoints[i]
                aqi = y1 + (value - x1) * (y2 - y1) / (x2 - x1)
            return int(round(aqi))
    
    # Above highest breakpoint
    return aqi_breakpoints[-1]


def get_highest_aqi(data):
    """
    Calculate AQI for all pollutants and return the highest AQI value.
    Also returns the pollutant that caused it.
    """
    max_aqi = 0
    max_pollutant = None
    
    for parameter, measurement in data.items():
        value = measurement.get("value")
        units = measurement.get("units")
        
        if value is None:
            continue
        
        aqi = calculate_aqi(parameter, value, units)
        if aqi is not None and aqi > max_aqi:
            max_aqi = aqi
            max_pollutant = parameter
    
    return max_aqi, max_pollutant


def get_aqi_color(aqi_value):
    """
    Map AQI value to color name using China's 4-color simplified standard.
    Returns color name and color info dict.
    """
    for color_name, color_info in AQI_COLORS.items():
        min_val, max_val = color_info["range"]
        if min_val <= aqi_value <= max_val:
            return color_name, color_info
    
    # If somehow higher than highest, return red
    return "red", AQI_COLORS["red"]


def make_cot(config, station, data):
    now = datetime.now(timezone.utc)
    stale_minutes = config.get_int("stale_minutes", 10)
    stale_time = now + timedelta(minutes=stale_minutes)

    time_string = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    stale_string = stale_time.strftime("%Y-%m-%dT%H:%M:%SZ")

    parameter_names = {
        "pm1": "PM1",
        "pm25": "PM2.5",
        "pm10": "PM10",
        "relativehumidity": "Humidity",
        "temperature": "Temperature",
        "um003": "UM003",
        "um005": "UM005",
        "um010": "UM010",
        "pm03_count": "PM0.3 Count",
        "o3": "O3",
        "no2": "NO2",
        "so2": "SO2",
        "co": "CO",
    }

    remarks_lines = []
    
    # Calculate AQI if enabled
    enable_aqi_colors = config.get_bool("enable_aqi_colors", True)
    aqi_value = 0
    highest_pollutant = None
    cot_type = config.get("cot_type", "a-h-G-E-S")
    
    if enable_aqi_colors:
        aqi_value, highest_pollutant = get_highest_aqi(data)
        color_name, color_info = get_aqi_color(aqi_value)
        
        remarks_lines.append(f"AQI: {aqi_value} ({color_info['label']})")
        remarks_lines.append(f"Health: {color_info['description']}")
        if highest_pollutant:
            remarks_lines.append(f"Highest: {highest_pollutant.upper()}")
        remarks_lines.append("")
        
        # Use hostile type for all to ensure consistent color handling
        cot_type = "a-h-G-E-S"
    
    # Add measurement details
    for parameter, measurement in data.items():
        value = measurement.get("value")
        units = measurement.get("units")
        display_name = parameter_names.get(parameter, parameter.replace("_", " ").upper())
        
        if units:
            units = sanitize_for_xml(units)
            remarks_lines.append(f"{display_name}: {value} {units}")
        else:
            remarks_lines.append(f"{display_name}: {value}")

    remarks = (
        "\n".join(remarks_lines)
        + "\n"
        + f"Source: {station['provider']}\n"
        + f"Location: {station['name']}"
    )

    uid = f"openaq.{station['location_id']}"
    callsign_prefix = config.get("callsign_prefix", "AQ")
    callsign = f"{callsign_prefix} {station['name']}"

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
        self.api_base = self.config.get("openaq_api", "https://api.openaq.org/v3")
        self.lock = threading.Lock()
        self.ready = False
        self.last_aqi = {}  # Track last AQI for each station to detect threshold crossings

    def reload(self):
        try:
            with self.lock:
                config = Config()
                api_base = config.get("openaq_api", "https://api.openaq.org/v3")

                if self.db is not None:
                    self.db.close()

                db_path = config.get_database_path()
                self.db = StationDB(db_path)
                print(f"Using database: {db_path}")

                location_ids = config.get_locations()
                stations = load_stations(api_base, location_ids)

                for station in stations:
                    self.db.save_station(station)

                self.config = config
                self.stations = stations
                self.api_base = api_base
                self.ready = True
                print(f"Loaded {len(stations)} of {len(location_ids)} configured locations")
                print(f"Configuration reload complete")
                print(f"AQI colors: {'enabled' if config.get_bool('enable_aqi_colors', True) else 'disabled'}")
                print(f"CoT type: {config.get('cot_type', 'a-h-G-E-S')}")
        except Exception as exc:
            print(f"FATAL: reload() failed: {exc}")
            self.ready = False

    def get_config(self):
        with self.lock:
            return self.config

    def get_stations(self):
        with self.lock:
            return list(self.stations) if self.ready else []

    def get_db(self):
        with self.lock:
            return self.db

    def is_ready(self):
        with self.lock:
            return self.ready

    def check_reload(self):
        if self.config.has_changed():
            print("Configuration changed; reloading...")
            self.reload()

    def send_all(self, conn):
        config = self.get_config()
        stations = self.get_stations()
        db = self.get_db()

        if not stations:
            print("WARNING: no stations to send")
            return

        for station in stations:
            try:
                data = get_latest_measurements(self.api_base, station)
                for parameter, measurement in data.items():
                    db.save_measurement(
                        station["location_id"],
                        parameter,
                        measurement.get("value"),
                        measurement.get("units"),
                    )

                cot = make_cot(config, station, data)
                conn.sendall(cot.encode("utf-8"))
                print(f"CoT sent: openaq.{station['location_id']}")

            except Exception as exc:
                print(f"ERROR updating station {station['location_id']}: {exc}")
                raise


def main():
    manager = StationManager()

    # Load config in background thread
    print("[DEBUG] Starting background reload thread...")
    def initial_load():
        print("[DEBUG] Initial reload starting...")
        manager.reload()
    
    load_thread = threading.Thread(target=initial_load, daemon=False)
    load_thread.start()

    # Start config watchdog
    def config_watchdog():
        reload_interval = manager.get_config().get_int("config_reload", DEFAULT_CONFIG_RELOAD_SECONDS)
        while True:
            time.sleep(reload_interval)
            try:
                manager.check_reload()
            except Exception as exc:
                print(f"ERROR during config reload check: {exc}")

    threading.Thread(target=config_watchdog, daemon=True).start()

    host = manager.get_config().get("cot_host", "0.0.0.0")
    port = manager.get_config().get_int("cot_port", 9000)

    print(f"Listening on {host}:{port}")
    print("AQTAK v2 ready (loading stations in background)")

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((host, port))
        server.listen(5)

        while True:
            try:
                conn, addr = server.accept()
                print(f"ATAK connection from {addr}")
                with conn:
                    if not manager.is_ready():
                        print("Waiting for manager to load...")
                        for _ in range(30):
                            if manager.is_ready():
                                break
                            time.sleep(1)
                        if not manager.is_ready():
                            print("ERROR: Manager failed to load within 30 seconds")
                            continue

                    # Send immediately on connection
                    try:
                        manager.send_all(conn)
                    except (BrokenPipeError, ConnectionResetError):
                        print("ATAK disconnected after initial send")
                        continue
                    except Exception as exc:
                        print(f"Error on initial send: {exc}")
                        continue

                    # Then continue polling
                    while True:
                        try:
                            poll_interval = manager.get_config().get_int("poll_interval", 10)
                            time.sleep(poll_interval)
                            manager.send_all(conn)
                        except (BrokenPipeError, ConnectionResetError):
                            print("ATAK disconnected")
                            break
                        except Exception as exc:
                            print(f"Error: {exc}")
                            break
            except KeyboardInterrupt:
                print("Shutting down")
                break


if __name__ == "__main__":
    main()
