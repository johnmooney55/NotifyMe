#!/bin/bash
# Sync NotifyMe to Mac Mini
# Usage: ./scripts/sync-to-mini.sh [--db]
#   --db    Also sync the database
#
# Auto-detects local network vs remote (DDNS)

LOCAL_IP="192.168.4.119"
DDNS_HOST="housemooney.ddns.net"
DDNS_PORT="2222"
REMOTE_USER="johnmooney"
REPO_DIR="~/NotifyMe"

# Detect which connection to use
detect_connection() {
    # Try local IP first (faster)
    if ping -c 1 -W 1 $LOCAL_IP &>/dev/null; then
        echo "$REMOTE_USER@$LOCAL_IP"
        return 0
    fi

    # Fall back to DDNS
    echo "$REMOTE_USER@$DDNS_HOST -p $DDNS_PORT"
    return 0
}

MAC_MINI=$(detect_connection)
echo "Using: $MAC_MINI"

echo "Syncing code to Mac Mini..."

# Push any local commits first
git push origin main 2>/dev/null

# Pull on Mac Mini and reinstall
ssh $MAC_MINI "cd $REPO_DIR && git pull && source .venv/bin/activate && pip install -e . -q"

if [ $? -eq 0 ]; then
    echo "Code synced successfully"
else
    echo "Error: Code sync failed (is Mac Mini online?)"
    exit 1
fi

# Optionally sync database
if [[ "$1" == "--db" ]]; then
    echo "Syncing database..."
    if [[ "$MAC_MINI" == *"-p"* ]]; then
        # DDNS with custom port
        scp -P $DDNS_PORT ~/.notifyme/notifyme.db $REMOTE_USER@$DDNS_HOST:~/.notifyme/
    else
        # Local
        scp ~/.notifyme/notifyme.db $REMOTE_USER@$LOCAL_IP:~/.notifyme/
    fi
    echo "Database synced"
fi

echo "Done!"
