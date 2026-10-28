"""One ticket's current state and the actions a person may take on it.

The console shows only these actions; the endpoints that perform them check again,
so the browser never decides on its own that an action is allowed.
"""
from __future__ import annotations

# Ordered for display; also the allowed values of the `state` filter.
STATES = ("not_processed", "processing", "waiting_approval", "sent", "escalated",
          "held_budget", "failed")


def ticket_state(decision_status: str | None, approval_status: str | None, sent: bool,
                 job_status: str | None) -> str:
    """Derive one state from persisted records (decisions, approvals, outbox, jobs)."""
    if job_status in ("queued", "leased"):
        return "processing"
    if decision_status is None:
        return "failed" if job_status == "dead" else "not_processed"
    if decision_status in ("escalated", "held_budget", "failed"):
        return decision_status
    # pending_approval: the latest approval says where the human step is.
    if sent:
        return "sent"
    if approval_status == "pending":
        return "waiting_approval"
    return "processing"  # rejected once and being redrafted, or between steps


def allowed_actions(state: str) -> list[str]:
    return {
        "not_processed": ["process"],
        "held_budget": ["process"],   # re-process after the budget was raised
        "failed": ["process"],        # retry after the cause was fixed
        "waiting_approval": ["approve", "reject"],
        "escalated": ["view_escalation"],
    }.get(state, [])
