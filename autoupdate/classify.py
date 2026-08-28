"""Stage 1 — local semantic filter (free; does the heavy lifting).

Embed subject+snippet with a frozen local model, then classify with either a
logistic-regression head (default) or class-mean prototypes. The embedder is
injected so this module is unit-testable offline with a stub.
"""
from __future__ import annotations

import csv
import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

LABELS = ["applied", "update", "ignore"]


# --------------------------------------------------------------------------- #
# Embedder
# --------------------------------------------------------------------------- #
class Embedder(Protocol):
    def encode(self, texts: list[str]) -> np.ndarray: ...


class SentenceTransformerEmbedder:
    """Lazy wrapper around sentence-transformers (heavy import kept out of module load)."""

    def __init__(self, model_name: str):
        from sentence_transformers import SentenceTransformer

        self.model_name = model_name
        self._model = SentenceTransformer(model_name)

    def encode(self, texts: list[str]) -> np.ndarray:
        return np.asarray(
            self._model.encode(texts, normalize_embeddings=True, show_progress_bar=False),
            dtype=np.float32,
        )


# --------------------------------------------------------------------------- #
# Classifier (head or prototype), persisted to model.pkl
# --------------------------------------------------------------------------- #
@dataclass
class Classifier:
    mode: str                 # "head" | "prototype"
    labels: list[str]
    embedding_model: str
    head: object = None       # sklearn LogisticRegression (mode="head")
    prototypes: np.ndarray | None = None  # (n_labels, dim) unit vectors (mode="prototype")

    def predict_proba(self, embeddings: np.ndarray) -> np.ndarray:
        """Return per-label probabilities aligned to self.labels."""
        if self.mode == "head":
            proba = self.head.predict_proba(embeddings)
            # sklearn orders columns by self.head.classes_; realign to LABELS order.
            order = [list(self.head.classes_).index(lbl) for lbl in self.labels]
            return proba[:, order]
        # prototype: cosine similarity (embeddings already unit-norm) -> softmax
        sims = embeddings @ self.prototypes.T  # (n, n_labels)
        exp = np.exp((sims - sims.max(axis=1, keepdims=True)) * 8.0)  # temperature 1/8
        return exp / exp.sum(axis=1, keepdims=True)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump(self, f)

    @staticmethod
    def load(path: Path) -> "Classifier":
        with open(path, "rb") as f:
            return pickle.load(f)


# --------------------------------------------------------------------------- #
# Training
# --------------------------------------------------------------------------- #
def load_store(path: Path) -> tuple[list[str], list[str]]:
    texts, labels = [], []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            lbl = row["label"].strip()
            if lbl not in LABELS:
                continue
            texts.append(row["text"].strip())
            labels.append(lbl)
    return texts, labels


def train(store_path: Path, embedder: Embedder, mode: str, model_path: Path) -> str:
    """Build the classifier from the whole store and persist it. Returns a report string."""
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import cross_val_predict
    from sklearn.metrics import classification_report

    texts, labels = load_store(store_path)
    if not texts:
        raise ValueError(f"No labeled rows in {store_path}")
    X = embedder.encode(texts)
    y = np.array(labels)

    model_name = getattr(embedder, "model_name", "unknown")

    if mode == "prototype":
        protos = np.stack([X[y == lbl].mean(axis=0) for lbl in LABELS])
        protos /= np.linalg.norm(protos, axis=1, keepdims=True) + 1e-9
        clf = Classifier("prototype", LABELS, model_name, prototypes=protos.astype(np.float32))
        report = "prototype mode — no cross-validation (cosine to class means)."
    else:
        head = LogisticRegression(max_iter=2000, C=10.0, class_weight="balanced")
        # Cross-validated sanity report (folds capped by smallest class size).
        min_class = min((y == lbl).sum() for lbl in LABELS)
        folds = max(2, min(5, int(min_class)))
        try:
            preds = cross_val_predict(head, X, y, cv=folds)
            report = f"{folds}-fold CV:\n" + classification_report(y, preds, digits=3, zero_division=0)
        except Exception as e:  # pragma: no cover - tiny data edge cases
            report = f"(cross-val skipped: {e})"
        head.fit(X, y)
        clf = Classifier("head", LABELS, model_name, head=head)

    clf.save(model_path)
    return (
        f"Trained {mode} classifier on {len(texts)} examples "
        f"({', '.join(f'{lbl}={sum(1 for l in labels if l == lbl)}' for lbl in LABELS)}).\n"
        f"Embedding model: {model_name}\nSaved: {model_path}\n\n{report}"
    )


# --------------------------------------------------------------------------- #
# Inference
# --------------------------------------------------------------------------- #
@dataclass
class Candidate:
    meta: object            # gmail.EmailMeta
    update_prob: float


@dataclass
class Stage1Result:
    applied_count: int
    update_candidates: list[Candidate]
    ignored: int


def run_stage1(metas, embedder, clf: Classifier, thresholds: dict, decision_log=None) -> Stage1Result:
    """Classify a batch of EmailMeta into applied count + update candidates.

    Rule per email:
      - P(update) >= update_candidate  -> update candidate (Stage 2 decides).
      - else argmax == 'applied'        -> counted, never sent to Claude.
      - else                            -> ignored (never costs a token).
    """
    if not metas:
        return Stage1Result(0, [], 0)

    lbl_idx = {lbl: i for i, lbl in enumerate(clf.labels)}
    X = embedder.encode([m.text for m in metas])
    proba = clf.predict_proba(X)

    applied = 0
    candidates: list[Candidate] = []
    ignored = 0
    up_thresh = float(thresholds.get("update_candidate", 0.45))
    conf_thresh = float(thresholds.get("applied_confident", 0.80))

    for m, p in zip(metas, proba):
        p_update = float(p[lbl_idx["update"]])
        argmax_lbl = clf.labels[int(np.argmax(p))]
        top = float(np.max(p))

        if p_update >= up_thresh:
            decision = "update"
            candidates.append(Candidate(m, p_update))
        elif argmax_lbl == "applied":
            decision = "applied"
            applied += 1
        else:
            decision = "ignore"
            ignored += 1

        if decision_log is not None:
            decision_log.record(
                stage="stage1",
                id=m.id,
                sender=m.sender,
                subject=m.subject[:120],
                decision=decision,
                p_applied=round(float(p[lbl_idx["applied"]]), 3),
                p_update=round(p_update, 3),
                p_ignore=round(float(p[lbl_idx["ignore"]]), 3),
                near_threshold=bool(top < conf_thresh),
                link=f"https://mail.google.com/mail/u/0/#all/{m.id}",
            )

    return Stage1Result(applied, candidates, ignored)
