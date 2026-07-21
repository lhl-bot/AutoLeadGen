"""Record Provider abuse complaints as a first-class message event."""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import inspect, text


revision: str = "0006_message_event_complaint"
down_revision: str | None = "0005_outreach_templates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TABLE = "v2_message_events"
CONSTRAINT = "ck_v2_message_event_type"
OLD_VALUES = (
    "queued",
    "sent",
    "delivered",
    "opened",
    "replied",
    "bounced",
    "failed",
    "unsubscribed",
    "unknown",
)
NEW_VALUES = (*OLD_VALUES[:6], "complained", *OLD_VALUES[6:])


def _expression(values: tuple[str, ...]) -> str:
    allowed = ", ".join(f"'{value}'" for value in values)
    return f"event_type IN ({allowed})"


def _replace_check(values: tuple[str, ...]) -> None:
    bind = op.get_bind()
    checks = {
        item.get("name"): str(item.get("sqltext") or "").lower()
        for item in inspect(bind).get_check_constraints(TABLE)
    }
    desired_value = "complained" in values
    current = checks.get(CONSTRAINT, "")
    if current and (("complained" in current) == desired_value):
        return

    if bind.dialect.name == "sqlite":
        with op.batch_alter_table(TABLE, recreate="always") as batch:
            if CONSTRAINT in checks:
                batch.drop_constraint(CONSTRAINT, type_="check")
            batch.create_check_constraint(CONSTRAINT, _expression(values))
        return

    if CONSTRAINT in checks:
        op.drop_constraint(CONSTRAINT, TABLE, type_="check")
    op.create_check_constraint(CONSTRAINT, TABLE, _expression(values))


def upgrade() -> None:
    _replace_check(NEW_VALUES)


def downgrade() -> None:
    complained = op.get_bind().execute(
        text(f"SELECT COUNT(*) FROM {TABLE} WHERE event_type = 'complained'")
    ).scalar_one()
    if complained:
        raise RuntimeError("Cannot downgrade while complained message events exist")
    _replace_check(OLD_VALUES)
