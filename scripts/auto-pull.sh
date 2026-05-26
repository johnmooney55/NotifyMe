#!/bin/bash
cd ~/NotifyMe
git fetch origin main --quiet
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)
if [ "$LOCAL" != "$REMOTE" ]; then
    echo "$(date): Pulling updates..."
    git pull --quiet
    source .venv/bin/activate
    pip install -e . -q
    echo "$(date): Updated to $(git rev-parse --short HEAD)"
fi
