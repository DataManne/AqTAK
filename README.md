# AQTAK – OpenAQ Air Quality into TAK

AQTAK bridges OpenAQ air quality data into the TAK (Team Awareness Kit) ecosystem. It fetches sensor data from OpenAQ and broadcasts it as Cursor-on-Target (CoT) messages via Taky, making air quality information visible to ATAK clients.

**Architecture:**
- **AQTAK** (Python container): Polls OpenAQ API, generates CoT messages, connects to Taky
- **Taky** (TAK CoT relay): Receives CoT from AQTAK, broadcasts to ATAK clients
- **ATAK** (existing): Receives air quality markers and updates in real-time

---

## Quick Start (Automated)

### Prerequisites
- Docker or Podman installed
- OpenAQ API key ([get one free](https://openaq.org/))
- Taky already running (or use the included setup)
- ATAK running on network reachable from container host

### One-Command Setup

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/PrinoBotsCatto/aqtak/main/install.sh) \
  --api-key YOUR_OPENAQ_API_KEY \
  --taky-host 192.168.1.100 \
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
cot_type=a-f-G-E-S
callsign_prefix=AQ

# Polling
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
git clone https://github.com/PrinoBotsCatto/aqtak.git .

# Build image
podman build -t localhost/aqtak:latest .
```

### 7. Run AQTAK

```bash
podman run -d \
  --name aqtak \
  --restart=unless-stopped \
  -e OPENAQ_API_KEY="your_api_key_here" \
  -v ~/aqtak/config/aqtak.conf:/app/config/aqtak.conf:ro \
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
CoT type: a-f-G-E-S
Connecting to Taky at 192.168.1.100:8087
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
3. Markers update every `poll_interval` seconds with new air quality data

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
--cot-type TYPE            CoT event type (default: a-f-G-E-S)
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
cot_type=a-f-G-E-S
callsign_prefix=AQ
```
- `cot_type`: CoT event type (see [CoT Spec](https://www.mitre.org/sites/default/files/pdf/09_3937.pdf))
  - `a-f-G-E-S`: Air-Friendly-Ground-Equipment-Sensors (recommended for environmental data)
- `callsign_prefix`: Prefix added to all markers

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
AQTAK (Polls, generates CoT)
    ↓
Taky (Relays CoT to clients)
    ↓
ATAK (Displays markers)
```

### CoT Message Format

AQTAK generates CoT events for each location with:
- **UID**: Unique identifier (based on location ID)
- **Callsign**: `{prefix}-{location_name}`
- **Coordinates**: Latitude/Longitude from OpenAQ
- **Details**: PM2.5, PM10, O3, NO2, CO, SO2 (if available)
- **Time**: Current timestamp (updates on each poll)

Example callsign: `AQ-Bangkok-Metro`, `AQ-Industrial-Site-East`

### CoT Event Type Breakdown

- `a`: Atom (CoT event)
- `f`: Friendly (known contact)
- `G`: Ground (surface-based)
- `E`: Equipment (device/sensor)
- `S`: Sensors (monitoring equipment)

---

## Development & Contributing

### Project Structure
```
aqtak/
├── README.md           # This file
├── Dockerfile          # Container image definition
├── aq_to_atak.py       # Main application
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

### Modifying CoT Generation

Edit `aq_to_atak.py`:
- `generate_cot()` function: CoT XML generation
- `manager.send_all()`: Message serialization
- Change `cot_type` in config to modify event classification

---

## License

AGPL-3.0 – See LICENSE file

---

## Support & Issues

For bugs, feature requests, or questions:
- GitHub Issues: [PrinoBotsCatto/aqtak/issues](https://github.com/PrinoBotsCatto/aqtak/issues)
- Include logs: `podman logs aqtak`
- Include config (redact API key)

---

## Related Links

- [OpenAQ Documentation](https://docs.openaq.org/)
- [TAK Server/Taky](https://github.com/pwarren/taky)
- [ATAK (Android Tactical Assault Kit)](https://www.civtak.org/)
- [Cursor-on-Target (CoT) Specification](https://www.mitre.org/sites/default/files/pdf/09_3937.pdf)
