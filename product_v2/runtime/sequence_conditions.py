"""Deterministic, fail-closed evaluation for SequenceStep controls.

The JSON definitions on a published revision are data, not executable code.
Only the small vocabulary below is supported.  Unknown keys and malformed
values are reported as invalid so the outbound runtime can block the sequence
instead of accidentally sending.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any, Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from product_v2 import models
from product_v2.enums import (
    AttemptStatus,
    MessageDirection,
    MessageEventType,
    ReplyAssessmentStatus,
    RestrictionScope,
)


@dataclass(frozen=True)
class SequenceControlResult:
    matched: bool
    invalid_reason: Optional[str] = None
    matched_rules: tuple[str, ...] = ()

    @property
    def valid(self) -> bool:
        return self.invalid_reason is None


_EXECUTION_KEYS = frozenset(
    {
        "always",
        "fake_only",
        "previous_attempt_status",
        "has_reply",
        "has_positive_reply",
        "contact_timezone_present",
    }
)
_STOP_KEYS = frozenset(
    {
        "stop_on_reply",
        "stop_on_positive_reply",
        "stop_on_unsubscribe",
        "stop_on_bounce",
        "stop_on_attempt_status",
    }
)


def _enabled(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _configured_fake_only() -> bool:
    return (
        os.environ.get("AUTOLEADGEN_CONNECTOR_MODE", "fake").strip().lower() != "real"
        or not _enabled("ALLOW_REAL_EXTERNAL_CALLS", False)
    )


def _status_values(value: Any, *, key: str) -> tuple[Optional[set[str]], Optional[str]]:
    raw_values = [value] if isinstance(value, str) else value
    if not isinstance(raw_values, list) or not raw_values or not all(isinstance(item, str) for item in raw_values):
        return None, f"{key}_must_be_status_or_non_empty_status_list"
    allowed = {status.value for status in AttemptStatus}
    values = set(raw_values)
    unknown = sorted(values - allowed)
    if unknown:
        return None, f"{key}_has_unknown_status:{','.join(unknown)}"
    return values, None


def _previous_attempt_status(
    db: Session,
    *,
    enrollment: models.Enrollment,
    step: models.SequenceStep,
) -> Optional[str]:
    row = (
        db.query(models.OutreachAttempt.status)
        .join(models.SequenceStep, models.SequenceStep.id == models.OutreachAttempt.sequence_step_id)
        .filter(
            models.OutreachAttempt.enrollment_id == enrollment.id,
            models.SequenceStep.position < step.position,
        )
        .order_by(models.SequenceStep.position.desc(), models.OutreachAttempt.id.desc())
        .first()
    )
    if not row:
        return None
    status = row[0]
    return status.value if hasattr(status, "value") else str(status)


def _step_attempt_status(
    db: Session,
    *,
    enrollment: models.Enrollment,
    step: models.SequenceStep,
) -> Optional[str]:
    row = (
        db.query(models.OutreachAttempt.status)
        .filter(
            models.OutreachAttempt.enrollment_id == enrollment.id,
            models.OutreachAttempt.sequence_step_id == step.id,
        )
        .order_by(models.OutreachAttempt.id.desc())
        .first()
    )
    if not row:
        return None
    status = row[0]
    return status.value if hasattr(status, "value") else str(status)


def _contact_message_exists(
    db: Session,
    *,
    enrollment: models.Enrollment,
    event_types: tuple[MessageEventType, ...] = (),
    inbound: bool = False,
) -> bool:
    query = (
        db.query(models.MessageEvent.id)
        .outerjoin(models.Conversation, models.Conversation.id == models.MessageEvent.conversation_id)
        .outerjoin(models.OutreachAttempt, models.OutreachAttempt.id == models.MessageEvent.outreach_attempt_id)
        .filter(
            models.MessageEvent.owner_id == enrollment.owner_id,
            or_(
                models.Conversation.contact_id == enrollment.contact_id,
                models.OutreachAttempt.enrollment_id == enrollment.id,
            ),
        )
    )
    signal_filters = []
    if inbound:
        signal_filters.append(models.MessageEvent.direction == MessageDirection.INBOUND)
    if event_types:
        signal_filters.append(models.MessageEvent.event_type.in_(event_types))
    if signal_filters:
        # Signal dimensions are cumulative: a reply must be inbound *and* have
        # a reply/unsubscribe event type.  Using OR here would let an outbound
        # provider event masquerade as a contact reply.
        query = query.filter(*signal_filters)
    return query.first() is not None


def _has_reply(db: Session, enrollment: models.Enrollment) -> bool:
    return _contact_message_exists(
        db,
        enrollment=enrollment,
        event_types=(MessageEventType.REPLIED, MessageEventType.UNSUBSCRIBED),
        inbound=True,
    )


def _has_positive_reply(db: Session, enrollment: models.Enrollment) -> bool:
    return (
        db.query(models.ReplyAssessment.id)
        .join(models.Conversation, models.Conversation.id == models.ReplyAssessment.conversation_id)
        .filter(
            models.ReplyAssessment.owner_id == enrollment.owner_id,
            models.ReplyAssessment.status == ReplyAssessmentStatus.CONFIRMED,
            models.ReplyAssessment.is_positive.is_(True),
            or_(
                models.ReplyAssessment.enrollment_id == enrollment.id,
                models.Conversation.contact_id == enrollment.contact_id,
            ),
        )
        .first()
        is not None
    )


def _has_unsubscribe(db: Session, enrollment: models.Enrollment) -> bool:
    point_ids = db.query(models.ContactPoint.id).filter(
        models.ContactPoint.contact_id == enrollment.contact_id,
        models.ContactPoint.archived_at.is_(None),
    )
    return (
        db.query(models.ConsentRestriction.id)
        .filter(
            models.ConsentRestriction.owner_id == enrollment.owner_id,
            models.ConsentRestriction.active.is_(True),
            or_(
                models.ConsentRestriction.scope == RestrictionScope.GLOBAL,
                models.ConsentRestriction.company_id == enrollment.company_id,
                models.ConsentRestriction.contact_id == enrollment.contact_id,
                models.ConsentRestriction.contact_point_id.in_(point_ids),
            ),
        )
        .first()
        is not None
    )


def evaluate_execution_conditions(
    db: Session,
    *,
    enrollment: models.Enrollment,
    step: models.SequenceStep,
    connector_is_fake: Optional[bool] = None,
) -> SequenceControlResult:
    """Return ``matched=True`` only when every declared condition is true."""

    definition = step.condition_definition or {}
    if not isinstance(definition, dict):
        return SequenceControlResult(False, "conditions_must_be_an_object")
    unknown = sorted(set(definition) - _EXECUTION_KEYS)
    if unknown:
        return SequenceControlResult(False, f"unknown_execution_conditions:{','.join(unknown)}")

    matched_rules: list[str] = []
    for key, expected in definition.items():
        if key in {"always", "fake_only", "has_reply", "has_positive_reply", "contact_timezone_present"}:
            if not isinstance(expected, bool):
                return SequenceControlResult(False, f"{key}_must_be_boolean")
        if key == "always":
            actual = True
        elif key == "fake_only":
            # ``false`` disables this safety-only constraint; it does not
            # require a real connector.
            actual = True if not expected else (
                connector_is_fake if connector_is_fake is not None else _configured_fake_only()
            )
            expected = True
        elif key == "previous_attempt_status":
            statuses, error = _status_values(expected, key=key)
            if error:
                return SequenceControlResult(False, error)
            actual = _previous_attempt_status(db, enrollment=enrollment, step=step) in statuses
            expected = True
        elif key == "has_reply":
            actual = _has_reply(db, enrollment)
        elif key == "has_positive_reply":
            actual = _has_positive_reply(db, enrollment)
        elif key == "contact_timezone_present":
            contact = db.get(models.Contact, enrollment.contact_id)
            actual = bool(contact and contact.timezone)
        else:  # pragma: no cover - guarded by the vocabulary check above
            return SequenceControlResult(False, f"unknown_execution_condition:{key}")

        if actual != expected:
            return SequenceControlResult(False, matched_rules=tuple(matched_rules))
        matched_rules.append(key)

    return SequenceControlResult(True, matched_rules=tuple(matched_rules))


def evaluate_stop_conditions(
    db: Session,
    *,
    enrollment: models.Enrollment,
    step: models.SequenceStep,
) -> SequenceControlResult:
    """Return ``matched=True`` when any enabled stop rule has fired."""

    definition = step.stop_condition_definition or {}
    if not isinstance(definition, dict):
        return SequenceControlResult(False, "stop_conditions_must_be_an_object")
    unknown = sorted(set(definition) - _STOP_KEYS)
    if unknown:
        return SequenceControlResult(False, f"unknown_stop_conditions:{','.join(unknown)}")

    matched_rules: list[str] = []
    for key, enabled in definition.items():
        if key == "stop_on_attempt_status":
            statuses, error = _status_values(enabled, key=key)
            if error:
                return SequenceControlResult(False, error)
            triggered = _step_attempt_status(db, enrollment=enrollment, step=step) in statuses
        else:
            if not isinstance(enabled, bool):
                return SequenceControlResult(False, f"{key}_must_be_boolean")
            if not enabled:
                continue
            if key == "stop_on_reply":
                triggered = _has_reply(db, enrollment)
            elif key == "stop_on_positive_reply":
                triggered = _has_positive_reply(db, enrollment)
            elif key == "stop_on_unsubscribe":
                triggered = _has_unsubscribe(db, enrollment)
            elif key == "stop_on_bounce":
                triggered = _contact_message_exists(
                    db,
                    enrollment=enrollment,
                    event_types=(MessageEventType.BOUNCED,),
                )
            else:  # pragma: no cover - guarded by the vocabulary check above
                return SequenceControlResult(False, f"unknown_stop_condition:{key}")
        if triggered:
            matched_rules.append(key)

    return SequenceControlResult(bool(matched_rules), matched_rules=tuple(matched_rules))
