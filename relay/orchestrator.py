"""Lead-agent runtime: runs, resumes and inspects LangGraph threads with durable
Postgres checkpoints. One thread per (tenant, ticket, generation)."""
from __future__ import annotations

import time
from functools import lru_cache

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.types import Command

from relay import store
from relay.config import get_settings
from relay.db import checkpoint_pool
from relay.gateway import ModelGateway, default_gateway
from relay.graph import Deps, RelayState, build_graph


def thread_id_for(tenant_id: str, ticket_id: str, generation: int) -> str:
    return f"{tenant_id}:{ticket_id}:g{generation}"


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


@lru_cache
def checkpointer() -> PostgresSaver:
    return PostgresSaver(checkpoint_pool(),
                         serde=JsonPlusSerializer(allowed_msgpack_modules=CHECKPOINT_TYPES))


class Orchestrator:
    def __init__(self, gateway: ModelGateway | None = None, saver=None):
        self.gateway = gateway or default_gateway()
        self.graph = build_graph(Deps(self.gateway, get_settings()), saver or checkpointer())

    def thread_state(self, thread_id: str):
        return self.graph.get_state(_config(thread_id))

    def run_thread(self, *, thread_id: str, tenant_id: str, ticket_id: str, run_id: str) -> str:
        """Start, resume after a crash, or no-op. Returns the thread's resulting phase:
        'paused' (waiting for a human), 'finished', or raises on a transient error."""
        snap = self.thread_state(thread_id)
        if not snap.values:
            store.create_workflow(thread_id, tenant_id, ticket_id, run_id)
            store.emit_event(tenant_id, run_id=run_id, thread_id=thread_id, ticket_id=ticket_id,
                             event_type="run_started", node="orchestrator", status="running")
            initial = RelayState(thread_id=thread_id, run_id=run_id, tenant_id=tenant_id,
                                 ticket_id=ticket_id, started_ms=int(time.time() * 1000))
            self.graph.invoke(initial, _config(thread_id), durability="sync")
        elif snap.next and not snap.interrupts:
            # Crashed mid-run: continue from the last durable checkpoint.
            store.emit_event(tenant_id, run_id=run_id, thread_id=thread_id, ticket_id=ticket_id,
                             event_type="run_resumed", node="orchestrator", status="running",
                             metadata={"from_checkpoint": True, "next": list(snap.next)})
            self.graph.invoke(None, _config(thread_id), durability="sync")
        return self.phase(thread_id)

    def phase(self, thread_id: str) -> str:
        snap = self.thread_state(thread_id)
        if snap.interrupts:
            return "paused"
        return "running" if snap.next else "finished"

    def resume_with_decision(self, thread_id: str, payload: dict) -> str:
        """Resume a paused approval interrupt. Safe to call twice: a thread that is no
        longer paused is left untouched."""
        snap = self.thread_state(thread_id)
        if not snap.interrupts:
            return self.phase(thread_id)
        pending = snap.interrupts[0].value
        if pending.get("approval_id") != payload.get("approval_id"):
            return self.phase(thread_id)  # decision is for an older attempt; nothing to resume
        tenant_id = snap.values["tenant_id"]
        store.emit_event(tenant_id, run_id=snap.values["run_id"], thread_id=thread_id,
                         ticket_id=snap.values["ticket_id"], event_type="run_resumed",
                         node="await_human_approval", status="running",
                         metadata={"approval_id": payload["approval_id"],
                                   "decision": payload["decision"], "from_checkpoint": True})
        self.graph.invoke(Command(resume=payload), _config(thread_id), durability="sync")
        return self.phase(thread_id)
