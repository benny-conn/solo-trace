#!/bin/bash
set -e

MINI="benjaminconn@192.168.1.83"
REMOTE_DIR="/Users/benjaminconn/solo-trace"
PLIST_LABEL="com.bennyconn.solo-trace"
PLIST_PATH="$HOME/Library/LaunchAgents/$PLIST_LABEL.plist"

echo "==> Building solo-trace for arm64 macOS..."
GOOS=darwin GOARCH=arm64 go build -o solo-trace ./cmd/api/main.go

echo "==> Syncing binary and scripts..."
ssh "$MINI" "mkdir -p $REMOTE_DIR/scripts $REMOTE_DIR/data $REMOTE_DIR/logs"
rsync -av solo-trace "$MINI:$REMOTE_DIR/"
rsync -av --exclude='.venv' --exclude='__pycache__' --exclude='output' --exclude='.git' --exclude='data' --exclude='jobs' --exclude='.DS_Store' --exclude='me_stems' --exclude='references' \
  scripts/ "$MINI:$REMOTE_DIR/scripts/"

echo "==> Bootstrapping Python venv..."
ssh "$MINI" "cd $REMOTE_DIR/scripts && PYTHON=\$(command -v python3.11 || command -v /opt/homebrew/bin/python3.11 || echo '') && [ -z \"\$PYTHON\" ] && echo 'ERROR: python3.11 not found — run: brew install python@3.11' && exit 1; rm -rf .venv && \$PYTHON -m venv .venv && .venv/bin/pip install -q -r requirements.lock"

echo "==> Installing plist and restarting service..."
rsync -av Service.plist "$MINI:$HOME/Library/LaunchAgents/$PLIST_LABEL.plist"
ssh "$MINI" "launchctl unload ~/Library/LaunchAgents/$PLIST_LABEL.plist 2>/dev/null; launchctl load ~/Library/LaunchAgents/$PLIST_LABEL.plist"

echo "==> Done. Tailing logs (ctrl+c to exit)..."
ssh "$MINI" "tail -f $REMOTE_DIR/logs/stdout.log"
