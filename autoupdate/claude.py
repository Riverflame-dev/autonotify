"""Stage 2 — Claude tiebreaker (the only "paid" stage; tiny volume).

Backends:
  - "claude_cli" (default): shells out to `claude -p` using your personal Claude
    subscription (no API key). One-shot, JSON output, tools disabled.
  - "api": the official anthropic SDK (needs ANTHROPIC_API_KEY).

For each update candidate: an optional cheap subject-only pre-check, then a full
body read + a strict JSON verdict. Confirmed updates are deduped to one per company,
preferring the most advanced stage.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass

# Ranking used for per-company dedup and notification priority.
STAGE_RANK = {"offer": 6, "interview": 5, "OA": 4, "call": 3, "rejection": 2, "other": 1}
NTFY_PRIORITY = {"offer": 5, "interview": 4, "OA": 4, "call": 3, "rejection": 3, "other": 3}

_SYSTEM = (
    "You classify emails about the RECIPIENT'S OWN job applications. "
    "A real 'update' is genuine progress on a specific application the recipient submitted: "
    "an online assessment (OA), interview invitation, recruiter/phone call scheduling, an offer, "
    "a rejection, or a concrete status change. "
    "Marketing, newsletters, job recommendations, 'jobs you may like', LinkedIn social "
    "notifications, and generic 'we're hiring' blasts are NOT updates, even when they use words "
    "like 'congratulations', 'next steps', or 'we'd like to invite you'."
)

_VERDICT_INSTR = (
    "Respond with ONLY a JSON object (no markdown, no prose) with exactly these keys:\n"
    '  "is_real_update": boolean,\n'
    '  "company": string (the company name, or "Unknown"),\n'
    '  "update_type": one of "OA","interview","call","offer","rejection","other",\n'
    '  "summary": string (one short human sentence).'
)
_PRECHECK_INSTR = (
    'Could this plausibly be a real update on the recipient\'s OWN job application? '
    'Respond with ONLY {"plausible": true} or {"plausible": false}.'
)


@dataclass
class ConfirmedUpdate:
    company: str
    update_type: str
    summary: str
    msg_id: str

    @property
    def ntfy_priority(self) -> int:
        return NTFY_PRIORITY.get(self.update_type, 3)


# --------------------------------------------------------------------------- #
# JSON extraction (models sometimes wrap output in ```json fences or prose)
# --------------------------------------------------------------------------- #
def _extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Fall back to the first balanced {...} object.
    start = text.find("{")
    if start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(text[start : i + 1])
    raise ValueError(f"no JSON object in model output: {text[:200]!r}")


# --------------------------------------------------------------------------- #
# Runners — a runner is `run(prompt) -> (dict, tokens_in, tokens_out)`
# --------------------------------------------------------------------------- #
class ClaudeCliRunner:
    """One-shot `claude -p` calls on your personal subscription (no API key)."""

    def __init__(self, model: str | None, config_dir: str | None, timeout: int = 120):
        self.model = model
        self.config_dir = os.path.expanduser(config_dir) if config_dir else None
        self.timeout = timeout

    def __call__(self, prompt: str) -> tuple[dict, int, int]:
        env = os.environ.copy()
        if self.config_dir:
            env["CLAUDE_CONFIG_DIR"] = self.config_dir
        cmd = ["claude", "-p", prompt, "--output-format", "json", "--allowedTools", ""]
        if self.model:
            cmd += ["--model", self.model]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout, env=env)
        if proc.returncode != 0:
            raise RuntimeError(f"claude -p failed ({proc.returncode}): {proc.stderr[:300]}")
        envelope = json.loads(proc.stdout)
        usage = envelope.get("usage", {}) or {}
        data = _extract_json(envelope.get("result", ""))
        return data, int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))


class ApiRunner:
    """anthropic SDK backend (needs ANTHROPIC_API_KEY). Kept as an alternative."""

    def __init__(self, api_key: str, model: str):
        import anthropic

        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model

    def __call__(self, prompt: str) -> tuple[dict, int, int]:
        resp = self.client.messages.create(
            model=self.model, max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "{}")
        u = getattr(resp, "usage", None)
        tin = getattr(u, "input_tokens", 0) if u else 0
        tout = getattr(u, "output_tokens", 0) if u else 0
        return _extract_json(text), tin, tout


def build_runner(cfg):
    backend = cfg.get("stage2.backend", "claude_cli")
    model = cfg.get("stage2.model")
    if backend == "api":
        key = cfg.anthropic_key
        if not key:
            raise RuntimeError("stage2.backend=api but ANTHROPIC_API_KEY is not set")
        return ApiRunner(key, model)
    return ClaudeCliRunner(model, cfg.get("stage2.config_dir", "~/.claude-personal"))


# --------------------------------------------------------------------------- #
# Verify
# --------------------------------------------------------------------------- #
def verify(candidates, cfg, body_fetcher, decision_log=None, report=None, runner=None) -> list[ConfirmedUpdate]:
    """Confirm update candidates and dedup to one per company.

    `body_fetcher(msg_id) -> str` is injected (gmail.fetch_body in production).
    `runner` may be injected for tests; otherwise built from config.
    """
    if not candidates:
        return []
    if runner is None:
        runner = build_runner(cfg)
    precheck = bool(cfg.get("stage2.subject_only_precheck", True))

    best: dict[str, ConfirmedUpdate] = {}

    for cand in candidates:
        m = cand.meta
        link = f"https://mail.google.com/mail/u/0/#all/{m.id}"
        try:
            # Cheap subject-only pre-check (skips a body read on obvious non-updates).
            if precheck:
                pc, tin, tout = runner(
                    f"{_SYSTEM}\n\n{_PRECHECK_INSTR}\n\n"
                    f"From: {m.sender}\nSubject: {m.subject}\nSnippet: {m.snippet}"
                )
                _acc(report, tin, tout)
                if not pc.get("plausible", False):
                    if decision_log is not None:
                        decision_log.record(stage="stage2_precheck", id=m.id,
                                            subject=m.subject[:120], decision="drop", link=link)
                    continue

            body = body_fetcher(m.id)
            if report is not None:
                report.sent_to_claude += 1
            verdict, tin, tout = runner(
                f"{_SYSTEM}\n\n{_VERDICT_INSTR}\n\n"
                f"From: {m.sender}\nSubject: {m.subject}\n\nBody:\n{body}"
            )
            _acc(report, tin, tout)
        except Exception as e:
            if decision_log is not None:
                decision_log.record(stage="stage2", id=m.id, subject=m.subject[:120],
                                    decision="error", error=str(e)[:200], link=link)
            continue

        is_update = bool(verdict.get("is_real_update"))
        company = (verdict.get("company") or "").strip() or "Unknown"
        utype = verdict.get("update_type", "other")
        if utype not in STAGE_RANK:
            utype = "other"

        if decision_log is not None:
            decision_log.record(stage="stage2", id=m.id, subject=m.subject[:120],
                                decision="confirmed" if is_update else "drop", company=company,
                                update_type=utype, summary=(verdict.get("summary") or "")[:200], link=link)
        if not is_update:
            continue

        cu = ConfirmedUpdate(company, utype, (verdict.get("summary") or "").strip(), m.id)
        key = company.lower()
        if key not in best or STAGE_RANK[utype] > STAGE_RANK[best[key].update_type]:
            best[key] = cu

    confirmed = list(best.values())
    if report is not None:
        report.confirmed = len(confirmed)
    return confirmed


def _acc(report, tin, tout):
    if report is not None:
        report.tokens_in += tin
        report.tokens_out += tout
