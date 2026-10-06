#!/usr/bin/env bash
# AQTAK installer. Run from inside a clone of the repo; everything lives
# relative to this script, so the clone can be anywhere (e.g. ~/pods/aqtak).
#
#   export OPENAQ_API_KEY=...
#   bash install.sh --locations 12345,67890
#   bash install.sh --clean --locations 12345      # wipe and rebuild
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME="podman"
LOCATIONS=""
POLL_INTERVAL="10"
STALE_MINUTES="10"
CALLSIGN_PREFIX="AQ"
TAKY_PORT="8087"
CLEAN=0
API_KEY="${OPENAQ_API_KEY:-}"

usage() {
    cat <<'EOF'
Usage: bash install.sh [options]
  --api-key KEY          OpenAQ API key (or set OPENAQ_API_KEY)
  --locations ID,ID      OpenAQ location IDs (required on first install)
  --poll-interval SECS   default 10
  --stale-minutes MINS   default 10
  --callsign-prefix STR  default AQ
  --taky-port PORT       port published on the host for ATAK, default 8087
  --runtime podman|docker  default podman
  --clean                remove containers, images, network and data first
  --help
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
        *) echo "Unknown option: $1"; usage; exit 1 ;;
    esac
done

command -v "$RUNTIME" >/dev/null || { echo "$RUNTIME not found"; exit 1; }
[[ -n "$API_KEY" ]] || { echo "OpenAQ API key required (--api-key or OPENAQ_API_KEY)"; exit 1; }
[[ "$POLL_INTERVAL" =~ ^[0-9]+$ ]] || { echo "--poll-interval must be a number"; exit 1; }
[[ "$POLL_INTERVAL" -ge 5 ]] || POLL_INTERVAL=5

DATA_DIR="$APP_DIR/data"
TAKY_DIR="$APP_DIR/taky/config"
CONF="$DATA_DIR/aqtak.conf"
NET="aqtak-net"

if [[ $CLEAN -eq 1 ]]; then
    echo "==> Cleaning previous install"
    "$RUNTIME" rm -f aqtak aqtak-taky >/dev/null 2>&1 || true
    "$RUNTIME" rmi -f localhost/aqtak:latest localhost/aqtak-taky:latest >/dev/null 2>&1 || true
    "$RUNTIME" network rm "$NET" >/dev/null 2>&1 || true
    rm -rf "$DATA_DIR" "$TAKY_DIR"
fi

mkdir -p "$DATA_DIR" "$TAKY_DIR"

if [[ -z "$LOCATIONS" && ! -f "$CONF" ]]; then
    echo "--locations is required on first install"
    exit 1
fi

# Taky: private CoT server for AQ reports only
cat > "$TAKY_DIR/taky.conf" <<EOF
[taky]
hostname = aqtak-taky
node_id = AQTAK
bind_ip = 0.0.0.0

[cot_server]
port = 8087

[dp_server]
upload_path = /var/taky/dp-user

[ssl]
enabled = false
EOF

# AQTAK config (kept if it already exists and no --locations was given)
if [[ -n "$LOCATIONS" || ! -f "$CONF" ]]; then
    cat > "$CONF" <<EOF
locations=$LOCATIONS
openaq_api=https://api.openaq.org/v3

# Taky, reached by container name on the aqtak-net network
cot_host=aqtak-taky
cot_port=8087

# Used only when enable_aqi_colors=false
cot_type=a-f-G-E-S
callsign_prefix=$CALLSIGN_PREFIX

# Colour markers by AQI: blue/green/yellow/red (affiliation colours in ATAK)
enable_aqi_colors=true

poll_interval=$POLL_INTERVAL
stale_minutes=$STALE_MINUTES
config_reload=5
database=/data/aqtak-{last_edit}.db
log_level=INFO
EOF
    echo "==> Wrote $CONF"
fi

echo "==> Building images"
"$RUNTIME" build -t localhost/aqtak:latest -f "$APP_DIR/Containerfile" "$APP_DIR"
"$RUNTIME" build -t localhost/aqtak-taky:latest -f "$APP_DIR/taky/Containerfile" "$APP_DIR/taky"

"$RUNTIME" network exists "$NET" 2>/dev/null || "$RUNTIME" network create "$NET" >/dev/null

echo "==> Starting containers"
"$RUNTIME" rm -f aqtak aqtak-taky >/dev/null 2>&1 || true

"$RUNTIME" run -d --name aqtak-taky --restart=unless-stopped --network "$NET" \
    -p "$TAKY_PORT:8087/tcp" \
    -v "$TAKY_DIR/taky.conf:/etc/taky/taky.conf:ro" \
    localhost/aqtak-taky:latest >/dev/null

export OPENAQ_API_KEY="$API_KEY"
"$RUNTIME" run -d --name aqtak --restart=unless-stopped --network "$NET" \
    -e OPENAQ_API_KEY \
    -v "$DATA_DIR:/data" \
    localhost/aqtak:latest >/dev/null

sleep 6
echo "==> Taky log"
"$RUNTIME" logs aqtak-taky 2>&1 | tail -5
echo "==> AQTAK log"
"$RUNTIME" logs aqtak 2>&1 | tail -15

cat <<EOF

Done. Point ATAK at this host, TCP port $TAKY_PORT (no SSL).
  Logs:   $RUNTIME logs -f aqtak
  Config: $CONF  (edits reload automatically within ~5 s)
EOF
