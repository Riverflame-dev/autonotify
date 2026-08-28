#!/usr/bin/env bash
# Cron entrypoint. Activates the venv and runs one pipeline pass.
# Pass --dry-run during the trial week (edit the crontab or this line).
set -euo pipefail

# launchd/cron give a minimal PATH; ensure Homebrew (for `claude`) is reachable.
export PATH="/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

if [ -d ".venv" ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

exec autonotify run "$@"
