#!/bin/bash
# Sync NotifyMe to Mac Mini
# Usage: ./scripts/sync-to-mini.sh [--db]
#   --db    Also sync the database

MAC_MINI="johnmooney@192.168.4.108"
REPO_DIR="~/NotifyMe"

echo "Syncing code to Mac Mini..."

# Push any local commits first
git push origin main 2>/dev/null

# Pull on Mac Mini and reinstall
ssh $MAC_MINI "cd $REPO_DIR && git pull && source .venv/bin/activate && pip install -e . -q"

if [ $? -eq 0 ]; then
    echo "Code synced successfully"
else
    echo "Error: Code sync failed"
    exit 1
fi

# Optionally sync database
if [[ "$1" == "--db" ]]; then
    echo "Syncing database..."
    scp ~/.notifyme/notifyme.db $MAC_MINI:~/.notifyme/
    echo "Database synced"
fi

echo "Done!"
