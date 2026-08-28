"""Notifications — a clean notify() interface, implemented for ntfy by default.

Stages never talk to ntfy directly; they hand Notification objects to a Notifier.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

import requests

# Simple, hype one-liners for the applications-sent count.
HYPE_LINES = [
    "Applications out the door. Keep swinging! 🚀",
    "You're putting in the work — it compounds. 💪",
    "Every application is a lottery ticket. Buy more. 🎟️",
    "Shipped more apps today. Momentum is everything. 🔥",
    "Future you is grateful. Keep going! ⚡",
    "That's how it's done. On to the next. 🎯",
    "Hustle logged. The grind pays off. 💥",
    "More lines in the water. Something will bite. 🎣",
    "Consistency beats luck. You're stacking both. 📈",
    "Big moves today. Stay relentless. 🏆",
]


def hype_line() -> str:
    return random.choice(HYPE_LINES)


@dataclass
class Notification:
    title: str
    body: str
    priority: int = 3          # ntfy 1(min)..5(max)
    tags: list[str] = field(default_factory=list)
    click_url: str | None = None


class Notifier:
    """Interface. Implementations send a Notification somewhere."""

    def send(self, n: Notification) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class NtfyNotifier(Notifier):
    """Publishes via ntfy's JSON endpoint so titles/bodies can carry any Unicode
    (HTTP headers are latin-1 only, which breaks em dashes/emoji in the Title header)."""

    def __init__(self, server: str, topic: str):
        self.server = server.rstrip("/")
        self.topic = topic

    def send(self, n: Notification) -> None:
        payload = {
            "topic": self.topic,
            "title": n.title,
            "message": n.body,
            "priority": n.priority,
        }
        if n.tags:
            payload["tags"] = n.tags
        if n.click_url:
            payload["click"] = n.click_url
        resp = requests.post(self.server, json=payload, timeout=15)
        resp.raise_for_status()


class ConsoleNotifier(Notifier):
    """Used in dry-run and when no ntfy topic is configured — prints instead of sending."""

    def send(self, n: Notification) -> None:
        print(f"[notify] ({n.priority}) {n.title}\n         {n.body}"
              + (f"\n         → {n.click_url}" if n.click_url else ""))


# --------------------------------------------------------------------------- #
# Builders — turn pipeline results into Notifications
# --------------------------------------------------------------------------- #
def count_notification(applied_count: int, approx: bool = False) -> Notification:
    """approx=True prefixes the number with '~' — used when duplicate confirmations
    were collapsed, so the count is a best-effort estimate rather than exact."""
    n = f"~{applied_count}" if approx else str(applied_count)
    return Notification(
        title=f"Application confirmations received: {n}",
        body=hype_line(),
        priority=3,
        tags=["briefcase"],
    )


def update_notification(update) -> Notification:
    """update: claude.ConfirmedUpdate"""
    from .state import gmail_link

    label = update.update_type.upper() if update.update_type in ("oa",) else update.update_type.capitalize()
    return Notification(
        title=f"{label} — {update.company}",
        body=update.summary or f"{update.update_type} from {update.company}",
        priority=update.ntfy_priority,
        tags=["tada"] if update.update_type in ("offer", "interview") else ["bell"],
        click_url=gmail_link(update.msg_id),
    )
