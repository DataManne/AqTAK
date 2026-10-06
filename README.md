# AqTAK

OpenAQ air quality readings as Cursor-on-Target markers for ATAK, relayed through a private [Taky](https://github.com/tkuester/taky) server. Containerised for Podman (Docker should also work).

```
OpenAQ API -> AQTAK (client) -> aqtak-taky (port 8087) -> ATAK devices
```

AQTAK connects **out** to Taky as a normal CoT client, the same way ATAK does. The Taky container is part of this repo and is only meant for AQ reports.

## Install

Requirements: Podman (or Docker), git, an [OpenAQ API key](https://explore.openaq.org/), and the OpenAQ location IDs you want.

```bash
mkdir -p ~/pods && cd ~/pods
git clone https://github.com/DataManne/AqTAK.git aqtak
cd aqtak

export OPENAQ_API_KEY="your_key"
bash install.sh --locations 12345,67890
```

The clone can live anywhere. `install.sh` keeps everything relative to itself:

| Path | Purpose |
|------|---------|
| `data/aqtak.conf` | AQTAK config (mounted at `/data` in the container) |
| `data/*.db` | SQLite history |
| `taky/config/taky.conf` | Taky config |

It builds two images (`localhost/aqtak`, `localhost/aqtak-taky`), creates a `aqtak-net` network and starts two containers: `aqtak-taky` (publishes TCP 8087) and `aqtak`.

In ATAK add a TCP server: this host's IP, port 8087, no SSL.

### Reinstall from scratch

```bash
bash install.sh --clean --locations 12345,67890
```

This removes both containers, both images, the network, `data/` and `taky/config/`.

### Options

`--api-key`, `--locations`, `--poll-interval` (min 5), `--stale-minutes`, `--callsign-prefix`, `--taky-port`, `--runtime podman|docker`, `--clean`.

## Finding location IDs

Browse [explore.openaq.org](https://explore.openaq.org/) or query the API:

```bash
curl -s -H "X-API-Key: $OPENAQ_API_KEY" \
  "https://api.openaq.org/v3/locations?coordinates=LAT,LON&radius=10000&limit=20"
```

## Configuration (`data/aqtak.conf`)

Changes are picked up automatically (`config_reload` seconds).

| Key | Default | Meaning |
|-----|---------|---------|
| `locations` | none | Comma-separated OpenAQ location IDs |
| `cot_host` / `cot_port` | `aqtak-taky` / `8087` | Where AQTAK connects |
| `enable_aqi_colors` | `true` | Colour markers by AQI (see below) |
| `cot_type` | `a-f-G-E-S` | CoT type used when colours are off, or no AQI pollutant is reported |
| `callsign_prefix` | `AQ` | Callsign is `<prefix> <station name>` |
| `poll_interval` | `10` | Seconds between updates |
| `stale_minutes` | `10` | Marker lifetime in ATAK |
| `database` | `/data/aqtak-{last_edit}.db` | SQLite path |
| `config_reload` | `5` | Seconds between config checks |

## AQI colours

ATAK colours a marker by the affiliation in its CoT type. With `enable_aqi_colors=true` AQTAK calculates an AQI per pollutant, takes the **highest**, and picks the affiliation:

| Colour | CoT type | AQI | Meaning |
|--------|----------|-----|---------|
| Blue | `a-f-G-E-S` | 0-50 | Excellent |
| Green | `a-n-G-E-S` | 51-100 | Good |
| Yellow | `a-u-G-E-S` | 101-150 | Lightly polluted |
| Red | `a-h-G-E-S` | 151+ | Moderately polluted or worse |

This is the Chinese standard (HJ 633-2012) with its upper bands merged into red. For PM2.5 the band edges are 35, 75 and 115 ug/m3. AQI is calculated for PM2.5, PM10, O3, NO2, SO2 and CO, with ppm/ppb converted automatically. Other readings (temperature, humidity, particle counts) are shown in the remarks but do not affect colour.

The colour changes only when the AQI crosses a band edge. Markers are still re-sent every poll so they do not go stale.

The remarks show the AQI, the pollutant driving it, advice and all raw readings.

**Masks:** particulates (PM2.5, PM10) can be filtered with an N95/FFP2 mask. Gases (O3, NO2, SO2, CO) are not stopped by ordinary masks. The remarks say which applies when AQI is above 50.

Simplifications: PM, SO2, NO2 and CO use the 24-hour tables and O3 the 8-hour table, applied to the latest reading rather than a rolling average, so values are indicative only. This is not an official or safety-critical AQI.

## Troubleshooting

```bash
podman logs aqtak
podman logs aqtak-taky
```

- `Connection failed: [Errno 111]`: Taky is not running or `cot_host`/`cot_port` is wrong. Check `podman ps`. AQTAK retries every 5 seconds.
- `Connected to Taky` but nothing in ATAK: check ATAK points at this host's port 8087 and the host firewall allows it.
- `ERROR loading location`: wrong location ID or API key. AQTAK retries every 30 seconds.
- `Skipping <pollutant>: unsupported units`: that reading is left out of the AQI.
- All markers one colour: check the log says `AQI colours: enabled`.

## Licence

AGPL-3.0
