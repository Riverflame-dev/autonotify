---
description: Set up autonotify from a fresh clone — deps, Gmail OAuth, ntfy, and the daily job.
---

You are setting up **autonotify** for a new user on their own Mac, from a fresh clone.
Walk them through it end to end. Do the mechanical parts yourself; stop and wait whenever
a step needs their browser or a decision only they can make.

Work through the phases in order. After each phase, confirm it actually worked before
moving on — do not batch the whole thing and hope.

## Phase 1 — Preflight

Check and report:
- macOS (the scheduler is launchd; on Linux tell them they'll need cron instead and adapt).
- `python3.11 --version` — 3.11 is what this is tested on. If missing: `brew install python@3.11`.
- `claude --version` — the Claude CLI must be installed and logged in. Stage 2 shells out to it.

## Phase 2 — Install

```bash
python3.11 -m venv .venv && source .venv/bin/activate && pip install -e .
autonotify train
```

`train` builds `data/model.pkl` from the bundled synthetic examples. Confirm the file exists.

## Phase 3 — Point stage 2 at THEIR Claude account

`config.yaml` ships with `stage2.config_dir: "~/.claude-personal"`, which is specific to the
original author's two-account setup. **This will silently fail for anyone else.**

Ask which config dir their logged-in Claude CLI uses — for almost everyone it is `~/.claude`.
Edit `config.yaml` to match, then verify:

```bash
CLAUDE_CONFIG_DIR=<their dir> claude -p 'Reply with only: ok' --output-format json
```

If that errors, stop — stage 2 will not work until it returns cleanly.

## Phase 4 — Google Cloud / Gmail OAuth

This is the fiddliest part and the one with a trap in it. Have them do this in a browser
at <https://console.cloud.google.com> while you explain each step:

1. Create or pick a project.
2. **APIs & Services → Library → enable "Gmail API".**
3. **OAuth consent screen** → User type **External** → fill App name, User support email,
   Developer contact email.
4. **Publish the app to "In production".** ⚠️ Do not skip this and do not leave it in
   "Testing". Google expires refresh tokens for Testing-status apps after **7 days**, so a
   Testing app stops working silently one week after setup. Production needs a homepage URL
   and a privacy policy URL before it will let you publish — see Phase 5.
5. **Do NOT upload an app logo.** A logo triggers mandatory Google verification. Without one,
   an unverified production app works fine for personal use (capped at 100 users).
6. **Credentials → Create credentials → OAuth client ID → Desktop app** → download the JSON
   and save it as `credentials.json` in the repo root. It is gitignored.

## Phase 5 — Homepage + privacy policy URLs

Phase 4 step 4 needs two public URLs. This repo already ships `docs/index.html` and
`docs/privacy.html` for exactly this.

Tell them to:
1. Push their fork to GitHub as a **public** repo (GitHub Pages needs public on free accounts).
2. Settings → Pages → Deploy from a branch → `main` / `/docs` → Save.
3. Edit the contact email at the bottom of `docs/privacy.html` to **their own** before pushing —
   the policy describes whoever is operating the tool, and that is now them.
4. Back in Google Cloud → Branding, fill in:
   - Application home page: `https://<user>.github.io/<repo>/`
   - Application privacy policy link: `https://<user>.github.io/<repo>/privacy.html`
   - Authorized domains → Add domain: `<user>.github.io`
5. Then publish the app (Phase 4 step 4).

Terms of service is optional — leave it blank.

## Phase 6 — ntfy

1. Generate a long random topic — anyone who knows the string can read their notifications:
   `python3 -c "import secrets; print('autonotify-' + secrets.token_urlsafe(16))"`
2. `cp .env.example .env` and set `NTFY_TOPIC` to it. Leave `ANTHROPIC_API_KEY` blank —
   the default backend uses their Claude subscription, not the paid API.
3. Have them install the ntfy app (iOS/Android) and subscribe to that exact topic.
4. Verify the round trip: `autonotify run --test-notify` → their phone should buzz.

Do not print the topic into any file that gets committed, and do not paste it into a
GitHub issue or README later.

## Phase 7 — First real run

```bash
autonotify run --dry-run
```

The first invocation opens a browser for Gmail consent. They will see
"Google hasn't verified this app" → **Advanced → Go to (unsafe)** — expected for an
unverified personal app. This writes `token.json` (gitignored).

`--dry-run` prints what it would send and does not advance state, so review the output
together. If the classifications look sane, run it for real once: `autonotify run`.

## Phase 8 — Schedule it

```bash
bash scripts/install_launchd.sh
```

Runs 09:00 daily and catches up on the next wake if the Mac was asleep. Verify:

```bash
launchctl list | grep com.autonotify.daily
```

Second column is the last exit status — `0` is healthy, non-zero means the last run failed.

## Phase 9 — Hand off

Tell them, briefly:
- **Logs:** `logs/launchd.log` (crashes), `logs/run-report.txt` (per-run counts),
  `logs/decisions.jsonl` (per-email calls).
- **Failure is silent.** A broken run sends no notification, so the only symptom is
  notifications quietly stopping. If a day goes by with nothing, check
  `launchctl list | grep autonotify` and the tail of `logs/launchd.log` first.
- **`pull.max_results: 200` is a per-run ceiling.** It is plenty for a daily run, but after
  a multi-day outage the backlog gets truncated to the newest 200 and the rest are skipped
  permanently. Raise it before catching up on a long gap.
- **Email text leaves the machine.** Stage 1 (local embeddings) sees everything; stage 2
  sends update candidates — including full bodies — to Claude. If they want it fully local,
  set `stage2.enabled: false` in `config.yaml`, at a real cost to precision.
- **Fixing a misclassification:** `autonotify correct --id <msg> --label <applied|update|ignore>`
  then `autonotify train`.
