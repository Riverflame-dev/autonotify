"""Offline unit tests — Stage 1 classification and Stage 2 verdict parsing.

No network, no model download: Stage 1 uses a deterministic fake embedder, and
Stage 2 uses a mocked Anthropic client.
"""
from __future__ import annotations

import csv
import types

import numpy as np

from autonotify import classify
from autonotify.claude import verify
from autonotify.gmail import EmailMeta


# --------------------------------------------------------------------------- #
# Fake embedder — keyword-driven, deterministic, trivially separable.
# --------------------------------------------------------------------------- #
class FakeEmbedder:
    model_name = "fake"
    APPLIED = ("received your application", "thanks for applying", "application was received")
    UPDATE = ("interview", "assessment", "offer", "rejected", "unfortunately", "recruiter")
    IGNORE = ("newsletter", "sale", "connection", "jobs you may")

    def encode(self, texts: list[str]) -> np.ndarray:
        rows = []
        for t in texts:
            tl = t.lower()
            a = sum(k in tl for k in self.APPLIED)
            u = sum(k in tl for k in self.UPDATE)
            g = sum(k in tl for k in self.IGNORE)
            v = np.array([a, u, g, 0.1], dtype=np.float32) + 0.01
            v /= np.linalg.norm(v)
            rows.append(v)
        return np.stack(rows)


def _meta(mid, subject, snippet="", sender="Recruiting <jobs@corp.com>"):
    return EmailMeta(mid, mid, subject, sender, snippet, 0)


def _train_tmp(tmp_path):
    store = tmp_path / "store.csv"
    rows = [
        ("We received your application for Engineer", "applied"),
        ("Thanks for applying to Acme", "applied"),
        ("Your application was received", "applied"),
        ("Invitation to interview at Acme", "update"),
        ("Online assessment for your application", "update"),
        ("Unfortunately we will not move forward (rejected)", "update"),
        ("A recruiter wants to schedule a call", "update"),
        ("You have an offer!", "update"),
        ("Our weekly newsletter", "ignore"),
        ("Flash sale 50% off", "ignore"),
        ("You have a new connection", "ignore"),
        ("Jobs you may like this week", "ignore"),
    ]
    with open(store, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["text", "label", "source"])
        for text, lbl in rows:
            w.writerow([text, lbl, "synthetic"])
    model = tmp_path / "model.pkl"
    classify.train(store, FakeEmbedder(), "head", model)
    return classify.Classifier.load(model)


def test_stage1_separates_classes(tmp_path):
    clf = _train_tmp(tmp_path)
    metas = [
        _meta("a1", "We received your application for Data Scientist"),
        _meta("u1", "Invitation to interview next week"),
        _meta("i1", "Flash sale on shoes"),
    ]
    res = classify.run_stage1(metas, FakeEmbedder(), clf, {"update_candidate": 0.45})
    assert res.applied_count == 1
    assert len(res.update_candidates) == 1
    assert res.update_candidates[0].meta.id == "u1"
    assert res.ignored == 1


def test_applied_dedup_and_marker(tmp_path):
    clf = _train_tmp(tmp_path)
    e = FakeEmbedder()
    metas = [
        _meta("a1", "We received your application", sender="jobs@acme.com"),
        _meta("a2", "We received your application", sender="jobs@acme.com"),   # dup of a1
        _meta("a3", "Your application was received at Beta", sender="hr@beta.com"),
    ]
    res = classify.run_stage1(metas, e, clf, {"update_candidate": 0.45})
    assert res.applied_count == 2          # a1/a2 collapse, a3 distinct
    assert res.applied_dups_dropped == 1


def test_count_notification_format():
    from autonotify.notify import count_notification
    assert count_notification(3).title == "Application confirmations received: 3"
    assert count_notification(3, approx=True).title == "Application confirmations received: ~3"


def test_prototype_mode(tmp_path):
    store = tmp_path / "store.csv"
    with open(store, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["text", "label", "source"])
        for text, lbl in [
            ("we received your application", "applied"),
            ("interview invitation", "update"),
            ("weekly newsletter sale", "ignore"),
        ]:
            w.writerow([text, lbl, "synthetic"])
    model = tmp_path / "m.pkl"
    classify.train(store, FakeEmbedder(), "prototype", model)
    clf = classify.Classifier.load(model)
    assert clf.mode == "prototype"
    res = classify.run_stage1([_meta("u", "interview invitation")], FakeEmbedder(), clf,
                              {"update_candidate": 0.45})
    assert len(res.update_candidates) == 1


# --------------------------------------------------------------------------- #
# Stage 2 — mocked Claude client.
# --------------------------------------------------------------------------- #
def fake_runner(prompt: str):
    """Mimics a runner: returns (dict, tokens_in, tokens_out). Also exercises
    _extract_json by wrapping the verdict in ```json fences like `claude -p` does."""
    if "plausible" in prompt:
        return {"plausible": True}, 10, 2
    from autonotify.claude import _extract_json
    fenced = ('```json\n{"is_real_update": true, "company": "Stripe", '
              '"update_type": "interview", "summary": "Interview invite."}\n```')
    return _extract_json(fenced), 100, 20


def test_stage2_confirms_and_dedups():
    cand = types.SimpleNamespace(meta=_meta("m1", "Interview at Stripe"), update_prob=0.9)
    cand2 = types.SimpleNamespace(meta=_meta("m2", "Another email from Stripe"), update_prob=0.7)
    from autonotify.state import RunReport

    class Cfg:
        def get(self, dotted, default=None):
            return {"stage2.subject_only_precheck": True}.get(dotted, default)

    report = RunReport()
    confirmed = verify([cand, cand2], Cfg(), body_fetcher=lambda mid: "body text",
                       report=report, runner=fake_runner)
    # Both map to company Stripe -> deduped to one.
    assert len(confirmed) == 1
    assert confirmed[0].company == "Stripe"
    assert confirmed[0].update_type == "interview"
    assert report.tokens_in > 0
