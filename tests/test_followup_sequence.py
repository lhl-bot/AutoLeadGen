import pytest
from datetime import datetime, timezone
from pydantic import ValidationError

import schemas
from services import followup_sequence as fs


def test_parse_steps_normalises_and_clamps():
    raw = [
        {"day_offset": 1, "instruction": "  opener  "},
        {"day_offset": 200, "instruction": ""},   # clamp to 90, blank -> None
        {"day_offset": "bad"},                      # skipped
        {"day_offset": 0},                          # clamp up to 1
    ]
    steps = fs.parse_steps(raw)
    assert steps == [
        {"day_offset": 1, "instruction": "opener"},
        {"day_offset": 90, "instruction": None},
        {"day_offset": 1, "instruction": None},
    ]


def test_parse_steps_handles_garbage():
    assert fs.parse_steps(None) == []
    assert fs.parse_steps("nope") == []
    assert fs.parse_steps([]) == []


def test_effective_max_followups_prefers_sequence_length():
    raw = [{"day_offset": 1}, {"day_offset": 3}]
    assert fs.effective_max_followups(raw, 3) == 2
    assert fs.effective_max_followups(None, 3) == 3
    assert fs.effective_max_followups([], 5) == 5


def test_interval_hours_for_round_uses_day_offset():
    raw = [{"day_offset": 1}, {"day_offset": 3}, {"day_offset": 7}]
    assert fs.interval_hours_for_round(raw, 1, 72) == 24
    assert fs.interval_hours_for_round(raw, 2, 72) == 72  # 3 days
    assert fs.interval_hours_for_round(raw, 3, 72) == 168
    # round beyond the sequence falls back to the default
    assert fs.interval_hours_for_round(raw, 4, 99) == 99
    # no sequence -> default
    assert fs.interval_hours_for_round(None, 1, 50) == 50


def test_instruction_for_round():
    raw = [{"day_offset": 1, "instruction": "send case study"}]
    assert fs.instruction_for_round(raw, 1) == "send case study"
    assert fs.instruction_for_round(raw, 2) is None


def test_due_at_for_round_counts_business_days():
    friday = datetime(2026, 7, 10, 10, tzinfo=timezone.utc)
    due = fs.due_at_for_round(friday, [{"day_offset": 4}], 1, 72)
    assert due == datetime(2026, 7, 16, 10, tzinfo=timezone.utc)


def test_schema_rejects_out_of_range_day_offset():
    with pytest.raises(ValidationError):
        schemas.FollowupStep(day_offset=0)
    with pytest.raises(ValidationError):
        schemas.FollowupStep(day_offset=91)


def test_schema_caps_sequence_length_and_empties_to_none():
    # Empty list is coerced to None (falls back to legacy behaviour).
    wf = schemas.WorkflowCreate(
        name="W", search_keywords="k", target_positions="buyer", followup_steps=[],
    )
    assert wf.followup_steps is None

    with pytest.raises(ValidationError):
        schemas.WorkflowCreate(
            name="W", search_keywords="k", target_positions="buyer",
            followup_steps=[{"day_offset": 1}] * 7,
        )
