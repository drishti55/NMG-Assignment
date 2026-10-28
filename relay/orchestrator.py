"""Lead-agent runtime: runs, resumes and inspects LangGraph threads with durable
Postgres checkpoints. One thread per (tenant, ticket, generation).

Tenant isolation of checkpoints: every operation names the tenant it acts for. The
thread must belong to that tenant (checked here first), and the checkpointer runs on
a connection whose session is bound to that tenant, so the RLS policies on the
checkpoint tables (migration 0002) decide what is visible and writable. A thread id
alone never grants access.
"""
from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.types import Command

from relay import store
from relay.config import get_settings
from relay.db import checkpoint_pool, clear_tenant, require_tenant
from relay.gateway import ModelGateway, default_gateway
from relay.graph import Deps, RelayState, build_workflow


class ThreadAccessError(PermissionError):
    """A thread was requested for a tenant it does not belong to."""


def thread_id_for(tenant_id: str, ticket_id: str, generation: int) -> str:
    return f"{tenant_id}:{ticket_id}:g{generation}"


def thread_tenant(thread_id: str) -> str:
    return thread_id.split(":", 1)[0]


def _config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


# Exactly the types the workflow state may contain; anything else in a checkpoint is
# refused on load instead of being imported (LangGraph's strict msgpack allowlist).
CHECKPOINT_TYPES = [
    ("relay.contracts", name) for name in (
        "Ticket", "TriageResult", "PolicySignals", "InjectionSignal", "RouteDecision",
        "Passage", "RetrievalResult", "DraftResult", "CheckResult", "RuleResult",
        "ApprovalDecision", "DecisionRow", "Category", "Priority", "Action", "DecisionStatus",
    )
] + [("relay.graph", "UsageRecord"), ("relay.graph", "RelayState")]
SERDE = JsonPlusSerializer(allowed_msgpack_modules=CHECKPOINT_TYPES)


@contextmanager
def tenant_checkpointer(tenant_id: str) -> Iterator[PostgresSaver]:
    """A PostgresSaver on one pooled connection whose session is bound to `tenant_id`
    for the duration of the block. The binding is cleared on exit (and again by the
    pool's reset hook), so it can never leak into another tenant's operation."""
    require_tenant(tenant_id)
    with checkpoint_pool().connection() as conn:
        conn.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant_id,))
        try:
            yield PostgresSaver(conn, serde=SERDE)
        finally:
            clear_tenant(conn)


class Orchestrator:
    def __init__(self, gateway: ModelGateway | None = None):
        self.gateway = gateway or default_gateway()
        self.workflow = build_workflow(Deps(self.gateway, get_settings()))

    @contextmanager
    def _graph(self, tenant_id: str, thread_id: str):
        if thread_tenant(thread_id) != tenant_id:
            raise ThreadAccessError(f"thread does not belong to tenant {tenant_id!r}")
        with tenant_checkpointer(tenant_id) as saver:
            yield self.workflow.compile(checkpointer=saver)

    def thread_state(self, tenant_id: str, thread_id: str):
        with self._graph(tenant_id, thread_id) as graph:
            return graph.get_state(_config(thread_id))

    def run_thread(self, *, thread_id: str, tenant_id: str, ticket_id: str, run_id: str) -> str:
        """Start, resume after a crash, or no-op. Returns the thread's resulting phase:
        'paused' (waiting for a human), 'finished', or raises on a transient error."""
        with self._graph(tenant_id, thread_id) as graph:
            snap = graph.get_state(_config(thread_id))
            if not snap.values:
                store.create_workflow(thread_id, tenant_id, ticket_id, run_id)
                store.emit_event(tenant_id, run_id=run_id, thread_id=thread_id, ticket_id=ticket_id,
                                 event_type="run_started", node="orchestrator", status="running")
                initial = RelayState(thread_id=thread_id, run_id=run_id, tenant_id=tenant_id,
                                     ticket_id=ticket_id, started_ms=int(time.time() * 1000))
                graph.invoke(initial, _config(thread_id), durability="sync")
            elif snap.next and not snap.interrupts:
                # Crashed mid-run: continue from the last durable checkpoint.
                store.emit_event(tenant_id, run_id=run_id, thread_id=thread_id, ticket_id=ticket_id,
                                 event_type="run_resumed", node="orchestrator", status="running",
                                 metadata={"from_checkpoint": True, "next": list(snap.next)})
                graph.invoke(None, _config(thread_id), durability="sync")
            return _phase(graph.get_state(_config(thread_id)))

    def phase(self, tenant_id: str, thread_id: str) -> str:
        return _phase(self.thread_state(tenant_id, thread_id))

    def resume_with_decision(self, tenant_id: str, thread_id: str, payload: dict) -> str:
        """Resume a paused approval interrupt. Safe to call twice: a thread that is no
        longer paused is left untouched."""
        with self._graph(tenant_id, thread_id) as graph:
            snap = graph.get_state(_config(thread_id))
            if not snap.interrupts:
                return _phase(snap)
            if snap.interrupts[0].value.get("approval_id") != payload.get("approval_id"):
                return _phase(snap)  # decision is for an older attempt; nothing to resume
            store.emit_event(tenant_id, run_id=snap.values["run_id"], thread_id=thread_id,
                             ticket_id=snap.values["ticket_id"], event_type="run_resumed",
                             node="await_human_approval", status="running",
                             metadata={"approval_id": payload["approval_id"],
                                       "decision": payload["decision"], "from_checkpoint": True})
            graph.invoke(Command(resume=payload), _config(thread_id), durability="sync")
            return _phase(graph.get_state(_config(thread_id)))


def _phase(snap) -> str:
    if snap.interrupts:
        return "paused"
    return "running" if snap.next else "finished"
