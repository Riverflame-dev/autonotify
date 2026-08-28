"""Config loading, checkpointing, idempotency, and observability.

Everything cross-cutting lives here so the three stages stay small and injectable.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (no dependency on python-dotenv). Sets os.environ."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


class Config:
    """Thin wrapper over config.yaml + .env with path resolution."""

    def __init__(self, root: Path = REPO_ROOT):
        self.root = root
        _load_dotenv(root / ".env")
        with open(root / "config.yaml") as f:
            self._raw: dict[str, Any] = yaml.safe_load(f)

    # dotted getter, e.g. cfg.get("thresholds.applied_confident")
    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self._raw
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def path(self, key: str) -> Path:
        """Resolve a path from the `paths:` section against the repo root."""
        rel = self.get(f"paths.{key}")
        if rel is None:
            raise KeyError(f"paths.{key} not in config.yaml")
        p = self.root / rel
        return p

    @property
    def ntfy_topic(self) -> str | None:
        return os.environ.get("NTFY_TOPIC") or None

    @property
    def anthropic_key(self) -> str | None:
        return os.environ.get("ANTHROPIC_API_KEY") or None


# --------------------------------------------------------------------------- #
# Checkpoint (timestamp-first, advances only on success)
# --------------------------------------------------------------------------- #
class Checkpoint:
    def __init__(self, path: Path):
        self.path = path

    def last_success(self) -> datetime | None:
        if not self.path.exists():
            return None
        data = json.loads(self.path.read_text())
        ts = data.get("last_success")
        return datetime.fromisoformat(ts) if ts else None

    def query_since(self, overlap_minutes: int, first_run_lookback_hours: int) -> datetime:
        """The lower bound of the pull window: last success minus overlap,
        or a first-run lookback if there is no checkpoint yet."""
        last = self.last_success()
        if last is None:
            return datetime.now(timezone.utc) - timedelta(hours=first_run_lookback_hours)
        return last - timedelta(minutes=overlap_minutes)

    def mark_success(self, when: datetime | None = None) -> None:
        when = when or datetime.now(timezone.utc)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"last_success": when.isoformat()}))


# --------------------------------------------------------------------------- #
# Processed-id set (idempotency — never count/notify the same email twice)
# --------------------------------------------------------------------------- #
class ProcessedIds:
    def __init__(self, path: Path):
        self.path = path
        self._ids: set[str] = set()
        if path.exists():
            self._ids = {ln.strip() for ln in path.read_text().splitlines() if ln.strip()}

    def __contains__(self, msg_id: str) -> bool:
        return msg_id in self._ids

    def add_many(self, ids: list[str]) -> None:
        new = [i for i in ids if i not in self._ids]
        if not new:
            return
        self._ids.update(new)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a") as f:
            for i in new:
                f.write(i + "\n")


# --------------------------------------------------------------------------- #
# Observability
# --------------------------------------------------------------------------- #
@dataclass
class RunReport:
    pulled: int = 0
    applied: int = 0
    update_candidates: int = 0
    ignored: int = 0
    sent_to_claude: int = 0
    confirmed: int = 0
    notified: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    def cost(self, price_in: float, price_out: float) -> float:
        return self.tokens_in / 1e6 * price_in + self.tokens_out / 1e6 * price_out

    def render(self, price_in: float = 1.0, price_out: float = 5.0) -> str:
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        lines = [
            f"[{ts}] run report",
            f"  pulled           : {self.pulled}",
            f"  applied (count)  : {self.applied}",
            f"  update candidates: {self.update_candidates}",
            f"  ignored          : {self.ignored}",
            f"  sent to Claude   : {self.sent_to_claude}",
            f"  confirmed updates: {self.confirmed}",
            f"  notified         : {self.notified}",
            f"  Claude tokens    : {self.tokens_in} in / {self.tokens_out} out"
            f"  (~${self.cost(price_in, price_out):.4f} this run)",
        ]
        for k, v in self.extra.items():
            lines.append(f"  {k}: {v}")
        return "\n".join(lines)

    def write(self, path: Path, price_in: float, price_out: float) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            f.write(self.render(price_in, price_out) + "\n\n")


class DecisionLog:
    """Append-only JSONL of every decision, so you can skim for false pos/neg."""

    def __init__(self, path: Path):
        self.path = path

    def record(self, **fields: Any) -> None:
        fields["ts"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a") as f:
            f.write(json.dumps(fields, ensure_ascii=False) + "\n")


def gmail_link(msg_id: str) -> str:
    """Deep-link to a message in the Gmail web UI (account index 0)."""
    return f"https://mail.google.com/mail/u/0/#all/{msg_id}"
