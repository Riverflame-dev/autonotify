---
description: Set up autonotify from a fresh clone in 3 steps — install, connect accounts, schedule.
---

Set up **autonotify** for a new user on their own Mac, from a fresh clone. Three steps.
Do the mechanical work yourself; pause only where their browser is genuinely required.
Confirm each step worked before starting the next.

## Step 1 — Install

```bash
python3.11 -m venv .venv && source .venv/bin/activate && pip install -e .
autonotify train
```

Then fix one thing that breaks for everyone except the original author: `config.yaml` ships
with `stage2.config_dir: "~/.claude-personal"`. Ask which config dir their Claude CLI uses
(almost always `~/.claude`), set it, and verify:

```bash
CLAUDE_CONFIG_DIR=<their dir> claude -p 'Reply with only: ok' --output-format json
```

Stop if that errors — stage 2 won't work until it returns cleanly.

## Step 2 — Connect Gmail and their phone

**Gmail** — at <https://console.cloud.google.com>: new project → enable **Gmail API** →
**OAuth consent screen** (External; app name, support email, dev email) →
**Credentials → OAuth client ID → Desktop app** → download the JSON as `credentials.json`
in the repo root (gitignored).

Three traps, all of which cost real debugging time:
- **Publish the app to "In production."** Refresh tokens for Testing-status apps die after
  **7 days** and the job then fails silently. Production first asks for a homepage and privacy
  policy URL — this repo ships `docs/index.html` and `docs/privacy.html` for that. Have them
  push their fork **public**, enable Settings → Pages → `main` `/docs`, swap the contact email
  in `privacy.html` to their own, then fill Google's Branding page with those two URLs plus
  `<user>.github.io` under Authorized domains.
- **No app logo** — uploading one forces mandatory Google verification.
- Terms of service is optional; leave it blank.

**Phone** — generate a private topic, put it in `.env`, subscribe to it in the ntfy app:

```bash
python3 -c "import secrets; print('autonotify-' + secrets.token_urlsafe(16))"
cp .env.example .env    # set NTFY_TOPIC; leave ANTHROPIC_API_KEY blank
autonotify run --test-notify
```

Anyone who knows that topic string can read their notifications — keep it out of commits
and issues.

## Step 3 — First run, then schedule

```bash
autonotify run --dry-run     # opens the Gmail consent browser on first use
```

They'll hit "Google hasn't verified this app" → **Advanced → Go to (unsafe)** — expected for
an unverified personal app. `--dry-run` sends nothing and doesn't advance state, so review the
classifications together, then run `autonotify run` once for real and schedule it:

```bash
bash scripts/install_launchd.sh
launchctl list | grep com.autonotify.daily   # 2nd column is last exit status; 0 is healthy
```

---

Then tell them, briefly:

- **Failure is silent** — a broken run notifies nothing, so the only symptom is notifications
  stopping. First checks: `launchctl list | grep autonotify` and `tail logs/launchd.log`.
- **`pull.max_results: 200` is a per-run ceiling** — fine daily, but after a multi-day outage
  the backlog truncates to the newest 200 and the rest are skipped permanently.
- **Email text leaves the machine** — stage 1 (local embeddings) sees everything; stage 2 sends
  update candidates, full bodies included, to Claude. `stage2.enabled: false` makes it fully
  local at a real cost to precision.
- **Fixing a misclassification:** `autonotify correct --id <msg> --label <applied|update|ignore>`
  then `autonotify train`.
