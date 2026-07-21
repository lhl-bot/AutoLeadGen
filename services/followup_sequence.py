"""Helpers for resolving a workflow's configurable cold follow-up sequence.

A workflow may define `followup_steps` as an ordered list of
``{"day_offset": int, "instruction": str | None}``. Step index 0 is the first
follow-up (round 1), which is sent ``day_offset`` days after the initial cold
email; step index 1 is round 2, sent ``day_offset`` days after round 1; etc.

When a workflow has no sequence configured, callers fall back to the legacy
behaviour: ``max_followups`` rounds spaced by a single global interval.
"""
from datetime import datetime, timedelta
from typing import Any, List, Optional

MAX_STEPS = 6
MIN_DAY_OFFSET = 1
MAX_DAY_OFFSET = 90


def parse_steps(raw: Any) -> List[dict]:
    """Normalise a raw ``followup_steps`` value (JSON column / dict / model) to a clean list.

    Invalid entries are skipped rather than raising, so a malformed stored value
    can never crash the background engine.
    """
    if not raw or not isinstance(raw, list):
        return []
    steps: List[dict] = []
    for item in raw[:MAX_STEPS]:
        if hasattr(item, "model_dump"):
            item = item.model_dump()
        if not isinstance(item, dict):
            continue
        try:
            day_offset = int(item.get("day_offset"))
        except (TypeError, ValueError):
            continue
        day_offset = max(MIN_DAY_OFFSET, min(MAX_DAY_OFFSET, day_offset))
        instruction = item.get("instruction")
        if isinstance(instruction, str):
            instruction = instruction.strip() or None
        else:
            instruction = None
        steps.append({"day_offset": day_offset, "instruction": instruction})
    return steps


def has_sequence(raw: Any) -> bool:
    return len(parse_steps(raw)) > 0


def effective_max_followups(raw: Any, fallback_max: int) -> int:
    """How many follow-up rounds are allowed: sequence length, or the legacy max."""
    steps = parse_steps(raw)
    return len(steps) if steps else max(0, int(fallback_max or 0))


def step_for_round(raw: Any, followup_round: int) -> Optional[dict]:
    """Return the configured step for a 1-indexed round, or None if not configured."""
    steps = parse_steps(raw)
    if not steps or followup_round < 1 or followup_round > len(steps):
        return None
    return steps[followup_round - 1]


def interval_hours_for_round(raw: Any, followup_round: int, default_hours: float) -> float:
    """Hours to wait before sending the given round's follow-up.

    Uses the configured step's ``day_offset`` when available, else the default.
    """
    step = step_for_round(raw, followup_round)
    if step is None:
        return float(default_hours)
    return float(step["day_offset"]) * 24.0


def instruction_for_round(raw: Any, followup_round: int) -> Optional[str]:
    step = step_for_round(raw, followup_round)
    return step["instruction"] if step else None


def due_at_for_round(
    last_sent_at: datetime,
    raw: Any,
    followup_round: int,
    default_hours: float,
    *,
    use_business_days: bool = True,
) -> datetime:
    """Return the earliest follow-up time, optionally counting weekdays only."""
    step = step_for_round(raw, followup_round)
    if not step or not use_business_days:
        return last_sent_at + timedelta(hours=interval_hours_for_round(raw, followup_round, default_hours))

    remaining = int(step["day_offset"])
    due_at = last_sent_at
    while remaining > 0:
        due_at += timedelta(days=1)
        if due_at.weekday() < 5:
            remaining -= 1
    return due_at
