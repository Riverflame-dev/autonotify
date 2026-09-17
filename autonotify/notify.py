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


# Shown instead of a bare "Rejection" line — a closed door, not a verdict on you.
SUPPORT_LINES = [
    "Their loss — they didn't get to see what you'd have built. Next. 💪",
    "Wrong room, not wrong you. The right one is still out there. 🚪",
    "They passed on someone good. That's on them. Keep going. ⚡",
    "One door shut. You've still got every bit of what got you the interview. 🔑",
    "Not a no to you — a no to a fit. Those are different things. 🌱",
    "They missed out. Someone else won't. On to the next. 🎯",
    "This one wasn't yours. Doesn't mean the next one isn't. 🧭",
    "Closed doors narrow the search. You're getting warmer. 🔥",
    "You put yourself out there again. That part never stops counting. 📈",
    "Their bar, their call, their loss. Your streak continues. 🏃",
]


def support_line() -> str:
    return random.choice(SUPPORT_LINES)


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

    # A rejection is reframed as an opening closing, and led with encouragement —
    # the factual summary still follows so you know which role it was.
    if update.update_type == "rejection":
        body = support_line()
        if update.summary:
            body = f"{body}\n{update.summary}"
        return Notification(
            title=f"Opening closed — {update.company}",
            body=body,
            priority=update.ntfy_priority,
            tags=["muscle"],
            click_url=gmail_link(update.msg_id),
        )

    label = update.update_type.upper() if update.update_type in ("oa",) else update.update_type.capitalize()
    return Notification(
        title=f"{label} — {update.company}",
        body=update.summary or f"{update.update_type} from {update.company}",
        priority=update.ntfy_priority,
        tags=["tada"] if update.update_type in ("offer", "interview") else ["bell"],
        click_url=gmail_link(update.msg_id),
    )
