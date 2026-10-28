"""Tenant-bound business persistence used by graph nodes and services.

Every function opens its own `tenant_tx`, so RLS scopes it to exactly one tenant.
Writes that must happen at most once rely on unique constraints + ON CONFLICT.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone

from psycopg.types.json import Jsonb

from relay.contracts import DecisionRow, Ticket
from relay.db import platform_tx, tenant_tx


def now() -> datetime:
    return datetime.now(timezone.utc)


def content_hash(ticket: Ticket) -> str:
    return hashlib.sha256(f"{ticket.subject}\x00{ticket.body}".encode()).hexdigest()[:16]


# ------------------------------------------------------------------ tickets / runs

def upsert_ticket(ticket: Ticket) -> None:
    with tenant_tx(ticket.tenant) as conn:
        conn.execute(
            """INSERT INTO tickets (tenant_id, ticket_id, customer_ref, subject, body,
                   received_at, content_hash)
               VALUES (%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (tenant_id, ticket_id) DO NOTHING""",
            (ticket.tenant, ticket.id, ticket.customer_ref, ticket.subject, ticket.body,
             ticket.received_at, content_hash(ticket)),
        )


def load_ticket(tenant_id: str, ticket_id: str) -> Ticket | None:
    with tenant_tx(tenant_id) as conn:
        r = conn.execute(
            "SELECT * FROM tickets WHERE ticket_id = %s", (ticket_id,)
        ).fetchone()
    if r is None:
        return None
    return Ticket(id=r["ticket_id"], tenant=r["tenant_id"], customer_ref=r["customer_ref"],
                  subject=r["subject"], body=r["body"], received_at=r["received_at"])


def create_run(run_id: str, kind: str, source: str | None, token_budget: int) -> None:
    with platform_tx() as conn:
        conn.execute(
            "INSERT INTO runs (run_id, kind, source, token_budget) VALUES (%s,%s,%s,%s) "
            "ON CONFLICT (run_id) DO NOTHING",
            (run_id, kind, source, token_budget),
        )


def finish_run(run_id: str) -> None:
    with platform_tx() as conn:
        conn.execute("UPDATE runs SET finished_at = now() WHERE run_id = %s", (run_id,))


def new_run_id() -> str:
    return f"{now().strftime('%Y-%m-%dT%H:%M:%SZ')}-{uuid.uuid4().hex[:4]}"


def log_rejection(run_id: str | None, raw_tenant: str | None, ticket_id: str | None,
                  reason: str) -> None:
    with platform_tx() as conn:
        conn.execute(
            "INSERT INTO rejections (run_id, raw_tenant, ticket_id, reason) VALUES (%s,%s,%s,%s)",
            (run_id, raw_tenant, ticket_id, reason),
        )


# ------------------------------------------------------------------ workflows / events / audit

def create_workflow(thread_id: str, tenant_id: str, ticket_id: str, run_id: str) -> None:
    with tenant_tx(tenant_id) as conn:
        conn.execute(
            """INSERT INTO workflows (thread_id, tenant_id, ticket_id, run_id, status)
               VALUES (%s,%s,%s,%s,'running') ON CONFLICT (thread_id) DO NOTHING""",
            (thread_id, tenant_id, ticket_id, run_id),
        )


def update_workflow(tenant_id: str, thread_id: str, status: str | None = None,
                    node: str | None = None) -> None:
    with tenant_tx(tenant_id) as conn:
        conn.execute(
            """UPDATE workflows SET status = COALESCE(%s, status),
                   current_node = COALESCE(%s, current_node), updated_at = now()
               WHERE thread_id = %s""",
            (status, node, thread_id),
        )


def emit_event(tenant_id: str, *, run_id: str | None, thread_id: str | None,
               ticket_id: str | None, event_type: str, node: str | None = None,
               status: str | None = None, duration_ms: int | None = None,
               metadata: dict | None = None) -> None:
    with tenant_tx(tenant_id) as conn:
        conn.execute(
            """INSERT INTO events (tenant_id, run_id, thread_id, ticket_id, event_type, node,
                   status, duration_ms, metadata)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (tenant_id, run_id, thread_id, ticket_id, event_type, node, status, duration_ms,
             Jsonb(metadata or {})),
        )


def audit(conn, tenant_id: str, *, ticket_id: str | None, thread_id: str | None, actor: str,
          action: str, from_state: str | None, to_state: str | None,
          reason: str | None = None, details: dict | None = None) -> None:
    """Audit inside the caller's transaction so the state change and its audit commit together."""
    conn.execute(
        """INSERT INTO audit_log (tenant_id, ticket_id, thread_id, actor, action, from_state,
               to_state, reason, details)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (tenant_id, ticket_id, thread_id, actor, action, from_state, to_state, reason,
         Jsonb(details or {})),
    )


# ------------------------------------------------------------------ decisions

def upsert_decision(row: DecisionRow, thread_id: str, reason_code: str | None,
                    actor: str = "lead-agent") -> None:
    with tenant_tx(row.tenant) as conn:
        prev = conn.execute(
            "SELECT status FROM decisions WHERE ticket_id = %s FOR UPDATE", (row.ticket_id,)
        ).fetchone()
        conn.execute(
            """INSERT INTO decisions (tenant_id, ticket_id, run_id, thread_id, category, priority,
                   action, injection_detected, kb_refs, draft_reply, status, model, tokens_in,
                   tokens_out, latency_ms, reason_code)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (tenant_id, ticket_id) DO UPDATE SET
                   run_id = EXCLUDED.run_id, thread_id = EXCLUDED.thread_id,
                   category = EXCLUDED.category, priority = EXCLUDED.priority,
                   action = EXCLUDED.action, injection_detected = EXCLUDED.injection_detected,
                   kb_refs = EXCLUDED.kb_refs, draft_reply = EXCLUDED.draft_reply,
                   status = EXCLUDED.status, model = EXCLUDED.model,
                   tokens_in = EXCLUDED.tokens_in, tokens_out = EXCLUDED.tokens_out,
                   latency_ms = EXCLUDED.latency_ms, reason_code = EXCLUDED.reason_code,
                   updated_at = now()""",
            (row.tenant, row.ticket_id, row.run_id, thread_id, row.category.value,
             row.priority.value, row.action.value, row.injection_detected, row.kb_refs,
             row.draft_reply, row.status.value, row.model, row.tokens_in, row.tokens_out,
             row.latency_ms, reason_code),
        )
        audit(conn, row.tenant, ticket_id=row.ticket_id, thread_id=thread_id, actor=actor,
              action="decision_recorded", from_state=prev["status"] if prev else None,
              to_state=row.status.value, reason=reason_code,
              details={"action": row.action.value, "category": row.category.value,
                       "priority": row.priority.value, "kb_refs": row.kb_refs})


def get_decision(tenant_id: str, ticket_id: str) -> dict | None:
    with tenant_tx(tenant_id) as conn:
        return conn.execute(
            "SELECT * FROM decisions WHERE ticket_id = %s", (ticket_id,)
        ).fetchone()


def decision_rows(tenant_id: str) -> list[DecisionRow]:
    with tenant_tx(tenant_id) as conn:
        rows = conn.execute("SELECT * FROM decisions ORDER BY ticket_id").fetchall()
    return [DecisionRow(
        ticket_id=r["ticket_id"], tenant=r["tenant_id"], run_id=r["run_id"],
        category=r["category"], priority=r["priority"], action=r["action"],
        injection_detected=r["injection_detected"], kb_refs=list(r["kb_refs"]),
        draft_reply=r["draft_reply"], status=r["status"], model=r["model"],
        tokens_in=r["tokens_in"], tokens_out=r["tokens_out"], latency_ms=r["latency_ms"],
    ) for r in rows]


# ------------------------------------------------------------------ approvals / outbox / cases

def ensure_approval(*, tenant_id: str, ticket_id: str, thread_id: str, attempt: int, kind: str,
                    draft_text: str, kb_refs: list[str], check_result: dict) -> tuple[dict, bool]:
    """Create the pending approval for (thread, attempt) once. Returns (row, created)."""
    approval_id = f"apr-{uuid.uuid5(uuid.NAMESPACE_URL, f'{thread_id}#{attempt}').hex[:12]}"
    with tenant_tx(tenant_id) as conn:
        created = conn.execute(
            """INSERT INTO approvals (approval_id, tenant_id, ticket_id, thread_id, attempt, kind,
                   draft_text, kb_refs, check_result)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (thread_id, attempt) DO NOTHING RETURNING approval_id""",
            (approval_id, tenant_id, ticket_id, thread_id, attempt, kind, draft_text, kb_refs,
             Jsonb(check_result)),
        ).fetchone() is not None
        row = conn.execute(
            "SELECT * FROM approvals WHERE thread_id = %s AND attempt = %s", (thread_id, attempt)
        ).fetchone()
        if created:
            audit(conn, tenant_id, ticket_id=ticket_id, thread_id=thread_id, actor="lead-agent",
                  action="approval_requested", from_state=None, to_state="pending",
                  details={"approval_id": row["approval_id"], "attempt": attempt, "kind": kind})
    return row, created


def outbox_key(tenant_id: str, ticket_id: str) -> str:
    """One customer message per ticket: the key depends on the ticket, not the attempt."""
    return hashlib.sha256(f"outbox:{tenant_id}:{ticket_id}".encode()).hexdigest()[:32]


def write_outbox(*, tenant_id: str, ticket_id: str, thread_id: str, approval_id: str) -> bool:
    """Insert the approved text exactly once. Returns True if this call wrote the row."""
    with tenant_tx(tenant_id) as conn:
        a = conn.execute(
            "SELECT * FROM approvals WHERE approval_id = %s", (approval_id,)
        ).fetchone()
        if a is None or a["status"] != "approved":
            raise PermissionError("outbox write refused: approval is not in state 'approved'")
        inserted = conn.execute(
            """INSERT INTO outbox (tenant_id, ticket_id, approval_id, text, approved_by,
                   approved_at, idempotency_key)
               VALUES (%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT DO NOTHING RETURNING ticket_id""",
            (tenant_id, ticket_id, approval_id, a["draft_text"], a["decided_by"], a["decided_at"],
             outbox_key(tenant_id, ticket_id)),
        ).fetchone() is not None
        if inserted:
            audit(conn, tenant_id, ticket_id=ticket_id, thread_id=thread_id, actor="outbox-writer",
                  action="outbox_written", from_state="approved", to_state="sent_to_outbox",
                  details={"approval_id": approval_id})
    return inserted


SEVERITY = {"urgent": "sev1", "high": "sev2", "normal": "sev3", "low": "sev4"}
OWNERS = {
    "injection": "security-review",
    "clinical": "on-duty-dentist",
    "legal_threat": "support-lead",
    "money_dispute": "billing-lead",
    "kb_not_covered": "support-lead",
    "checker_failed": "support-lead",
    "rejected_twice": "support-lead",
    "empty_ticket": "support-lead",
    "model_proposed_escalation": "support-lead",
    "dead_letter": "ops-oncall",
    "invalid_model_output": "ops-oncall",
}


def open_case(*, tenant_id: str, ticket_id: str, thread_id: str | None, kind: str,
              reason_code: str, reason: str, priority: str) -> str:
    case_id = f"case-{uuid.uuid5(uuid.NAMESPACE_URL, f'{tenant_id}:{ticket_id}:{kind}').hex[:10]}"
    with tenant_tx(tenant_id) as conn:
        created = conn.execute(
            """INSERT INTO cases (case_id, tenant_id, ticket_id, thread_id, kind, owner, severity,
                   reason_code, reason)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (tenant_id, ticket_id, kind) DO UPDATE
                   SET reason_code = EXCLUDED.reason_code, reason = EXCLUDED.reason,
                       severity = EXCLUDED.severity, owner = EXCLUDED.owner,
                       thread_id = EXCLUDED.thread_id
               RETURNING (xmax = 0) AS created""",
            (case_id, tenant_id, ticket_id, thread_id, kind,
             OWNERS.get(reason_code, "support-lead"), SEVERITY.get(priority, "sev3"),
             reason_code, reason),
        ).fetchone()["created"]
        audit(conn, tenant_id, ticket_id=ticket_id, thread_id=thread_id, actor="lead-agent",
              action=f"{kind}_case_opened" if created else f"{kind}_case_updated",
              from_state=None, to_state="open", reason=reason_code,
              details={"case_id": case_id})
    return case_id
