"""Human approval service (C5), shared by the CLI and the API.

State machine for one approval row:  pending -> approved | rejected   (terminal)

approve: lock the row, mark approved + audit (one transaction), then resume the paused
  LangGraph thread, whose write_approved_outbox node inserts the outbox row exactly once
  (unique ticket + idempotency key). Repeating approve is a no-op that reports the
  existing outcome. If the process dies between the two steps, `reconcile()` resumes it.
reject: requires a reason; resumes the thread, which redrafts once, re-runs the checker
  and asks for approval again (attempt 2). A second rejection escalates to a case.
"""
from __future__ import annotations

from dataclasses import dataclass

from relay import store
from relay.db import tenant_ids, tenant_tx
from relay.orchestrator import Orchestrator


class ApprovalError(Exception):
    pass


@dataclass
class ApprovalOutcome:
    approval_id: str
    status: str
    changed: bool          # False when the call was an idempotent repeat
    thread_phase: str
    outbox_written: bool


def find(approval_id: str, tenants: frozenset[str] | None = None) -> dict | None:
    for tenant in sorted(tenants or tenant_ids()):
        with tenant_tx(tenant) as conn:
            row = conn.execute(
                "SELECT * FROM approvals WHERE approval_id = %s", (approval_id,)
            ).fetchone()
        if row:
            return row
    return None


def list_approvals(tenants: frozenset[str] | None = None, status: str | None = "pending") -> list[dict]:
    rows: list[dict] = []
    for tenant in sorted(tenants or tenant_ids()):
        with tenant_tx(tenant) as conn:
            rows += conn.execute(
                """SELECT a.*, d.category, d.priority, d.injection_detected,
                          (o.ticket_id IS NOT NULL) AS outbox_written
                   FROM approvals a
                   LEFT JOIN decisions d ON d.ticket_id = a.ticket_id
                   LEFT JOIN outbox o ON o.ticket_id = a.ticket_id AND o.approval_id = a.approval_id
                   WHERE (%(s)s::text IS NULL OR a.status = %(s)s::text)
                   ORDER BY a.created_at""",
                {"s": status},
            ).fetchall()
    return rows


def _decide(orch: Orchestrator, approval_id: str, actor: str, decision: str,
            reason: str | None, tenants: frozenset[str] | None) -> ApprovalOutcome:
    if not actor or not actor.strip():
        raise ApprovalError("an approver identity is required")
    if decision == "rejected" and not (reason and reason.strip()):
        raise ApprovalError("a rejection requires a reason")
    row = find(approval_id, tenants)
    if row is None:
        raise ApprovalError(f"approval {approval_id} not found")
    tenant = row["tenant_id"]

    with tenant_tx(tenant) as conn:
        locked = conn.execute(
            "SELECT * FROM approvals WHERE approval_id = %s FOR UPDATE", (approval_id,)
        ).fetchone()
        if locked["status"] != "pending":
            if locked["status"] != decision:
                raise ApprovalError(
                    f"approval {approval_id} is already {locked['status']}; cannot mark {decision}")
            changed = False  # idempotent repeat of the same decision
        else:
            conn.execute(
                """UPDATE approvals SET status = %s, decided_by = %s, decided_at = now(),
                       reason = %s WHERE approval_id = %s""",
                (decision, actor.strip(), reason.strip() if reason else None, approval_id),
            )
            store.audit(conn, tenant, ticket_id=row["ticket_id"], thread_id=row["thread_id"],
                        actor=actor.strip(), action=f"approval_{decision}", from_state="pending",
                        to_state=decision, reason=reason,
                        details={"approval_id": approval_id, "attempt": row["attempt"]})
            changed = True

    phase = orch.resume_with_decision(tenant, row["thread_id"], {
        "approval_id": approval_id, "decision": decision, "actor": actor.strip(), "reason": reason,
    })
    with tenant_tx(tenant) as conn:
        written = conn.execute(
            "SELECT 1 FROM outbox WHERE approval_id = %s", (approval_id,)
        ).fetchone() is not None
    return ApprovalOutcome(approval_id, decision, changed, phase, written)


def approve(orch: Orchestrator, approval_id: str, actor: str,
            tenants: frozenset[str] | None = None) -> ApprovalOutcome:
    return _decide(orch, approval_id, actor, "approved", None, tenants)


def reject(orch: Orchestrator, approval_id: str, actor: str, reason: str,
           tenants: frozenset[str] | None = None) -> ApprovalOutcome:
    return _decide(orch, approval_id, actor, "rejected", reason, tenants)


def reconcile(orch: Orchestrator) -> list[str]:
    """Resume threads whose approval was decided but whose graph never resumed
    (e.g. the process died between the DB commit and the resume)."""
    resumed = []
    for tenant in sorted(tenant_ids()):
        with tenant_tx(tenant) as conn:
            rows = conn.execute(
                """SELECT a.* FROM approvals a JOIN workflows w ON w.thread_id = a.thread_id
                   WHERE a.status IN ('approved','rejected') AND w.status = 'awaiting_approval'"""
            ).fetchall()
        for r in rows:
            orch.resume_with_decision(tenant, r["thread_id"], {
                "approval_id": r["approval_id"], "decision": r["status"],
                "actor": r["decided_by"], "reason": r["reason"],
            })
            resumed.append(r["approval_id"])
    return resumed
