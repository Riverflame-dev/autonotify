#!/usr/bin/env bash
# Install the twice-daily (11:00 & 16:00 local) cron jobs.
# Idempotent: removes any prior autonotify lines first (matched by the marker comment).
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN="$REPO_DIR/scripts/run.sh"
LOG="$REPO_DIR/logs/cron.log"
MARK="# autonotify-inbox-watcher"

mkdir -p "$REPO_DIR/logs"

current="$(crontab -l 2>/dev/null | grep -v "$MARK" || true)"
{
  printf '%s\n' "$current"
  echo "0 11 * * * $RUN >> $LOG 2>&1 $MARK"
} | crontab -

echo "Installed cron job (11:00 local, daily):"
crontab -l | grep "$MARK"
echo
echo "Edit or remove with: crontab -e"
