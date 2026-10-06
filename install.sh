#!/usr/bin/env bash
# AQTAK installer. Run from inside a clone of the repo; everything lives
# relative to this script, so the clone can be anywhere (e.g. ~/pods/aqtak).
#
#   export OPENAQ_API_KEY=sk_live_...
#   bash install.sh --locations LOCID1,LOCID2
#   bash install.sh --clean --locations LOCID1   # wipe and rebuild everything
#
# To find location IDs:
#   curl -s -H "X-API-Key: $OPENAQ_API_KEY" \
#     "https://api.openaq.org/v3/locations?coordinates=LAT,LON&radius=10000&limit=20" \
#     | jq '.results[] | {id, name, city, country}'

set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME="${CONTAINER_RUNTIME:-podman}"
LOCATIONS=""
POLL_INTERVAL="10"
STALE_MINUTES="10"
CALLSIGN_PREFIX="AQ"
TAKY_PORT="8087"
CLEAN=0
API_KEY="${OPENAQ_API_KEY:-}"

usage() {
    cat <<'EOF'
AQTAK installer

Usage: bash install.sh [options]

Options:
  --api-key KEY                OpenAQ API key (or set OPENAQ_API_KEY env var)
  --locations LOC1,LOC2        OpenAQ location IDs to monitor (comma-separated, required on first install)
  --poll-interval SECS         Update frequency in seconds (default 10, minimum 5)
  --stale-minutes MINS         Minutes before markers expire in ATAK (default 10)
  --callsign-prefix PREFIX     Marker callsign prefix (default "AQ")
  --taky-port PORT             TCP port published on this host for ATAK (default 8087)
  --runtime RUNTIME            Container runtime: podman or docker (default: podman or $CONTAINER_RUNTIME)
  --clean                      Remove containers, images, network and data first (full rebuild)
  --help                       Show this message

Examples:
  bash install.sh --locations 12345,67890
  bash install.sh --clean --locations 12345 --taky-port 9000
  OPENAQ_API_KEY=sk_live_... bash install.sh --locations 12345,67890
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --api-key) API_KEY="$2"; shift 2 ;;
        --locations) LOCATIONS="$2"; shift 2 ;;
        --poll-interval) POLL_INTERVAL="$2"; shift 2 ;;
        --stale-minutes) STALE_MINUTES="$2"; shift 2 ;;
        --callsign-prefix) CALLSIGN_PREFIX="$2"; shift 2 ;;
        --taky-port) TAKY_PORT="$2"; shift 2 ;;
        --runtime) RUNTIME="$2"; shift 2 ;;
        --clean) CLEAN=1; shift ;;
        --help) usage; exit 0 ;;
        *) echo "ERROR: unknown option '$1'"; usage; exit 1 ;;
    esac
done

# Validation
command -v "$RUNTIME" >/dev/null 2>&1 || {
    echo "ERROR: container runtime '$RUNTIME' not found"
    exit 1
}

[[ -n "$API_KEY" ]] || {
    echo "ERROR: OpenAQ API key required"
    echo "  Set OPENAQ_API_KEY environment variable or use --api-key"
    exit 1
}

[[ "$POLL_INTERVAL" =~ ^[0-9]+$ ]] || {
    echo "ERROR: --poll-interval must be a number"
    exit 1
}

if [[ "$POLL_INTERVAL" -lt 5 ]]; then
    echo "WARN: poll_interval less than 5 seconds; using 5 (API rate limit)"
    POLL_INTERVAL=5
fi

[[ -z "$LOCATIONS" && ! -f "$APP_DIR/data/aqtak.conf" ]] && {
    echo "ERROR: --locations required on first install"
    echo "  See README.md for how to find location IDs"
    exit 1
}

# Paths relative to this script
DATA_DIR="$APP_DIR/data"
TAKY_CONFIG_DIR="$APP_DIR/taky/config"
AQTAK_CONF="$DATA_DIR/aqtak.conf"
NETWORK_NAME="aqtak-net"

# Clean
if [[ $CLEAN -eq 1 ]]; then
    echo "==> Removing previous installation"
    "$RUNTIME" rm -f aqtak aqtak-taky >/dev/null 2>&1 || true
    "$RUNTIME" rmi -f localhost/aqtak:latest localhost/aqtak-taky:latest >/dev/null 2>&1 || true
    "$RUNTIME" network rm "$NETWORK_NAME" >/dev/null 2>&1 || true
    rm -rf "$DATA_DIR" "$TAKY_CONFIG_DIR"
    echo "    Removed containers, images, network and data"
fi

# Create directories
mkdir -p "$DATA_DIR" "$TAKY_CONFIG_DIR"

# Taky config (always regenerated to ensure consistency)
cat > "$TAKY_CONFIG_DIR/taky.conf" <<'TAKY_EOF'
[taky]
hostname = aqtak-taky
node_id = AQTAK-RELAY
bind_ip = 0.0.0.0

[cot_server]
port = 8087

[dp_server]
upload_path = /var/taky/dp-user

[ssl]
enabled = false
TAKY_EOF

echo "==> Taky config: $TAKY_CONFIG_DIR/taky.conf"

# AQTAK config (only write if locations provided or no config exists)
if [[ -n "$LOCATIONS" || ! -f "$AQTAK_CONF" ]]; then
    cat > "$AQTAK_CONF" <<AQTAK_EOF
# AQTAK Configuration
# Edit and save; changes reload automatically (~5 seconds)

# OpenAQ location IDs to monitor (comma-separated)
locations=$LOCATIONS

# OpenAQ API endpoint (rarely needs to change)
openaq_api=https://api.openaq.org/v3

# Taky connection (aqtak-taky is the container hostname)
cot_host=aqtak-taky
cot_port=8087

# Marker callsign prefix (final callsign: PREFIX + space + location name)
callsign_prefix=$CALLSIGN_PREFIX

# Colour markers by AQI (blue/green/yellow/red) using China HJ 633-2012 standard
# When true: affiliation in CoT type changes based on air quality level
# When false: use cot_type_fallback for all markers
enable_aqi_colors=true

# CoT type used only when enable_aqi_colors=false or no AQI pollutant is reported
# (when enabled, AQI determines the affiliation: a-f/a-n/a-u/a-h for blue/green/yellow/red)
cot_type_fallback=a-f-G-E-S

# Update frequency in seconds (must be >= 5 to respect API rate limits)
poll_interval=$POLL_INTERVAL

# Minutes before markers disappear from ATAK if not updated
stale_minutes=$STALE_MINUTES

# Advanced settings
config_reload=5
database=/data/aqtak-{last_edit}.db
log_level=INFO
AQTAK_EOF
    echo "==> AQTAK config: $AQTAK_CONF"
fi

# Build images
echo "==> Building container images"
"$RUNTIME" build -t localhost/aqtak:latest -f "$APP_DIR/Containerfile" "$APP_DIR" >/dev/null 2>&1
echo "    Built localhost/aqtak:latest"

"$RUNTIME" build -t localhost/aqtak-taky:latest -f "$APP_DIR/taky/Containerfile" "$APP_DIR/taky" >/dev/null 2>&1
echo "    Built localhost/aqtak-taky:latest"

# Network
"$RUNTIME" network inspect "$NETWORK_NAME" >/dev/null 2>&1 || {
    "$RUNTIME" network create "$NETWORK_NAME" >/dev/null 2>&1
    echo "==> Created network: $NETWORK_NAME"
}

# Start containers
echo "==> Starting containers"
"$RUNTIME" rm -f aqtak aqtak-taky >/dev/null 2>&1 || true

"$RUNTIME" run -d \
    --name aqtak-taky \
    --restart=unless-stopped \
    --network "$NETWORK_NAME" \
    -p "$TAKY_PORT:8087/tcp" \
    -v "$TAKY_CONFIG_DIR/taky.conf:/etc/taky/taky.conf:ro" \
    localhost/aqtak-taky:latest >/dev/null 2>&1
echo "    Started aqtak-taky (TCP $TAKY_PORT)"

export OPENAQ_API_KEY="$API_KEY"
"$RUNTIME" run -d \
    --name aqtak \
    --restart=unless-stopped \
    --network "$NETWORK_NAME" \
    -e OPENAQ_API_KEY \
    -v "$DATA_DIR:/data" \
    localhost/aqtak:latest >/dev/null 2>&1
echo "    Started aqtak"

# Wait and show logs
echo ""
echo "==> Waiting for startup (5 seconds)"
sleep 5

echo ""
echo "==> Taky status:"
"$RUNTIME" logs aqtak-taky 2>&1 | tail -3

echo ""
echo "==> AQTAK status:"
"$RUNTIME" logs aqtak 2>&1 | tail -10

cat <<EOF

=== Installation Complete ===

Installation directory:
  $APP_DIR

Configuration:
  $AQTAK_CONF

Data & history:
  $APP_DIR/data/

Container info:
  Image: localhost/aqtak:latest
  Container: aqtak
  Network: $NETWORK_NAME
  Published port: $TAKY_PORT (TCP)

Logs:
  Follow AQTAK:  $RUNTIME logs -f aqtak
  Follow Taky:   $RUNTIME logs -f aqtak-taky
  Both:          $RUNTIME logs -f aqtak aqtak-taky

ATAK configuration:
  1. In ATAK, add a server connection
  2. Type: TCP
  3. Host: $(hostname -I | awk '{print $1}') or localhost
  4. Port: $TAKY_PORT
  5. No SSL
  6. Save

Troubleshooting:
  - Check logs for "Connected to Taky" or error messages
  - Verify location IDs are valid
  - Check firewall allows TCP $TAKY_PORT
  - Markers should appear in ATAK within 30 seconds
  - Edit $AQTAK_CONF and save to reload config (~5 seconds)

Documentation:
  See README.md for detailed info on AQI colours, finding location IDs, and more
EOF
