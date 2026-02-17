#!/bin/bash
#
# NotifyMe Setup Script
# Sets up NotifyMe on a fresh macOS machine
#
# Usage:
#   curl -sSL https://raw.githubusercontent.com/johnmooney55/NotifyMe/main/scripts/setup.sh | bash
#   OR
#   git clone https://github.com/johnmooney55/NotifyMe.git && cd NotifyMe && ./scripts/setup.sh
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

info() { echo -e "${BLUE}[INFO]${NC} $1"; }
success() { echo -e "${GREEN}[OK]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

echo ""
echo "=============================================="
echo "        NotifyMe Setup Script"
echo "=============================================="
echo ""

# Detect if we're in the repo or need to clone
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$SCRIPT_DIR/../pyproject.toml" ]]; then
    REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
    info "Found existing repo at: $REPO_DIR"
else
    REPO_DIR="$HOME/NotifyMe"
    if [[ -d "$REPO_DIR" ]]; then
        info "Found existing repo at: $REPO_DIR"
        cd "$REPO_DIR"
        git pull
    else
        info "Cloning NotifyMe repository..."
        git clone https://github.com/johnmooney55/NotifyMe.git "$REPO_DIR"
    fi
fi

cd "$REPO_DIR"

# Check Python version
info "Checking Python version..."
if ! command -v python3 &> /dev/null; then
    error "Python 3 is not installed. Install with: brew install python3"
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PYTHON_MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
PYTHON_MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)

if [[ "$PYTHON_MAJOR" -lt 3 ]] || [[ "$PYTHON_MAJOR" -eq 3 && "$PYTHON_MINOR" -lt 10 ]]; then
    error "Python 3.10+ required. Found: $PYTHON_VERSION"
fi
success "Python $PYTHON_VERSION"

# Create virtual environment
info "Setting up virtual environment..."
if [[ ! -d ".venv" ]]; then
    python3 -m venv .venv
    success "Created virtual environment"
else
    success "Virtual environment exists"
fi

# Activate virtual environment
source .venv/bin/activate

# Install dependencies
info "Installing dependencies..."
pip install --upgrade pip -q
pip install -e . -q
success "Installed NotifyMe package"

# Install Playwright
info "Installing Playwright for browser automation..."
pip install playwright -q
playwright install chromium
success "Installed Playwright + Chromium"

# Create data directory
info "Creating data directory..."
mkdir -p ~/.notifyme
success "Created ~/.notifyme"

# Check for .env file
echo ""
if [[ -f ".env" ]]; then
    success ".env file exists"
else
    warn ".env file not found!"
    echo ""
    echo "You need to create a .env file with your credentials."
    echo ""

    if [[ -f ".env.example" ]]; then
        read -p "Create .env from .env.example? [Y/n] " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]] || [[ -z $REPLY ]]; then
            cp .env.example .env
            info "Created .env from .env.example"
            echo ""
            echo "Edit .env with your credentials:"
            echo "  nano $REPO_DIR/.env"
            echo ""
        fi
    else
        cat > .env << 'EOF'
# Anthropic API Key for agentic checks
ANTHROPIC_API_KEY=sk-ant-...

# Gmail SMTP settings for notifications
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-email@gmail.com
SMTP_PASSWORD=your-gmail-app-password
NOTIFY_EMAIL=your-email@gmail.com

# For credits monitor (optional)
ANTHROPIC_CONSOLE_EMAIL=your-console-email@example.com
IMAP_HOST=imap.gmail.com
IMAP_USER=your-email@gmail.com
IMAP_PASSWORD=your-gmail-app-password
EOF
        info "Created template .env file"
        echo ""
        echo "Edit .env with your credentials:"
        echo "  nano $REPO_DIR/.env"
        echo ""
    fi
fi

# Check for existing database (offer to copy from another machine)
echo ""
if [[ -f "$HOME/.notifyme/notifyme.db" ]]; then
    success "Database exists at ~/.notifyme/notifyme.db"
    MONITOR_COUNT=$(sqlite3 ~/.notifyme/notifyme.db "SELECT COUNT(*) FROM monitors WHERE is_active=1" 2>/dev/null || echo "0")
    info "Found $MONITOR_COUNT active monitor(s)"
else
    warn "No database found. You'll start fresh or can copy from another machine:"
    echo "  scp other-machine:~/.notifyme/notifyme.db ~/.notifyme/"
fi

# Install launchd scheduler
echo ""
info "Setting up background scheduler..."

SCHEDULER_INTERVAL=5
read -p "Check interval in minutes (default: 5): " -r USER_INTERVAL
if [[ -n "$USER_INTERVAL" ]]; then
    SCHEDULER_INTERVAL=$USER_INTERVAL
fi

# Generate plist
notifyme install-scheduler --interval "$SCHEDULER_INTERVAL"

# Check if already loaded
if launchctl list | grep -q "com.notifyme.checker"; then
    info "Scheduler already running, reloading..."
    launchctl unload ~/Library/LaunchAgents/com.notifyme.checker.plist 2>/dev/null || true
fi

# Load scheduler
launchctl load ~/Library/LaunchAgents/com.notifyme.checker.plist
success "Scheduler installed (checking every ${SCHEDULER_INTERVAL} minutes)"

# Test email (optional)
echo ""
read -p "Test email configuration? [y/N] " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    notifyme test-email || warn "Email test failed. Check your .env settings."
fi

# Summary
echo ""
echo "=============================================="
echo "        Setup Complete!"
echo "=============================================="
echo ""
echo "Location:   $REPO_DIR"
echo "Database:   ~/.notifyme/notifyme.db"
echo "Logs:       ~/.notifyme/scheduler.log"
echo "Scheduler:  Every $SCHEDULER_INTERVAL minutes (runs on restart)"
echo ""
echo "Quick commands:"
echo "  cd $REPO_DIR && source .venv/bin/activate"
echo "  notifyme list              # View monitors"
echo "  notifyme check --all       # Check all now"
echo "  notifyme add --help        # Add a monitor"
echo "  tail -f ~/.notifyme/scheduler.log  # Watch logs"
echo ""
echo "To copy monitors from another machine:"
echo "  scp laptop:~/.notifyme/notifyme.db ~/.notifyme/"
echo "  scp laptop:~/NotifyMe/.env ~/NotifyMe/"
echo ""
