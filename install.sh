#!/bin/bash
set -e

# AQTAK Installation Script
# Automates setup of AQTAK + Taky for air quality data into TAK

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Default values
TAKY_HOST="192.168.1.100"
TAKY_PORT="8087"
POLL_INTERVAL="10"
STALE_MINUTES="10"
CALLSIGN_PREFIX="AQ"
COT_TYPE="a-f-G-E-S"
WORK_DIR="$HOME/aqtak"
RUNTIME="podman"
API_KEY=""
LOCATIONS=""
SETUP_TAKY=0

# Functions
show_help() {
    cat << 'EOF'
AQTAK Installation Script

Usage: bash install.sh [OPTIONS]

Required Options:
  --api-key KEY              OpenAQ API key (required)
  --locations ID1,ID2        Comma-separated location IDs (required)

Optional Options:
  --taky-host HOST           Taky server IP or hostname (default: 192.168.1.100)
  --taky-port PORT           Taky CoT server port (default: 8087)
  --poll-interval SECS       Seconds between API polls (default: 10)
  --stale-minutes MINS       Marker stale timeout in minutes (default: 10)
  --callsign-prefix PREFIX   CoT marker prefix (default: AQ)
  --cot-type TYPE            CoT event type (default: a-f-G-E-S)
  --work-dir DIR             Working directory (default: ~/aqtak)
  --runtime RUNTIME          Container runtime: podman or docker (default: podman)
  --setup-taky               Also set up Taky container (requires Taky image)
  --help                     Show this message

Examples:
  bash install.sh --api-key sk_live_xxx --locations 12345,67890
  bash install.sh --api-key sk_live_xxx --locations 12345 --taky-host 10.0.0.5 --work-dir /opt/aqtak
  bash install.sh --api-key sk_live_xxx --locations 12345,67890 --runtime docker --setup-taky
EOF
}

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[✓]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[!]${NC} $1"
}

log_error() {
    echo -e "${RED}[✗]${NC} $1"
}

check_command() {
    if ! command -v "$1" &> /dev/null; then
        log_error "$1 is not installed"
        exit 1
    fi
}

check_image() {
    local image=$1
    if ! $RUNTIME images | grep -q "$image"; then
        log_error "Container image '$image' not found. Build it first:"
        echo "  cd $WORK_DIR && $RUNTIME build -t localhost/$image:latest ."
        exit 1
    fi
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --api-key)
            API_KEY="$2"
            shift 2
            ;;
        --locations)
            LOCATIONS="$2"
            shift 2
            ;;
        --taky-host)
            TAKY_HOST="$2"
            shift 2
            ;;
        --taky-port)
            TAKY_PORT="$2"
            shift 2
            ;;
        --poll-interval)
            POLL_INTERVAL="$2"
            shift 2
            ;;
        --stale-minutes)
            STALE_MINUTES="$2"
            shift 2
            ;;
        --callsign-prefix)
            CALLSIGN_PREFIX="$2"
            shift 2
            ;;
        --cot-type)
            COT_TYPE="$2"
            shift 2
            ;;
        --work-dir)
            WORK_DIR="$2"
            shift 2
            ;;
        --runtime)
            RUNTIME="$2"
            shift 2
            ;;
        --setup-taky)
            SETUP_TAKY=1
            shift
            ;;
        --help)
            show_help
            exit 0
            ;;
        *)
            log_error "Unknown option: $1"
            show_help
            exit 1
            ;;
    esac
done

# Validate required arguments
if [ -z "$API_KEY" ]; then
    log_error "Missing required argument: --api-key"
    show_help
    exit 1
fi

if [ -z "$LOCATIONS" ]; then
    log_error "Missing required argument: --locations"
    show_help
    exit 1
fi

# Validate poll interval
if [ "$POLL_INTERVAL" -lt 5 ]; then
    log_warning "poll_interval should be at least 5 seconds (OpenAQ rate limits)"
    POLL_INTERVAL=5
fi

echo -e "${BLUE}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║         AQTAK Installation and Setup                         ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════════════════════════╝${NC}"

# Step 1: Check prerequisites
log_info "Checking prerequisites..."

check_command "$RUNTIME"
log_success "$RUNTIME is installed"

if [ "$RUNTIME" = "podman" ] && ! podman machine inspect > /dev/null 2>&1 && [ ! -S /run/podman/podman.sock ]; then
    log_warning "Podman machine may not be running. If containers fail, start it with: podman machine start"
fi

# Step 2: Create directories
log_info "Creating project directories..."
mkdir -p "$WORK_DIR/config" "$WORK_DIR/data"
log_success "Directories created at $WORK_DIR"

# Step 3: Create AQTAK config
log_info "Creating AQTAK configuration..."
cat > "$WORK_DIR/config/aqtak.conf" << AQTAK_EOF
# OpenAQ Configuration
locations=$LOCATIONS
openaq_api=https://api.openaq.org/v3

# TAK/Taky Connection
cot_host=$TAKY_HOST
cot_port=$TAKY_PORT

# CoT Settings
cot_type=$COT_TYPE
callsign_prefix=$CALLSIGN_PREFIX

# Polling & Data Lifecycle
poll_interval=$POLL_INTERVAL
stale_minutes=$STALE_MINUTES

# Advanced
config_reload=5
database=/data/aqtak-{last_edit}.db
log_level=INFO
AQTAK_EOF
log_success "Config created at $WORK_DIR/config/aqtak.conf"

# Step 4: Optional Taky setup
if [ $SETUP_TAKY -eq 1 ]; then
    log_info "Setting up Taky container..."
    
    TAKY_DIR="$HOME/taky"
    mkdir -p "$TAKY_DIR/config" "$TAKY_DIR/data"
    
    cat > "$TAKY_DIR/config/taky.conf" << TAKY_EOF
[taky]
hostname = taky.local
node_id = TAKY
bind_ip = 0.0.0.0

[cot_server]
port = $TAKY_PORT

[dp_server]
upload_path = /var/taky/dp-user

[ssl]
enabled = False
TAKY_EOF
    log_success "Taky config created at $TAKY_DIR/config/taky.conf"
    
    check_image "taky"
    
    log_info "Stopping existing Taky container (if running)..."
    $RUNTIME stop taky 2>/dev/null || true
    $RUNTIME rm taky 2>/dev/null || true
    
    log_info "Starting Taky container..."
    $RUNTIME run -d \
        --name taky \
        --restart=unless-stopped \
        -p $TAKY_PORT:$TAKY_PORT/tcp \
        -v "$TAKY_DIR/config/taky.conf:/etc/taky/taky.conf:ro" \
        localhost/taky:latest
    
    log_success "Taky started (checking startup...)"
    sleep 2
    
    if $RUNTIME logs taky | grep -q "Listening for tcp"; then
        log_success "Taky is listening on port $TAKY_PORT"
    else
        log_warning "Taky startup logs:"
        $RUNTIME logs taky | head -10
    fi
fi

# Step 5: Check AQTAK image
log_info "Checking for AQTAK container image..."
if check_image "aqtak"; then
    log_success "AQTAK image found: localhost/aqtak:latest"
else
    log_warning "Building AQTAK image (this may take a minute)..."
    cd "$WORK_DIR"
    
    if [ ! -f "Dockerfile" ]; then
        log_error "Dockerfile not found in $WORK_DIR"
        log_info "Clone the repository first: git clone https://github.com/PrinoBotsCatto/aqtak.git $WORK_DIR"
        exit 1
    fi
    
    $RUNTIME build -t localhost/aqtak:latest .
    log_success "AQTAK image built"
fi

# Step 6: Stop existing AQTAK container
log_info "Checking for existing AQTAK container..."
if $RUNTIME inspect aqtak > /dev/null 2>&1; then
    log_info "Stopping existing AQTAK container..."
    $RUNTIME stop aqtak 2>/dev/null || true
    $RUNTIME rm aqtak 2>/dev/null || true
fi

# Step 7: Start AQTAK container
log_info "Starting AQTAK container..."
$RUNTIME run -d \
    --name aqtak \
    --restart=unless-stopped \
    -e OPENAQ_API_KEY="$API_KEY" \
    -v "$WORK_DIR/config/aqtak.conf:/app/config/aqtak.conf:ro" \
    -v "$WORK_DIR/data:/data" \
    localhost/aqtak:latest

log_success "AQTAK container started"

# Step 8: Verify startup
log_info "Waiting for AQTAK to start (5 seconds)..."
sleep 5

log_info "Checking AQTAK logs..."
AQTAK_LOG=$($RUNTIME logs aqtak 2>&1 | tail -20)

if echo "$AQTAK_LOG" | grep -q "Connected to Taky"; then
    log_success "AQTAK successfully connected to Taky!"
elif echo "$AQTAK_LOG" | grep -q "Connection refused"; then
    log_warning "AQTAK could not connect to Taky"
    echo "$AQTAK_LOG"
    log_info "Verify Taky is running and accessible at $TAKY_HOST:$TAKY_PORT"
else
    log_warning "AQTAK startup status:"
    echo "$AQTAK_LOG"
fi

# Step 9: Summary
echo ""
echo -e "${BLUE}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║                   Installation Complete!                      ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${GREEN}Configuration:${NC}"
echo "  API Key: ${API_KEY:0:20}..."
echo "  Locations: $LOCATIONS"
echo "  Taky: $TAKY_HOST:$TAKY_PORT"
echo "  Poll Interval: ${POLL_INTERVAL}s"
echo "  Callsign Prefix: $CALLSIGN_PREFIX"
echo ""
echo -e "${GREEN}Directories:${NC}"
echo "  Config: $WORK_DIR/config/aqtak.conf"
echo "  Data: $WORK_DIR/data"
echo ""
echo -e "${GREEN}Useful Commands:${NC}"
echo "  View logs:       $RUNTIME logs -f aqtak"
echo "  Stop AQTAK:      $RUNTIME stop aqtak"
echo "  Restart AQTAK:   $RUNTIME restart aqtak"
echo "  Edit config:     nano $WORK_DIR/config/aqtak.conf"
echo "  View Taky logs:  $RUNTIME logs -f taky"
echo ""
echo -e "${GREEN}Next Steps:${NC}"
echo "  1. In ATAK, add a server connection to Taky at $TAKY_HOST:$TAKY_PORT"
echo "  2. Wait 10-30 seconds for markers to appear"
echo "  3. Look for callsigns starting with '$CALLSIGN_PREFIX-'"
echo "  4. Check logs if markers don't appear: $RUNTIME logs aqtak"
echo ""
echo -e "${YELLOW}Troubleshooting:${NC}"
echo "  - If connection refused: Check Taky is running and firewall allows port $TAKY_PORT"
echo "  - If no markers: Check OpenAQ has data for your location IDs"
echo "  - If API errors: Verify API key is correct and has not expired"
echo "  - For help: See README.md in $WORK_DIR"
echo ""
