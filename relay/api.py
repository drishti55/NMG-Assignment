"""FastAPI service for the operations console.

Auth (local development only, see docs/LIMITATIONS.md): every request carries
`Authorization: Bearer <token>`; RELAY_API_TOKENS maps tokens to an operator name and
the tenants that operator may see. Every read and write runs inside `tenant_tx` for an
authorized tenant, so RLS applies to the API exactly as to the worker. A tenant id from
the browser is only ever used after it is checked against the operator's grant.
"""
from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from relay import approvals as approvals_svc
from relay import outputs, store
from relay.config import get_settings
from relay.contracts import Ticket
from relay.db import UnknownTenantError, platform_tx, require_tenant, tenant_registry, tenant_tx
from relay.gateway import default_gateway
from relay.graph import Deps, build_graph

app = FastAPI(title="Project Relay API", version="1.0.0")

EVENT_FIELDS = ("event_id", "tenant_id", "run_id", "thread_id", "ticket_id", "event_type", "node",
                "status", "duration_ms", "metadata", "at")
_SSE = {"clients": 0, "delivered": 0}
_jobs_lock = threading.Lock()
_background: dict[str, str] = {}  # what the API process is running: {"eval": eval_id, ...}


# ------------------------------------------------------------------ auth

@dataclass(frozen=True)
class Operator:
    name: str
    tenants: frozenset[str]


def _token_map() -> dict[str, Operator]:
    out = {}
    for entry in filter(None, (e.strip() for e in get_settings().api_tokens.split(","))):
        token, _, spec = entry.partition("=")
        name, _, tenants = spec.partition("@")
        out[token.strip()] = Operator(name.strip(), frozenset(t for t in tenants.split("|") if t))
    return out


def operator(authorization: str = Header(default="")) -> Operator:
    scheme, _, token = authorization.partition(" ")
    op = _token_map().get(token.strip()) if scheme.lower() == "bearer" else None
    if op is None:
        raise HTTPException(401, "missing or invalid bearer token")
    return op


def _tenants(op: Operator, tenant: str | None) -> list[str]:
    """Tenants this request may touch: the requested one (if granted) or all granted."""
    if tenant:
        if tenant not in op.tenants:
            raise HTTPException(403, "tenant not authorized for this operator")
        try:
            require_tenant(tenant)
        except UnknownTenantError:
            raise HTTPException(404, "unknown tenant") from None
        return [tenant]
    return sorted(op.tenants)


def _jsonable(row: dict) -> dict:
    return {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in row.items()}


# ------------------------------------------------------------------ meta

@app.get("/health")
def health() -> dict:
    from relay.health import run_checks

    checks = run_checks(default_gateway(), sse_stats=_SSE)
    return {"ok": all(c["ok"] for c in checks.values()), "checks": checks}


@app.get("/api/me")
def me(op: Operator = Depends(operator)) -> dict:
    return {"name": op.name, "tenants": sorted(op.tenants)}


@app.get("/api/tenants")
def tenants(op: Operator = Depends(operator)) -> list[dict]:
    return [{"tenant_id": t["tenant_id"], "name": t["name"]} for t in tenant_registry()
            if t["tenant_id"] in op.tenants]


@app.get("/api/graph")
def graph_structure(op: Operator = Depends(operator)) -> dict:
    """The compiled LangGraph's real nodes and edges (what the monitor draws)."""
    g = build_graph(Deps(default_gateway(), get_settings(), persist=False)).get_graph()
    return {"nodes": [n for n in g.nodes],
            "edges": [{"source": e.source, "target": e.target, "conditional": e.conditional}
                      for e in g.edges]}


# ------------------------------------------------------------------ metrics

@app.get("/api/metrics")
def metrics(tenant: str | None = None, op: Operator = Depends(operator)) -> dict:
    agg = {"decisions": 0, "pending_approvals": 0, "escalated": 0, "failed": 0, "held_budget": 0,
           "pending_decision": 0, "outbox": 0, "tokens_in": 0, "tokens_out": 0, "model_calls": 0,
           "model_errors": 0, "usage_unreported": 0}
    latencies, model_lat, by_action, by_category, by_model, recent = [], [], {}, {}, {}, []
    for t in _tenants(op, tenant):
        with tenant_tx(t) as conn:
            for r in conn.execute("SELECT status, action, category, latency_ms FROM decisions"):
                agg["decisions"] += 1
                if r["status"] in ("escalated", "failed", "held_budget"):
                    agg[r["status"]] += 1
                latencies.append(r["latency_ms"])
                by_action[r["action"]] = by_action.get(r["action"], 0) + 1
                by_category[r["category"]] = by_category.get(r["category"], 0) + 1
            agg["pending_approvals"] += conn.execute(
                "SELECT count(*) AS n FROM approvals WHERE status = 'pending'").fetchone()["n"]
            agg["outbox"] += conn.execute("SELECT count(*) AS n FROM outbox").fetchone()["n"]
            for r in conn.execute(
                    """SELECT model, task, count(*) AS calls, coalesce(sum(tokens_in),0) AS tin,
                              coalesce(sum(tokens_out),0) AS tout, avg(latency_ms) AS lat,
                              sum((NOT ok)::int) AS errors,
                              sum((ok AND tokens_in IS NULL)::int) AS unreported
                       FROM model_calls WHERE run_id NOT LIKE 'eval-%%' GROUP BY 1, 2"""):
                key = f"{r['model']} ({r['task']})"
                m = by_model.setdefault(key, {"model": r["model"], "task": r["task"], "calls": 0,
                                              "tokens_in": 0, "tokens_out": 0, "avg_latency_ms": 0})
                m["avg_latency_ms"] = round(
                    (m["avg_latency_ms"] * m["calls"] + float(r["lat"]) * r["calls"]) / (m["calls"] + r["calls"]))
                m["calls"] += r["calls"]
                m["tokens_in"] += int(r["tin"])
                m["tokens_out"] += int(r["tout"])
                agg["tokens_in"] += int(r["tin"])
                agg["tokens_out"] += int(r["tout"])
                agg["model_calls"] += r["calls"]
                agg["model_errors"] += r["errors"]
                agg["usage_unreported"] += r["unreported"]
            recent += [_jsonable(r) for r in conn.execute(
                f"SELECT {', '.join(EVENT_FIELDS)} FROM events ORDER BY event_id DESC LIMIT 25")]
    with platform_tx() as conn:
        queue = {r["status"]: r["n"] for r in conn.execute(
            "SELECT status, count(*) AS n FROM jobs WHERE tenant_id = ANY(%s) GROUP BY status",
            (list(_tenants(op, tenant)),))}
    finished = agg["decisions"]
    return {
        **agg,
        "success_rate": round((finished - agg["failed"]) / finished, 4) if finished else None,
        "avg_ticket_latency_ms": round(sum(latencies) / len(latencies)) if latencies else None,
        "queue": {"queued": queue.get("queued", 0), "leased": queue.get("leased", 0),
                  "done": queue.get("done", 0), "dead": queue.get("dead", 0)},
        "by_action": by_action, "by_category": by_category, "by_model": list(by_model.values()),
        "recent_events": sorted(recent, key=lambda e: -e["event_id"])[:25],
    }


# ------------------------------------------------------------------ tickets

def _ticket_rows(tenants: list[str], q: str | None = None) -> list[dict]:
    """Every accepted ticket of the given tenants with its derived state, in one stable
    order: newest received first, then tenant, then ticket id (no ties, no shuffling)."""
    from relay.queue import ticket_states
    from relay.ticket_view import allowed_actions, ticket_state

    out = []
    for t in tenants:
        jobs = ticket_states(t)
        with tenant_tx(t) as conn:
            rows = conn.execute(
                """SELECT t.tenant_id, t.ticket_id, t.customer_ref, t.subject, t.received_at,
                          t.source, d.category, d.priority, d.action, d.status AS decision_status,
                          d.injection_detected, d.latency_ms, d.tokens_in, d.tokens_out,
                          d.reason_code, d.updated_at,
                          (SELECT status FROM approvals a WHERE a.ticket_id = t.ticket_id
                           ORDER BY attempt DESC LIMIT 1) AS approval_status,
                          EXISTS (SELECT 1 FROM outbox o WHERE o.ticket_id = t.ticket_id) AS sent
                   FROM tickets t LEFT JOIN decisions d USING (tenant_id, ticket_id)
                   WHERE (%(q)s::text IS NULL OR t.ticket_id ILIKE %(like)s
                          OR t.subject ILIKE %(like)s OR t.body ILIKE %(like)s)""",
                {"q": q, "like": f"%{q}%"},
            ).fetchall()
        for r in rows:
            job = jobs.get(r["ticket_id"])
            state = ticket_state(r["decision_status"], r["approval_status"], r["sent"],
                                 job["status"] if job else None)
            out.append({**_jsonable(r), "state": state, "allowed_actions": allowed_actions(state)})
    out.sort(key=lambda r: (r["tenant_id"], r["ticket_id"]))
    out.sort(key=lambda r: r["received_at"], reverse=True)  # stable: ties keep tenant/id order
    return out


@app.get("/api/tickets")
def list_tickets(tenant: str | None = None, q: str | None = None, state: str | None = None,
                 category: str | None = None, priority: str | None = None,
                 action: str | None = None, status: str | None = None,
                 op: Operator = Depends(operator)) -> list[dict]:
    rows = _ticket_rows(_tenants(op, tenant), q)
    filters = {"state": state, "category": category, "priority": priority, "action": action,
               "decision_status": status}
    return [r for r in rows if all(v is None or r.get(k) == v for k, v in filters.items())]


@app.get("/api/tickets/summary")
def tickets_summary(tenant: str | None = None, op: Operator = Depends(operator)) -> dict:
    """Ticket counts by state. Each ticket counts once, however many events it has."""
    from relay.ticket_view import STATES

    rows = _ticket_rows(_tenants(op, tenant))
    counts = {s: 0 for s in STATES}
    for r in rows:
        counts[r["state"]] += 1
    return {"total": len(rows), "by_state": counts}


@app.get("/api/tickets/{tenant}/{ticket_id}")
def ticket_detail(tenant: str, ticket_id: str, op: Operator = Depends(operator)) -> dict:
    _tenants(op, tenant)
    with tenant_tx(tenant) as conn:
        ticket = conn.execute("SELECT * FROM tickets WHERE ticket_id = %s", (ticket_id,)).fetchone()
        if ticket is None:
            raise HTTPException(404, "ticket not found")
        data = {
            "ticket": _jsonable(ticket),
            "decision": conn.execute("SELECT * FROM decisions WHERE ticket_id = %s", (ticket_id,)).fetchone(),
            "approvals": conn.execute("SELECT * FROM approvals WHERE ticket_id = %s ORDER BY attempt",
                                      (ticket_id,)).fetchall(),
            "outbox": conn.execute("SELECT * FROM outbox WHERE ticket_id = %s", (ticket_id,)).fetchone(),
            "cases": conn.execute("SELECT * FROM cases WHERE ticket_id = %s ORDER BY created_at, case_id",
                                  (ticket_id,)).fetchall(),
            "threads": conn.execute("SELECT * FROM workflows WHERE ticket_id = %s "
                                    "ORDER BY started_at, thread_id", (ticket_id,)).fetchall(),
            "model_calls": conn.execute(
                "SELECT task, model, tokens_in, tokens_out, latency_ms, ok, error_class, at, thread_id "
                "FROM model_calls WHERE ticket_id = %s AND run_id NOT LIKE 'eval-%%' ORDER BY call_id",
                (ticket_id,)).fetchall(),
            "audit": conn.execute("SELECT * FROM audit_log WHERE ticket_id = %s ORDER BY audit_id",
                                  (ticket_id,)).fetchall(),
            "events": conn.execute(f"SELECT {', '.join(EVENT_FIELDS)} FROM events WHERE ticket_id = %s "
                                   "ORDER BY event_id", (ticket_id,)).fetchall(),
        }
    view = next(r for r in _ticket_rows([tenant], None) if r["ticket_id"] == ticket_id)
    pending = [a for a in data["approvals"] if a["status"] == "pending"]
    data["view"] = {"state": view["state"], "allowed_actions": view["allowed_actions"],
                    "pending_approval_id": pending[-1]["approval_id"] if pending else None}
    return json.loads(json.dumps(data, default=str))


# ------------------------------------------------------------------ inbox import + explicit processing

def _inbox_files() -> dict[str, Path]:
    from relay.config import ROOT

    return {str(p.relative_to(ROOT)): p for p in sorted((ROOT / "data").glob("*/tickets.jsonl"))}


@app.get("/api/inbox")
def inbox_files(op: Operator = Depends(operator)) -> list[dict]:
    """Ticket files the console may import (the seed inboxes under data/)."""
    from relay.queue import read_inbox

    out = []
    for name, path in _inbox_files().items():
        tickets, rejected = read_inbox(path)
        mine = [t for t in tickets if t.tenant in op.tenants]
        present = 0
        for tenant in sorted({t.tenant for t in mine}):
            with tenant_tx(tenant) as conn:
                ids = [t.id for t in mine if t.tenant == tenant]
                present += conn.execute("SELECT count(*) AS n FROM tickets WHERE ticket_id = ANY(%s)",
                                        (ids,)).fetchone()["n"]
        out.append({"file": name, "tickets": len(mine), "already_imported": present,
                    "rejected_lines": len(rejected)})
    return out


class ImportIn(BaseModel):
    file: str


@app.post("/api/inbox/import")
def import_inbox(body: ImportIn, op: Operator = Depends(operator)) -> dict:
    """Store an inbox's tickets as received. Nothing is processed: no job, no model call."""
    from relay.queue import read_inbox

    path = _inbox_files().get(body.file)
    if path is None:
        raise HTTPException(400, "unknown inbox file; choose one listed by GET /api/inbox")
    tickets, rejected = read_inbox(path)
    new, existing, not_authorized = [], [], []
    for t in tickets:
        if t.tenant not in op.tenants:
            not_authorized.append(f"{t.tenant}/{t.id}")
        elif store.upsert_ticket(t, source=body.file):
            new.append(f"{t.tenant}/{t.id}")
        else:
            existing.append(f"{t.tenant}/{t.id}")
    for r in rejected:
        store.log_rejection(None, str(r["tenant"]), str(r["ticket_id"]), r["reason"])
    return {"imported": new, "already_present": existing, "not_authorized": not_authorized,
            "rejected": rejected}


@app.post("/api/tickets/{tenant}/{ticket_id}/process", status_code=202)
def process_ticket(tenant: str, ticket_id: str, op: Operator = Depends(operator)) -> dict:
    """Process exactly this ticket, once. Re-requesting while it is queued, running or
    already decided returns 409; the queue's unique keys make duplicates impossible even
    for concurrent requests."""
    from relay.orchestrator import Orchestrator, thread_id_for
    from relay.queue import Worker, enqueue_ticket

    _tenants(op, tenant)
    ticket = store.load_ticket(tenant, ticket_id)
    if ticket is None:
        raise HTTPException(404, "ticket not found")
    s = get_settings()
    with _jobs_lock:
        view = next(r for r in _ticket_rows([tenant], None) if r["ticket_id"] == ticket_id)
        if "process" not in view["allowed_actions"]:
            raise HTTPException(409, f"ticket cannot be processed in its current state ({view['state']})")
        run_id = store.new_run_id()
        store.create_run(run_id, "inbox", f"console:{op.name}", s.max_tokens_per_run)
        outcome = enqueue_ticket(ticket, run_id, s.max_attempts)
        if outcome == "duplicate":
            raise HTTPException(409, "ticket is already being processed or was already decided")
        with platform_tx() as conn:
            generation = conn.execute(
                "SELECT max(generation) AS g FROM jobs WHERE tenant_id = %s AND ticket_id = %s",
                (tenant, ticket_id)).fetchone()["g"]

    def work():
        Worker(Orchestrator(default_gateway()), f"console-{run_id[-4:]}").drain(run_id)
        store.finish_run(run_id)
        outputs.export_decisions()

    threading.Thread(target=work, daemon=True).start()
    return {"run_id": run_id, "outcome": outcome,
            "thread_id": thread_id_for(tenant, ticket_id, generation)}


# ------------------------------------------------------------------ runs (LangGraph threads)

@app.get("/api/runs")
def list_runs(tenant: str | None = None, limit: int = Query(50, le=500),
              op: Operator = Depends(operator)) -> list[dict]:
    out = []
    for t in _tenants(op, tenant):
        with tenant_tx(t) as conn:
            out += [_jsonable(r) for r in conn.execute(
                """SELECT w.*, d.action, d.status AS decision_status, d.latency_ms
                   FROM workflows w LEFT JOIN decisions d ON d.thread_id = w.thread_id
                   ORDER BY w.started_at DESC, w.thread_id LIMIT %s""", (limit,))]
    # Newest started first; a run never moves because it was updated.
    out.sort(key=lambda r: r["thread_id"])
    out.sort(key=lambda r: r["started_at"], reverse=True)
    return out[:limit]


def _thread_tenant(thread_id: str, op: Operator) -> str:
    tenant = thread_id.split(":", 1)[0]
    _tenants(op, tenant)
    return tenant


@app.get("/api/runs/{thread_id}")
def run_detail(thread_id: str, op: Operator = Depends(operator)) -> dict:
    tenant = _thread_tenant(thread_id, op)
    with tenant_tx(tenant) as conn:
        wf = conn.execute("SELECT * FROM workflows WHERE thread_id = %s", (thread_id,)).fetchone()
        if wf is None:
            raise HTTPException(404, "run not found")
        events = conn.execute(f"SELECT {', '.join(EVENT_FIELDS)} FROM events WHERE thread_id = %s "
                              "ORDER BY event_id", (thread_id,)).fetchall()
        calls = conn.execute("SELECT * FROM model_calls WHERE thread_id = %s ORDER BY call_id",
                             (thread_id,)).fetchall()
        job = None
    with platform_tx() as conn:
        job = conn.execute("SELECT job_id, status, attempts, max_attempts, lease_owner, lease_expires_at, "
                           "last_error, available_at FROM jobs WHERE idempotency_key = %s",
                           (":".join(thread_id.rsplit(":g", 1)),)).fetchone()
    return json.loads(json.dumps({"workflow": wf, "events": events, "model_calls": calls, "job": job},
                                 default=str))


@app.get("/api/runs/{thread_id}/events")
def run_events(thread_id: str, after: int = 0, op: Operator = Depends(operator)) -> list[dict]:
    tenant = _thread_tenant(thread_id, op)
    with tenant_tx(tenant) as conn:
        rows = conn.execute(f"SELECT {', '.join(EVENT_FIELDS)} FROM events WHERE thread_id = %s "
                            "AND event_id > %s ORDER BY event_id", (thread_id, after)).fetchall()
    return [_jsonable(r) for r in rows]


PAGE = 500


def _events_after(tenants: list[str], after: int, thread_id: str | None) -> list[dict]:
    """Events after `after` across tenants (one RLS-bound query per tenant). If a tenant
    returned a full page, the merged batch stops at that page's last id so the cursor
    never jumps past another tenant's unread events."""
    rows, horizon = [], None
    for t in tenants:
        with tenant_tx(t) as conn:
            page = conn.execute(
                f"SELECT {', '.join(EVENT_FIELDS)} FROM events WHERE event_id > %s "
                "AND (%s::text IS NULL OR thread_id = %s::text) ORDER BY event_id LIMIT %s",
                (after, thread_id, thread_id, PAGE)).fetchall()
        if len(page) == PAGE:
            horizon = min(horizon or page[-1]["event_id"], page[-1]["event_id"])
        rows += page
    rows = [r for r in rows if horizon is None or r["event_id"] <= horizon]
    return sorted((_jsonable(r) for r in rows), key=lambda r: r["event_id"])


def _latest_event_id(tenants: list[str]) -> int:
    latest = 0
    for t in tenants:
        with tenant_tx(t) as conn:
            latest = max(latest, conn.execute("SELECT coalesce(max(event_id), 0) AS m FROM events")
                         .fetchone()["m"])
    return latest


async def _sse(request: Request, tenants: list[str], after: int, thread_id: str | None):
    _SSE["clients"] += 1
    cursor = after if after >= 0 else await asyncio.to_thread(_latest_event_id, tenants)
    try:
        yield "retry: 2000\n\n"
        idle = 0
        while not await request.is_disconnected():
            batch = await asyncio.to_thread(_events_after, tenants, cursor, thread_id)
            for e in batch:
                cursor = e["event_id"]
                _SSE["delivered"] += 1
                yield f"id: {cursor}\nevent: relay\ndata: {json.dumps(e, default=str)}\n\n"
            idle = 0 if batch else idle + 1
            if idle and idle % 20 == 0:
                yield ": keep-alive\n\n"
            await asyncio.sleep(0.5)
    finally:
        _SSE["clients"] -= 1


def _resume_from(last_event_id: str | None, after: int) -> int:
    try:
        return max(after, int(last_event_id)) if last_event_id else after
    except ValueError:
        return after


@app.get("/api/stream")
async def stream(request: Request, tenant: str | None = None, after: int = 0,
                 last_event_id: str | None = Header(default=None),
                 op: Operator = Depends(operator)):
    """Live execution events for the operator's tenants. `after=-1` starts from now.
    Reconnects resume from Last-Event-ID, so events emitted while disconnected are
    replayed from Postgres."""
    return StreamingResponse(_sse(request, _tenants(op, tenant), _resume_from(last_event_id, after), None),
                             media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/runs/{thread_id}/stream")
async def run_stream(thread_id: str, request: Request, after: int = 0,
                     last_event_id: str | None = Header(default=None),
                     op: Operator = Depends(operator)):
    tenant = _thread_tenant(thread_id, op)
    return StreamingResponse(_sse(request, [tenant], _resume_from(last_event_id, after), thread_id),
                             media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class TicketIn(BaseModel):
    id: str
    tenant: str
    customer_ref: str = "unknown"
    subject: str = ""
    body: str = ""
    received_at: datetime


class RunIn(BaseModel):
    tickets: list[TicketIn] = Field(min_length=1, max_length=50)


@app.post("/api/runs", status_code=202)
def submit_run(body: RunIn, op: Operator = Depends(operator)) -> dict:
    """Enqueue tickets and process them in a background worker thread (real models)."""
    for t in body.tickets:
        _tenants(op, t.tenant)
    from relay.orchestrator import Orchestrator
    from relay.queue import Worker, enqueue_ticket

    s = get_settings()
    run_id = store.new_run_id()
    store.create_run(run_id, "inbox", f"api:{op.name}", s.max_tokens_per_run)
    outcomes = {t.id: enqueue_ticket(Ticket.model_validate(t.model_dump()), run_id, s.max_attempts)
                for t in body.tickets}

    def work():
        Worker(Orchestrator(default_gateway()), f"api-{run_id[-4:]}").drain(run_id)
        store.finish_run(run_id)
        outputs.export_decisions()

    threading.Thread(target=work, daemon=True).start()
    return {"run_id": run_id, "enqueued": outcomes}


# ------------------------------------------------------------------ approvals

@app.get("/api/approvals")
def list_approvals(status: str | None = "pending", tenant: str | None = None,
                   op: Operator = Depends(operator)) -> list[dict]:
    rows = approvals_svc.list_approvals(frozenset(_tenants(op, tenant)), None if status == "all" else status)
    return json.loads(json.dumps(rows, default=str))


@app.get("/api/approvals/{approval_id}")
def get_approval(approval_id: str, op: Operator = Depends(operator)) -> dict:
    row = approvals_svc.find(approval_id, op.tenants)
    if row is None:
        raise HTTPException(404, "approval not found")
    with tenant_tx(row["tenant_id"]) as conn:
        ticket = conn.execute("SELECT * FROM tickets WHERE ticket_id = %s", (row["ticket_id"],)).fetchone()
        passages = conn.execute("SELECT kb_id, title, body FROM kb_articles WHERE kb_id = ANY(%s) "
                                "AND NOT internal", (row["kb_refs"],)).fetchall()
        outbox = conn.execute("SELECT * FROM outbox WHERE approval_id = %s", (approval_id,)).fetchone()
    return json.loads(json.dumps({"approval": row, "ticket": ticket, "citations": passages,
                                  "outbox": outbox}, default=str))


class RejectIn(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


def _decide(fn, approval_id: str, op: Operator, *args) -> dict:
    from relay.orchestrator import Orchestrator

    with _jobs_lock:
        try:
            out = fn(Orchestrator(default_gateway()), approval_id, op.name, *args, tenants=op.tenants)
        except approvals_svc.ApprovalError as e:
            raise HTTPException(409, str(e)) from None
    outputs.export_outbox()
    outputs.export_decisions()
    return out.__dict__


@app.post("/api/approvals/{approval_id}/approve")
def approve(approval_id: str, op: Operator = Depends(operator)) -> dict:
    return _decide(approvals_svc.approve, approval_id, op)


@app.post("/api/approvals/{approval_id}/reject")
def reject(approval_id: str, body: RejectIn, op: Operator = Depends(operator)) -> dict:
    return _decide(approvals_svc.reject, approval_id, op, body.reason)


# ------------------------------------------------------------------ escalations

NEXT_STEP = {
    "injection": "Security review: confirm no instruction was followed, then answer the genuine question manually.",
    "clinical": "On-duty dentist contacts the patient; use the KB emergency wording for severe symptoms.",
    "legal_threat": "Support lead responds personally within the day; check the order and refund history first.",
    "money_dispute": "Billing lead reviews the charge history before any commitment is made.",
    "kb_not_covered": "Support lead answers or routes it; consider adding a KB article if it recurs.",
    "checker_failed": "Write the reply manually; the model could not produce a grounded, safe draft.",
    "rejected_twice": "Write the reply manually using the reviewers' rejection reasons.",
    "dead_letter": "Ops: check Ollama / database health, then re-run the inbox to retry this ticket.",
    "invalid_model_output": "Ops: inspect model health; re-run the inbox to retry.",
}


@app.get("/api/escalations")
def escalations(tenant: str | None = None, op: Operator = Depends(operator)) -> list[dict]:
    out = []
    for t in _tenants(op, tenant):
        with tenant_tx(t) as conn:
            for c in conn.execute(
                    """SELECT c.*, d.category, d.priority, d.injection_detected, t.subject, t.received_at
                       FROM cases c LEFT JOIN decisions d USING (tenant_id, ticket_id)
                       LEFT JOIN tickets t USING (tenant_id, ticket_id)
                       ORDER BY c.created_at DESC, c.case_id"""):
                audit = conn.execute("SELECT actor, action, from_state, to_state, reason, at FROM audit_log "
                                     "WHERE ticket_id = %s ORDER BY audit_id", (c["ticket_id"],)).fetchall()
                out.append({**c, "audit": audit,
                            "next_step": NEXT_STEP.get(c["reason_code"], "Support lead reviews the case.")})
    out.sort(key=lambda c: c["case_id"])
    out.sort(key=lambda c: c["created_at"], reverse=True)  # newest first, ties by case id
    return json.loads(json.dumps(out, default=str))


# ------------------------------------------------------------------ evaluations

@app.get("/api/evaluations")
def evaluations(op: Operator = Depends(operator)) -> dict:
    with platform_tx() as conn:
        rows = conn.execute("SELECT eval_id, runs, summary, created_at FROM eval_runs "
                            "ORDER BY created_at DESC").fetchall()
    files = sorted(p.name for p in get_settings().eval_dir.glob("run_*.jsonl"))
    return {"running": _background.get("eval"),
            "runs": json.loads(json.dumps(rows, default=str)), "files": files}


@app.get("/api/evaluations/files/{name}")
def evaluation_file(name: str, op: Operator = Depends(operator)) -> list[dict]:
    if not name.startswith("run_") or not name.endswith(".jsonl") or "/" in name:
        raise HTTPException(400, "invalid file name")
    path: Path = get_settings().eval_dir / name
    if not path.exists():
        raise HTTPException(404, "file not found")
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return [r for r in rows if r.get("tenant") in op.tenants]


class EvalIn(BaseModel):
    runs: int = Field(5, ge=1, le=10)


@app.post("/api/evaluations/run", status_code=202)
def run_eval(body: EvalIn, op: Operator = Depends(operator)) -> dict:
    from relay.config import ROOT
    from relay.evaluation import run_evaluation

    if not op.tenants >= {t["tenant_id"] for t in tenant_registry()}:
        raise HTTPException(403, "evaluation spans all tenants; operator lacks access")
    if _background.get("eval"):
        raise HTTPException(409, "an evaluation is already running")
    _background["eval"] = f"started by {op.name}"

    def work():
        try:
            run_evaluation(default_gateway(), body.runs, ROOT / "data" / "golden.jsonl", ROOT / "data")
        finally:
            _background.pop("eval", None)

    threading.Thread(target=work, daemon=True).start()
    return {"started": True, "runs": body.runs}


def main() -> None:
    import uvicorn

    uvicorn.run("relay.api:app", host="127.0.0.1", port=get_settings().api_port)
