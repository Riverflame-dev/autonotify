# Job-Application Inbox Watcher — Implementation Plan (simplified)

## Context

Your Gmail drowns real job-application signal (application confirmations; OA/interview/
offer/rejection updates) in marketing mail that reuses the same words ("congratulations",
"next steps", "we'd like to invite you"). Keyword filters have failed because the vocabulary is
identical — you need *semantic* understanding, done locally and free, with a paid Claude call only
as a last-resort tiebreaker on the handful of emails that survive.

Goal: a cron job (11:00 & 16:00 local) that per run sends two ntfy notifications — an
**applications-sent count** and **per-company application updates** — and drops everything else
locally before it costs a token. Validation is live: a dry-run decision log you skim + a one-command
correction loop, not an upfront hand-labeled test set.

Fresh repo at `~/Workspace/autonotify` (empty, not yet git). Will `git init` and later
push to a private remote.

### Decisions locked from your feedback
- **Keep it small** — ~6 modules, flat package (structure below).
- **Synthetic training data**: I generate it **directly, now, on your Claude Pro session** and write
  it to `data/store.csv` — no API key, no cost, no separate generator script shipped.
- **Encouragement lines**: simple and hype.
- **Zero applications → send nothing** (skip the count notification).
- **Python venv**: use whatever version installs cleanly (try 3.12, fall back to system 3.14 CPU).
- **Overlap window**: ~30 min on top of last-success timestamp — approved.
- **ntfy topic, Google Cloud OAuth, and Anthropic API key are TODOs** you don't have yet — see below.

### Important: Pro subscription vs API key
Your Claude **Pro** subscription lets *me* generate the labeled dataset during this build. The
**runtime Stage-2 tiebreaker is a pay-as-you-go API call** (Pro ≠ API access), so it needs an
`ANTHROPIC_API_KEY`. That's on the TODO list. Until it's set, the pipeline runs **local-only**
(`stage2.enabled: false` in config) — Stage 1 alone still produces both notifications; update
candidates just skip the Claude confirmation. You flip Stage 2 on when the key exists.

---

## Repo structure (flat, ~6 modules)

```
autonotify/
├── README.md                  # setup + the TODO checklist
├── pyproject.toml             # deps + `autonotify` console entrypoint
├── config.yaml                # thresholds, model choice, mode, stage2.enabled, notify_on_zero
├── .env.example               # NTFY_TOPIC, ANTHROPIC_API_KEY (real .env gitignored)
├── .gitignore                 # .env, credentials.json, token.json, data/model.pkl, logs/, state/
├── autonotify/
│   ├── __init__.py
│   ├── cli.py                 # run [--dry-run] [--test-notify] | train | correct
│   ├── gmail.py               # OAuth desktop flow + Stage 0 incremental pull (meta only)
│   ├── classify.py            # Stage 1: embed (Qwen3-0.6B) + logistic head → count + candidates
│   ├── claude.py              # Stage 2: fetch body + Haiku JSON verdict + per-company dedup
│   ├── notify.py              # notify() interface + NtfyNotifier + hype lines
│   └── state.py               # config load, checkpoint, processed-ids, run report, decision log
├── data/
│   ├── store.csv              # text,label,source  (I pre-populate with synthetic; corrections append)
│   └── model.pkl              # trained head (gitignored)
├── scripts/
│   ├── run.sh                 # cron entrypoint (activate venv + run)
│   └── install_cron.sh        # writes the two crontab lines
└── tests/
    ├── fixtures/              # saved example emails (subject/from/snippet/body)
    └── test_pipeline.py       # Stage 1 classify + Stage 2 parse, offline
```

**Stage boundaries** (each testable offline):
- **Stage 0** `gmail.pull()` → `list[EmailMeta{id, thread_id, subject, sender, snippet, date}]` (no bodies).
- **Stage 1** `classify.run()` → `(applied_count, update_candidates)`; drops `ignore`, counts confident `applied` without Claude.
- **Stage 2** `claude.verify()` → confirmed updates, bodies fetched only here, deduped one-per-company; **skipped entirely if `stage2.enabled: false`**.
- **`notify.notify(...)`** — clean interface, `NtfyNotifier` default; stages never touch ntfy directly.

---

## How it works (per run)

1. **Pull** (free): checkpoint stores the last *successful* run's ISO timestamp. Query Gmail
   `after:<last_success − 30min overlap>`, fetch subject/from/snippet/id only. `processed_ids`
   (persisted set) dedups, so the overlap never double-counts. Checkpoint advances **only on
   success** → a missed run (laptop asleep) is absorbed by the next wider window; a crash re-runs
   rather than loses.
2. **Classify** (free): embed `subject + snippet` with **Qwen3-Embedding-0.6B** (Apache-2.0,
   CPU, `sentence-transformers`), run a **scikit-learn logistic-regression head** over the frozen
   embeddings → `applied` / `update` / `ignore` with a confidence. `ignore` dropped (never costs a
   token). Confident `applied` counted, never sent to Claude. `update` → candidates. (Config toggle
   `mode: head|prototype`; prototype = cosine-to-class-mean, the zero-training fallback.)
3. **Verify + notify** (paid, tiny volume, skippable): for each candidate fetch the body now, send
   to **`claude-haiku-4-5`** (cheap/fast; structured-output JSON `{is_real_update, company,
   update_type, summary}`). Drop non-updates; dedup to one notification per company (prefer most
   advanced stage). Then send via ntfy:
   - **Count**: title `Applications sent: N`, body a hype line. Skipped when N=0.
   - **Updates**: title `Interview — Stripe` (type + company), body the one-liner, priority scaled
     (offers/interviews high), click URL deep-linking to the Gmail message.

Per-run report (printed + `logs/`): `pulled / applied / update / ignore / sent-to-Claude /
confirmed / notified` + approx Claude tokens & est. cost. Bodies leave the machine **only** at
Stage 2, **only** for survivors.

---

## Training data & the correction loop

- **Synthetic (primary)**: I generate a varied labeled set now — `applied` confirmations; `update`
  emails (OA, interview, recruiter call, offer, rejection, status); `ignore` hard negatives
  (LinkedIn reactions, promos, newsletters, "congratulations" marketing) — varying industry, tone,
  length, with injected messiness (typos, forwarded chains, curt rejections). Written to
  `data/store.csv` as `text,label,source=synthetic`.
- **Public real examples (optional grit)**: a small text-only, quality-filtered set folded into the
  same CSV as `source=public` if useful — not a separate shipped script.
- **Corrections (the real validation)**: dry-run + live decisions are logged to `logs/decisions.jsonl`
  (class, confidence, company, Gmail link; near-threshold easy to spot). `autonotify correct` (or
  editing the `label` column of a flagged CSV) appends a relabel as `source=correction`.
  `autonotify train` rebuilds the head from the whole store in one command. `train` prints 5-fold
  CV accuracy / per-class P-R as a sanity check (the real metric is your flags).

---

## Cron

```
0 11 * * *  ~/Workspace/autonotify/scripts/run.sh >> .../logs/cron.log 2>&1
0 16 * * *  ~/Workspace/autonotify/scripts/run.sh >> .../logs/cron.log 2>&1
```
`install_cron.sh` installs them; `run.sh` activates the venv + runs `autonotify run` (use
`--dry-run` during the trial week). Logs in `logs/`; state in `state/`.

---

## Dependencies

`google-api-python-client`, `google-auth-oauthlib` (Gmail + OAuth) · `sentence-transformers`
(pulls CPU torch) · `scikit-learn`, `numpy` (head) · `anthropic` (Stage 2) · `requests` (ntfy) ·
`pydantic`/`pydantic-settings`, `pyyaml` (config) · `pytest` (dev).

---

## TODO checklist for you (external accounts — not blocking the build)

The code and synthetic data get built now; these are things only you can create. README will have
exact click paths.

- [ ] **Google Cloud + Gmail OAuth**: project → enable Gmail API → OAuth consent (External,
      scope `gmail.readonly`, add your email as Test user) → Desktop OAuth client → download
      `credentials.json` to repo root. First run opens a browser once, caches `token.json`.
- [ ] **ntfy**: pick a private topic, put `NTFY_TOPIC=...` in `.env` (default server ntfy.sh).
- [ ] **Anthropic API key** (for Stage 2 only): `ANTHROPIC_API_KEY=...` in `.env`. Until then leave
      `stage2.enabled: false` and the pipeline runs local-only.

---

## Verification

- **Offline unit**: `pytest tests/` — Stage 1 classify on `tests/fixtures/`, Stage 2 JSON parse
  against a mocked Anthropic client. No network.
- **Notify smoke**: `autonotify run --test-notify` sends one sample of each notification to your
  ntfy topic (after you set it) so you confirm format on your phone.
- **Pull smoke**: `autonotify run --dry-run` after OAuth — confirms pull + checkpoint + decision log
  without sending.
- **Live acceptance**: ~1 week `--dry-run`, skim `logs/decisions.jsonl`, `correct` + `train`, then
  flip cron to live (and `stage2.enabled: true` once the API key exists).
```
