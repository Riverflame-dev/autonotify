#!/usr/bin/env bash
# Install a launchd agent that runs autonotify at 11:00 daily.
# Unlike cron, launchd runs a missed job when the Mac next wakes.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.autonotify.daily"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

mkdir -p "$HOME/Library/LaunchAgents" "$REPO_DIR/logs"

cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>$REPO_DIR/scripts/run.sh</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key><integer>11</integer>
        <key>Minute</key><integer>0</integer>
    </dict>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key><string>/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    </dict>
    <key>StandardOutPath</key><string>$REPO_DIR/logs/launchd.log</string>
    <key>StandardErrorPath</key><string>$REPO_DIR/logs/launchd.log</string>
    <key>RunAtLoad</key><false/>
</dict>
</plist>
PLIST_EOF

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load -w "$PLIST"

echo "Loaded $LABEL — runs 11:00 daily, catches up on wake if the Mac was asleep."
echo "Plist : $PLIST"
echo "Logs  : $REPO_DIR/logs/launchd.log"
echo "Remove with: launchctl unload \"$PLIST\" && rm \"$PLIST\""
