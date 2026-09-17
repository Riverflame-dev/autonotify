# autonotify

Scans your Gmail once a day and pushes a phone notification (via [ntfy](https://ntfy.sh))
for real job-application activity — a **count of application confirmations** and a
**per-company update** (OA / interview / call / offer / rejection) — while ignoring the
marketing, newsletters, and social noise that use the same words.

## How it works

Three stages, cost increasing at each:

1. **Pull** (`gmail.py`) — Gmail metadata since the last run.
2. **Classify** (`classify.py`) — local embeddings (Qwen3-0.6B) + a logistic head sort each
   email into `applied` / `update` / `ignore`. Free; most mail is dropped here.
3. **Verify** (`claude.py`) — only update candidates go to `claude -p` (your Claude
   subscription, no API key) to confirm and label them; deduped to one per company.

## Setup

```bash
# 1. Install (Python 3.11 recommended)
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e .

# 2. Train the local classifier from the bundled data
autonotify train

# 3. Accounts (one-time)
#    - Google Cloud: enable Gmail API, make a Desktop OAuth client,
#      download it as ./credentials.json (read-only gmail scope).
#    - ntfy: pick a private topic, then: cp .env.example .env  and set NTFY_TOPIC.
#    - Stage 2 uses `claude -p` on the account in config.yaml (stage2.config_dir);
#      make sure that Claude CLI is installed and logged in.

# 4. First run authorizes Gmail in the browser, then works headless after.
autonotify run --dry-run     # prints instead of sending; --test-notify checks ntfy
```

## Run it daily

```bash
bash scripts/install_launchd.sh   # 09:00 local; catches up on wake if the Mac was asleep
```

## Setting this up yourself

Clone it, open Claude Code in the repo, and run `/setup` — it walks through deps, the
Google OAuth consent screen (including the publishing-status trap that silently kills
refresh tokens after 7 days), ntfy, and the daily job.

Manual commands: `autonotify run` (send), `autonotify run --dry-run` (preview),
`autonotify correct --id <msg> --label <applied|update|ignore>` then `autonotify train`
(fix a misclassification).

## Notes

- All tunables live in `config.yaml`; secrets in `.env`. Both `state/` and `logs/` are local.
- The confirmation count is deduped; a `~` prefix means duplicates were collapsed so it's
  approximate. For exact application counts, use your tracker — this tool is for *awareness*.
