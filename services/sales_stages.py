"""Canonical sales-pipeline stages (manual CRM layer over automation status)."""
from typing import Optional

# Ordered pipeline. Order matters for board column layout and "suggested" mapping.
SALES_STAGES = ["new", "contacted", "interested", "quoting", "won", "lost"]
SALES_STAGE_SET = set(SALES_STAGES)
DEFAULT_STAGE = "new"

# When a lead has never been moved manually (stage == "new"), suggest a stage from
# its automation status so the board isn't all stuck in the first column.
_STATUS_TO_STAGE = {
    "found": "new",
    "needs_email": "new",
    "low_score": "new",
    "invalid_email": "new",
    "drafted": "new",
    "sent": "contacted",
    "send_failed": "contacted",
    "replied": "interested",
    "bounced": "lost",
    "rejected": "lost",
    "unsubscribed": "lost",
}


def is_valid_stage(stage: Optional[str]) -> bool:
    return stage in SALES_STAGE_SET


def effective_stage(sales_stage: Optional[str], status: Optional[str]) -> str:
    """Resolve the stage to display.

    An explicit non-"new" stage always wins. Otherwise derive a sensible stage from
    the automation status so replied/sent leads surface in the right column.
    """
    if sales_stage and sales_stage in SALES_STAGE_SET and sales_stage != DEFAULT_STAGE:
        return sales_stage
    return _STATUS_TO_STAGE.get(status or "", DEFAULT_STAGE)
