"""Command-line entrypoint: run | train | correct."""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from functools import partial

from . import classify, gmail, notify
from .claude import verify
from .state import Checkpoint, Config, DecisionLog, ProcessedIds, RunReport


# --------------------------------------------------------------------------- #
# run
# --------------------------------------------------------------------------- #
def cmd_run(cfg: Config, dry_run: bool, test_notify: bool) -> int:
    server = cfg.get("notify.server", "https://ntfy.sh")

    if test_notify:
        if not cfg.ntfy_topic:
            print("NTFY_TOPIC not set in .env — cannot test notifications.", file=sys.stderr)
            return 1
        n = notify.NtfyNotifier(server, cfg.ntfy_topic)
        n.send(notify.count_notification(5))
        from .claude import ConfirmedUpdate
        n.send(notify.update_notification(
            ConfirmedUpdate("Stripe", "interview", "Interview invite for the Backend role.", "TESTID")))
        print("Sent two sample notifications to your ntfy topic.")
        return 0

    model_path = cfg.path("model")
    if not model_path.exists():
        print(f"No trained model at {model_path}. Run `autoupdate train` first.", file=sys.stderr)
        return 1

    clf = classify.Classifier.load(model_path)
    embedder = classify.SentenceTransformerEmbedder(clf.embedding_model)

    checkpoint = Checkpoint(cfg.path("checkpoint"))
    processed = ProcessedIds(cfg.path("processed_ids"))
    decisions = DecisionLog(cfg.path("decision_log"))
    report = RunReport()

    run_start = datetime.now(timezone.utc)
    since = checkpoint.query_since(
        int(cfg.get("pull.overlap_minutes", 30)),
        int(cfg.get("pull.first_run_lookback_hours", 24)),
    )

    try:
        metas = gmail.pull(
            cfg.path("credentials"), cfg.path("token"), since,
            max_results=int(cfg.get("pull.max_results", 200)),
        )
    except FileNotFoundError as e:
        print(f"Gmail is not set up yet: {e}\nSee the TODO in README.md.", file=sys.stderr)
        return 1
    fresh = [m for m in metas if m.id not in processed]
    report.pulled = len(fresh)
    report.extra["window_since"] = since.isoformat(timespec="seconds")
    report.extra["dry_run"] = dry_run

    # Stage 1 — local, free.
    s1 = classify.run_stage1(fresh, embedder, clf, cfg.get("thresholds", {}), decisions)
    report.applied = s1.applied_count
    report.update_candidates = len(s1.update_candidates)
    report.ignored = s1.ignored

    # Stage 2 — Claude, paid, only if enabled.
    confirmed = []
    if cfg.get("stage2.enabled", False) and s1.update_candidates:
        confirmed = verify(
            s1.update_candidates, cfg,
            body_fetcher=partial(gmail.fetch_body, cfg.path("credentials"), cfg.path("token")),
            decision_log=decisions, report=report,
        )
    elif s1.update_candidates:
        # Local-only: treat every candidate as an update (no company/type from Claude).
        from .claude import ConfirmedUpdate
        for c in s1.update_candidates:
            confirmed.append(ConfirmedUpdate(
                company=(c.meta.sender.split("<")[0].strip() or "Unknown"),
                update_type="other", summary=c.meta.subject, msg_id=c.meta.id))
        report.confirmed = len(confirmed)

    # Notify (or print, in dry-run / no-topic).
    if dry_run or not cfg.ntfy_topic:
        notifier: notify.Notifier = notify.ConsoleNotifier()
    else:
        notifier = notify.NtfyNotifier(server, cfg.ntfy_topic)

    sent = 0
    if s1.applied_count > 0 or cfg.get("notify.notify_on_zero", False):
        notifier.send(notify.count_notification(s1.applied_count))
        sent += 1
    for u in confirmed:
        notifier.send(notify.update_notification(u))
        sent += 1
    report.notified = sent

    # Persist state only on a real (non-dry) run, so dry-runs are repeatable.
    if not dry_run:
        processed.add_many([m.id for m in fresh])
        checkpoint.mark_success(run_start)

    price_in = float(cfg.get("stage2.price_in_per_mtok", 1.0))
    price_out = float(cfg.get("stage2.price_out_per_mtok", 5.0))
    print(report.render(price_in, price_out))
    report.write(cfg.path("run_report"), price_in, price_out)
    return 0


# --------------------------------------------------------------------------- #
# train
# --------------------------------------------------------------------------- #
def cmd_train(cfg: Config, mode: str | None, model_name: str | None) -> int:
    mode = mode or cfg.get("mode", "head")
    model_name = model_name or cfg.get("embedding_model")
    print(f"Loading embedder {model_name} …")
    embedder = classify.SentenceTransformerEmbedder(model_name)
    report = classify.train(cfg.path("store"), embedder, mode, cfg.path("model"))
    print(report)
    return 0


# --------------------------------------------------------------------------- #
# correct
# --------------------------------------------------------------------------- #
def cmd_correct(cfg: Config, msg_id: str | None, text: str | None, label: str) -> int:
    if label not in classify.LABELS:
        print(f"label must be one of {classify.LABELS}", file=sys.stderr)
        return 1

    if text is None and msg_id:
        # Recover text from the most recent decision-log entry for this id.
        log_path = cfg.path("decision_log")
        if log_path.exists():
            for line in reversed(log_path.read_text().splitlines()):
                rec = json.loads(line)
                if rec.get("id") == msg_id and rec.get("subject"):
                    text = rec["subject"]
                    break
    if not text:
        print("Provide --text, or --id of an email already in logs/decisions.jsonl.", file=sys.stderr)
        return 1

    store = cfg.path("store")
    store.parent.mkdir(parents=True, exist_ok=True)
    write_header = not store.exists()
    with open(store, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(["text", "label", "source"])
        w.writerow([text, label, "correction"])
    print(f"Appended correction ({label}) to {store}. Run `autoupdate train` to apply.")
    return 0


# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autoupdate", description="Gmail job-application watcher.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="run the pipeline once")
    p_run.add_argument("--dry-run", action="store_true", help="log what it would notify; send nothing; don't advance state")
    p_run.add_argument("--test-notify", action="store_true", help="send one sample of each notification type")

    p_train = sub.add_parser("train", help="(re)build the classifier from data/store.csv")
    p_train.add_argument("--mode", choices=["head", "prototype"])
    p_train.add_argument("--model", help="override embedding model name")

    p_corr = sub.add_parser("correct", help="append a relabel to the training store")
    p_corr.add_argument("--id", help="message id from logs/decisions.jsonl")
    p_corr.add_argument("--text", help="raw text to label (subject/snippet)")
    p_corr.add_argument("--label", required=True, choices=classify.LABELS)

    args = parser.parse_args(argv)
    cfg = Config()

    if args.cmd == "run":
        return cmd_run(cfg, args.dry_run, args.test_notify)
    if args.cmd == "train":
        return cmd_train(cfg, args.mode, args.model)
    if args.cmd == "correct":
        return cmd_correct(cfg, args.id, args.text, args.label)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
