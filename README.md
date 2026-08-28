# autoupdate — job-application inbox watcher

A mostly-free, privacy-preserving pipeline that scans Gmail twice a day for job-application
activity and sends two kinds of [ntfy](https://ntfy.sh) notification — an **applications-sent
count** and **per-company application updates** (OA / interview / call / offer / rejection) —
while ignoring the flood of promos, newsletters, and social notifications that reuse the same
vocabulary. Semantic understanding is done **locally and for free**; a paid Claude call is used
only as a final tiebreaker on the handful of emails that survive.

See [`docs/PLAN.md`](docs/PLAN.md) for the full design.

## Architecture (three stages, cost increasing)

| Stage | Where | Cost | What |
|------|-------|------|------|
| 0 Pull | `gmail.py` | free | Gmail metadata (subject/from/snippet/id) since the last successful run |
| 1 Classify | `classify.py` | free | local embeddings (Qwen3-0.6B) + a logistic head → `applied` / `update` / `ignore` |
| 2 Verify | `claude.py` | paid, tiny | Claude Haiku confirms update candidates, one notification per company |

Only update candidates ever reach Claude, and full bodies are fetched **only** at Stage 2 —
nothing else leaves your machine.

## Setup

```bash
# 1. Create a venv and install (Python 3.11 recommended for wheel coverage)
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e .

# 2. Train the local classifier from the bundled synthetic data
autoupdate train

# 3. Try it without any accounts (prints to console, sends nothing, no state change)
autoupdate run --dry-run     # (needs Gmail OAuth first — see TODO below)
pytest                       # offline unit tests, no accounts needed
```

## TODO — external accounts you set up (not needed to build/train)

- [ ] **Google Cloud + Gmail OAuth**
  1. console.cloud.google.com → new project.
  2. APIs & Services → Library → enable **Gmail API**.
  3. OAuth consent screen → **External**; add scope `.../auth/gmail.readonly`; add your Gmail as a **Test user** (stays in Testing mode, no verification needed).
  4. Credentials → Create → **OAuth client ID** → **Desktop app** → download JSON as `credentials.json` in this repo root.
  5. First `autoupdate run` opens a browser once and caches `token.json`.
- [ ] **ntfy** — pick a private, random topic; `cp .env.example .env` and set `NTFY_TOPIC=...`. Then `autoupdate run --test-notify` to confirm it reaches your phone.

### Stage 2 uses your Claude subscription (no API key)

Stage 2 shells out to `claude -p` (Claude Code in one-shot mode), authenticated by your
**personal Claude subscription** — no Anthropic API key, no per-call billing. Controlled in
`config.yaml` under `stage2:`:

- `backend: claude_cli` — the default (subprocess to `claude -p`).
- `config_dir: ~/.claude-personal` — which logged-in Claude account to use (your personal sub).
- `model: claude-haiku-4-5` — passed to `claude -p --model`.

Requires the `claude` CLI installed and logged in to that account. (Set `backend: api` +
`ANTHROPIC_API_KEY` only if you'd rather use the paid API.)

## Daily use

```bash
# Install the twice-daily cron jobs (11:00 & 16:00 local)
bash scripts/install_cron.sh

# Trial week: run in dry-run and skim the decision log
autoupdate run --dry-run
tail -f logs/decisions.jsonl

# Correct a mistake, then retrain — one command each
autoupdate correct --id <message-id-from-the-log> --label update
autoupdate train
```

`config.yaml` holds every tunable (thresholds, model, `mode: head|prototype`, `stage2.enabled`,
`notify_on_zero`). Secrets live in `.env`. State (`state/`) and logs (`logs/`) are gitignored.

## How validation works

There's no hand-labeled test set. `autoupdate train` prints a cross-validation sanity check, but
the **real** signal is your dry-run decision log: run for ~a week, skim `logs/decisions.jsonl`
(near-threshold decisions are flagged), `correct` the misses, `train` again. The classifier starts
decent from generated data and converges on your real inbox through corrections.
