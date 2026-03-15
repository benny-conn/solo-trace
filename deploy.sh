#!/bin/bash
set -e

MINI="benjaminconn@192.168.1.12"
REMOTE_DIR="/Users/benjaminconn/solo-trace"
PLIST_LABEL="com.bennyconn.solo-trace"
PLIST_PATH="$HOME/Library/LaunchAgents/$PLIST_LABEL.plist"

echo "==> Building solo-trace for arm64 macOS..."
GOOS=darwin GOARCH=arm64 go build -o solo-trace ./cmd/api/main.go

echo "==> Syncing binary and scripts..."
ssh "$MINI" "mkdir -p $REMOTE_DIR/scripts $REMOTE_DIR/data $REMOTE_DIR/logs"
rsync -av --exclude='.venv' --exclude='__pycache__' --exclude='output' --exclude='.git' --exclude='data' --exclude='jobs' --exclude='.DS_Store' --exclude='me_stems' --exclude='references' \
  solo-trace scripts/ "$MINI:$REMOTE_DIR/"

echo "==> Restarting service..."
ssh "$MINI" "launchctl kickstart -k gui/\$(id -u)/$PLIST_LABEL 2>/dev/null || launchctl start $PLIST_LABEL"

echo "==> Done. Tailing logs (ctrl+c to exit)..."
ssh "$MINI" "tail -f $REMOTE_DIR/logs/stdout.log"
