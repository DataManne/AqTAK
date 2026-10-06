# AQTAK – OpenAQ Air Quality into TAK

AQTAK bridges OpenAQ air quality data into the TAK (Team Awareness Kit) ecosystem. It fetches sensor data from OpenAQ and broadcasts it as Cursor-on-Target (CoT) messages via Taky, making air quality information visible to ATAK clients.

**Key Features:**
- Real-time air quality data from OpenAQ
- **AQI-based color coding** (China 4-color standard: Blue → Green → Yellow → Red)
- Automatic health warnings and descriptions
- Highest pollutant detection (PM2.5, PM10, O3, NO2, SO2, CO)
- Hot-reload configuration
- SQLite persistence

**Architecture:**
- **AQTAK** (Python container): Polls OpenAQ API, generates CoT messages, connects to Taky
- **Taky** (TAK CoT relay): Receives CoT from AQTAK, broadcasts to ATAK clients
- **ATAK** (existing): Receives air quality markers with color-coded health status

---

## Quick Start (Automated)

### Prerequisites
- Docker or Podman installed
- OpenAQ API key ([get one free](https://openaq.org/))
- Taky already running (or use the included setup)
- ATAK running on network reachable from container host

### One-Command Setup

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/DataManne/AqTAK/main/install.sh) \
  --api-key YOUR_OPENAQ_API_KEY \
  --locations LOC_ID_1,LOC_ID_2
```

See [Installation Script](#installation-script) below for all options.

---

## Step-by-Step Installation

### 1. Create Project Directory

```bash
mkdir -p ~/aqtak/data ~/aqtak/config
cd ~/aqtak
```

### 2. Get OpenAQ API Key

1. Visit https://openaq.org/
2. Sign up and generate an API key
3. Save it securely (you'll need it in step 5)

### 3. Find Your Sensor Location IDs

Use OpenAQ's API to find location IDs for your sensors:

```bash
# Search by city
curl -s "https://api.openaq.org/v3/locations?limit=100&city=YOUR_CITY" | jq '.results[] | {id, name, city, country}'

# Or search globally
curl -s "https://api.openaq.org/v3/locations?limit=10" | jq '.results[] | {id, name, city, country}'
```

Make note of the location `id` values you want to monitor.

### 4. Configure AQTAK

Create `~/aqtak/config/aqtak.conf`:

```ini
# OpenAQ Configuration
locations=12345,67890
openaq_api=https://api.openaq.org/v3

# TAK/Taky Connection
cot_host=192.168.1.100
cot_port=8087

# CoT Settings
cot_type=a-h-G-E-S
callsign_prefix=AQ

# Air Quality Index (AQI) Color Coding
# Uses China's 4-color standard: Blue (0-35) → Green (36-75) → Yellow (76-150) → Red (151+)
# Colors are rendered as marker affiliation in ATAK (blue=friendly, red=hostile)
# Enable/disable AQI colors (true/false)
enable_aqi_colors=true

# Polling & Data Lifecycle
poll_interval=10
stale_minutes=10

# Advanced
config_reload=5
database=/data/aqtak-{last_edit}.db
log_level=INFO
```

**Key fields:**
- `locations`: Comma-separated OpenAQ location IDs
- `cot_host`: IP or hostname of machine running Taky
- `cot_port`: Taky's CoT server port (default 8087)
- `enable_aqi_colors`: Toggle AQI color coding on/off (default: true)
- `poll_interval`: Seconds between API polls (min 5)
- `stale_minutes`: How long before a marker disappears from ATAK if not updated
- `callsign_prefix`: Prefix for air quality markers (e.g., "AQ-LOCATION")

### 5. Set Up Taky (if not already running)

**Option A: Using Podman/Docker (Recommended)**

```bash
mkdir -p ~/taky/config ~/taky/data

cat > ~/taky/config/taky.conf << 'EOF'
[taky]
hostname = taky.local
node_id = TAKY
bind_ip = 0.0.0.0

[cot_server]
port = 8087

[dp_server]
upload_path = /var/taky/dp-user

[ssl]
enabled = False
EOF

podman run -d \
  --name taky \
  --restart=unless-stopped \
  -p 8087:8087/tcp \
  -v ~/taky/config/taky.conf:/etc/taky/taky.conf:ro \
  localhost/taky:latest
```

**Option B: Using Docker**

```bash
docker run -d \
  --name taky \
  --restart=unless-stopped \
  -p 8087:8087/tcp \
  -v ~/taky/config/taky.conf:/etc/taky/taky.conf:ro \
  taky:latest
```

Verify Taky is running:
```bash
podman logs taky | grep "Listening for tcp"
# Should show: INFO:COTServer:Listening for tcp on 0.0.0.0:8087
```

### 6. Build AQTAK Container

```bash
cd ~/aqtak

# Clone or download the repo
git clone https://github.com/DataManne/AqTAK.git .

# Build image
podman build -t localhost/aqtak:latest .
```

### 7. Run AQTAK

```bash
podman run -d \
  --name aqtak \
  --restart=unless-stopped \
  -e OPENAQ_API_KEY="your_api_key_here" \
  -v ~/aqtak/config/aqtak.conf:/data/aqtak.conf:ro \
  -v ~/aqtak/data:/data \
  localhost/aqtak:latest
```

### 8. Verify Connection

Check logs:
```bash
podman logs aqtak
```

Should show:
```
Configuration reload complete
AQI colors: enabled
CoT type: a-h-G-E-S
Listening on 0.0.0.0:9000
AQTAK v2 ready (loading stations in background)
Connected to Taky at 192.168.1.100:8087
Loaded 2 of 2 configured locations
```

Check Taky logs:
```bash
podman logs taky | tail -5
```

Should show new TCP CoT client connections.

### 9. Verify in ATAK

In ATAK:
1. Add a server connection to Taky (if not already configured)
2. You should see new markers appear for each location with the callsign prefix (e.g., `AQ-Location1`, `AQ-Location2`)
3. Markers will have colors based on air quality:
   - **Blue**: Excellent (AQI 0-35)
   - **Green**: Good (AQI 36-75)
   - **Yellow**: Lightly Polluted (AQI 76-150)
   - **Red**: Heavily Polluted (AQI 151+)
4. Markers update every `poll_interval` seconds with new air quality data

---

## Installation Script

An automated installation script is provided for quick setup.

### Usage

```bash
bash install.sh [OPTIONS]
```

### Options

```
--api-key KEY              OpenAQ API key (required)
--locations ID1,ID2        Comma-separated location IDs (required)
--taky-host HOST           Taky server IP or hostname (default: 192.168.1.100)
--taky-port PORT           Taky CoT server port (default: 8087)
--poll-interval SECS       Seconds between API polls (default: 10)
--stale-minutes MINS       Marker stale timeout (default: 10)
--callsign-prefix PREFIX   CoT marker prefix (default: AQ)
--cot-type TYPE            CoT event type (default: a-h-G-E-S)
--work-dir DIR             Working directory (default: ~/aqtak)
--runtime RUNTIME          Container runtime: podman or docker (default: podman)
--help                     Show this message
```

### Example

```bash
bash install.sh \
  --api-key sk_live_xxxxxxxxxxxx \
  --locations 12345,67890,11111 \
  --taky-host 192.168.1.50 \
  --work-dir /opt/aqtak \
  --runtime podman
```

---

## Configuration Reference

### aqtak.conf Sections

#### OpenAQ API
```ini
locations=12345,67890,11111
openaq_api=https://api.openaq.org/v3
```
- `locations`: OpenAQ location IDs (comma-separated, no spaces)
- `openaq_api`: OpenAQ API endpoint (usually unchanged)

#### TAK/Taky Connection
```ini
cot_host=192.168.1.100
cot_port=8087
```
- `cot_host`: IP/hostname of Taky server
- `cot_port`: Taky's CoT server port

#### CoT Marker Settings
```ini
cot_type=a-h-G-E-S
callsign_prefix=AQ
```
- `cot_type`: CoT event type
  - `a-h-G-E-S`: Hostile/Hazard (all markers use this for consistent color rendering)
  - Color in ATAK is controlled by AQI, not affiliation code
- `callsign_prefix`: Prefix added to all markers

#### AQI Color Coding
```ini
enable_aqi_colors=true
```
- `enable_aqi_colors`: Toggle color coding on/off (true/false)
  - **true**: Markers colored by AQI (Blue → Green → Yellow → Red)
  - **false**: All markers use default color

**AQI Thresholds (China Standard):**

| Color | Range | Label | Health Advisory |
|-------|-------|-------|-----------------|
| 🔵 Blue | 0-35 | Excellent | Air quality is good; suitable for all outdoor activities |
| 🟢 Green | 36-75 | Good | Air quality is acceptable; most people can engage in outdoor activities |
| 🟡 Yellow | 76-150 | Lightly Polluted | Sensitive groups should limit prolonged outdoor exposure |
| 🔴 Red | 151+ | Heavily Polluted | Public should limit outdoor; masks with PM2.5 filter recommended |

**Pollutants Considered for AQI:**
- PM2.5 (fine particles) – **maskable** with N95/PM2.5 filter
- PM10 (coarse particles) – **maskable** with PM10/N95 filter
- O3 (ozone) – **not maskable** (penetrates masks)
- NO2 (nitrogen dioxide) – **partially maskable**
- SO2 (sulfur dioxide) – **partially maskable**
- CO (carbon monoxide) – **not maskable** (colorless, odorless gas)

The **highest AQI** from all pollutants determines the marker color, showing the worst condition regardless of specific pollutant.

#### Polling & Data Lifecycle
```ini
poll_interval=10
stale_minutes=10
```
- `poll_interval`: Seconds between OpenAQ API polls (minimum 5)
- `stale_minutes`: Minutes before markers disappear from ATAK if not updated

#### Advanced
```ini
config_reload=5
database=/data/aqtak-{last_edit}.db
log_level=INFO
```
- `config_reload`: Seconds between config file checks (hot-reload)
- `database`: SQLite database path (persists station metadata)
- `log_level`: DEBUG, INFO, WARNING, ERROR

---

## Troubleshooting

### AQTAK can't connect to Taky

**Error:** `Connection refused`

**Check:**
1. Taky is running: `podman ps | grep taky`
2. Taky is listening on 8087: `podman logs taky | grep "Listening"`
3. `cot_host` in `aqtak.conf` matches Taky's IP/hostname
4. Firewall allows traffic on port 8087

**Fix:** Update `cot_host` in `aqtak.conf`:
```bash
# If running on same machine (using podman network)
cot_host=taky

# If running on different machine
cot_host=192.168.1.100  # Use Taky server's IP
```

### No markers appearing in ATAK

**Check:**
1. AQTAK connected: `podman logs aqtak | grep "Connected to Taky"`
2. Locations loaded: `podman logs aqtak | grep "Loaded"`
3. ATAK configured to receive from Taky (check ATAK server settings)
4. OpenAQ has data for your locations: 
   ```bash
   curl -s "https://api.openaq.org/v3/locations/12345" | jq .
   ```

### Markers all same color (not changing based on AQI)

**Check:**
1. AQI colors enabled: `podman logs aqtak | grep "AQI colors"`
2. Should show: `AQI colors: enabled`
3. Verify config has `enable_aqi_colors=true`

**Fix:** Restart container after changing config:
```bash
podman restart aqtak
```

### High memory usage

AQTAK uses SQLite to cache station metadata. If the database grows large:
```bash
# Restart to clear old cache
podman restart aqtak

# Or manually truncate
rm ~/aqtak/data/aqtak-*.db
```

### API rate limiting

OpenAQ has rate limits. If you see `429 Too Many Requests`:
- Increase `poll_interval` in config (e.g., 30 seconds instead of 10)
- Reduce number of `locations`
- Restart AQTAK: `podman restart aqtak`

---

## Architecture Details

### Data Flow

```
OpenAQ API
    ↓
AQTAK (Polls, calculates AQI, generates CoT)
    ↓
Taky (Relays CoT to clients)
    ↓
ATAK (Displays color-coded markers)
```

### CoT Message Format

AQTAK generates CoT events for each location with:
- **UID**: Unique identifier (based on location ID)
- **Callsign**: `{prefix}-{location_name}`
- **Coordinates**: Latitude/Longitude from OpenAQ
- **AQI & Health Status**: Current AQI value, health level, dominant pollutant
- **Pollutant Details**: Individual measurements for all available parameters
- **Time**: Current timestamp (updates on each poll)
- **Type**: `a-h-G-E-S` (Hostile/Equipment) for consistent ATAK rendering

**Example Remarks Section:**
```
AQI: 78 (Lightly Polluted)
Health: Sensitive groups should limit prolonged outdoor exposure
Highest: PM2.5

PM2.5: 42.5 ug/m³
PM10: 65 ug/m³
O3: 50 ug/m³
Temperature: 28 deg C
Humidity: 65%

Source: OpenAQ
Location: Metro Air Quality Station
```

### AQI Calculation

AQI is calculated using **China's 4-color standard** with linear interpolation:

1. **For each pollutant**, calculate individual AQI using breakpoints and measured value
2. **Select the highest AQI** across all pollutants
3. **Map AQI to color**:
   - 0-35: Blue
   - 36-75: Green
   - 76-150: Yellow
   - 151+: Red

Example: If PM2.5 AQI = 85 and PM10 AQI = 60, use 85 (Yellow marker in ATAK)

### CoT Event Type

- `a-h-G-E-S`: 
  - `a`: Atom (CoT event)
  - `h`: Hostile (hazard/equipment – used for consistent color handling)
  - `G`: Ground (surface-based)
  - `E`: Equipment (sensor/device)
  - `S`: Sensors (monitoring equipment)

All markers use this type regardless of AQI. Color coding is achieved through ATAK's marker rendering system.

---

## Development & Contributing

### Project Structure
```
aqtak/
��── README.md           # This file
├── Dockerfile          # Container image definition
├── aq_to_atak.py       # Main application with AQI engine
├── config/
│   └── aqtak.conf      # Configuration file
└── data/
    └── aqtak-*.db      # SQLite cache (auto-created)
```

### Running Locally (Without Container)

```bash
# Install dependencies
pip install requests

# Set API key
export OPENAQ_API_KEY="your_api_key"

# Run
python aq_to_atak.py
```

### Modifying AQI Calculation

Edit `aq_to_atak.py`, section `POLLUTANT_AQI_PARAMS`:

```python
POLLUTANT_AQI_PARAMS = {
    "pm25": {
        "breakpoints": [35, 75, 115, 150, 250, 500],
        "aqi_breakpoints": [50, 100, 150, 200, 300, 500],
        "units": ["µg/m³", "ug/m³", "μg/m³"],
    },
    # Add or modify pollutants here
}
```

### Adding New Pollutants

1. Define breakpoints in `POLLUTANT_AQI_PARAMS`
2. Add friendly name to `parameter_names` in `make_cot()`
3. Function `calculate_aqi()` automatically handles new pollutants

---

## License

AGPL-3.0 – See LICENSE file

---

## Support & Issues

For bugs, feature requests, or questions:
- GitHub Issues: [DataManne/AqTAK/issues](https://github.com/DataManne/AqTAK/issues)
- Include logs: `podman logs aqtak`
- Include config (redact API key)

---

## Related Links

- [OpenAQ Documentation](https://docs.openaq.org/)
- [TAK Server/Taky](https://github.com/pwarren/taky)
- [ATAK (Android Tactical Assault Kit)](https://www.civtak.org/)
- [Cursor-on-Target (CoT) Specification](https://www.mitre.org/sites/default/files/pdf/09_3937.pdf)
- [China Air Quality Index Standard](http://www.cnemc.cn/)
