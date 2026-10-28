"""The ticket workflow as a LangGraph StateGraph.

Lead-agent nodes (deterministic orchestration):  validate_input, validate_tenant,
  route_decision, request_information, repair_invalid_output, escalate_ticket,
  record_decision, await_human_approval, write_approved_outbox, record_failure, finalize_run
Specialist nodes:  triage_agent (LLM), retrieve_knowledge (embeddings + pgvector),
  draft_reply (LLM), safety_check (deterministic rules)

Bounds: one schema repair per model stage, one checker-driven redraft, one
human-rejection redraft. Every path ends at END or at a durable approval interrupt.
"""
from __future__ import annotations

import functools
import time
from dataclasses import dataclass
from typing import Callable

from langgraph.errors import GraphInterrupt
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from pydantic import BaseModel, Field

from relay import store
from relay.agents import checker, drafter, lead, retrieval, signals, triage
from relay.config import Settings, get_settings
from relay.contracts import (
    Action, ApprovalDecision, CheckResult, DecisionRow, DecisionStatus, DraftResult,
    PolicySignals, RetrievalResult, RouteDecision, Ticket, TriageResult,
)
from relay.db import UnknownTenantError, require_tenant, tenant_registry
from relay.gateway import BudgetExceeded, CallContext, InvalidModelOutput, ModelGateway, Usage

MAX_SCHEMA_REPAIRS = 1   # per model stage (triage, draft)
MAX_CHECK_REPAIRS = 1    # checker-driven redrafts
MAX_HUMAN_REDRAFTS = 1   # redrafts after a human rejection


class UsageRecord(BaseModel):
    task: str
    model: str
    tokens_in: int | None
    tokens_out: int | None
    latency_ms: int


class RelayState(BaseModel):
    """Shared workflow state. Contains only what the workflow needs: ids, the ticket,
    and validated contracts. Never credentials, never internal KB text."""

    thread_id: str
    run_id: str
    tenant_id: str
    ticket_id: str
    started_ms: int
    ticket: Ticket | None = None
    tenant_name: str = ""
    clinical_restricted: bool = False
    signals: PolicySignals | None = None
    triage: TriageResult | None = None
    route: RouteDecision | None = None
    mode: str | None = None              # draft_reply | ask_for_info | courtesy
    missing_info: list[str] = Field(default_factory=list)
    retrieval: RetrievalResult | None = None
    draft: DraftResult | None = None
    check: CheckResult | None = None
    repair_stage: str | None = None      # triage | draft
    repair_hint: str | None = None
    schema_repairs: dict[str, int] = Field(default_factory=dict)
    triage_recheck: str | None = None    # None | pending | done
    check_repairs: int = 0
    human_redrafts: int = 0
    feedback: str | None = None
    approval_attempt: int = 0
    approval_id: str | None = None
    approval: ApprovalDecision | None = None
    usage: list[UsageRecord] = Field(default_factory=list)
    final_status: DecisionStatus | None = None
    escalation_reason: str | None = None
    error_code: str | None = None
    outbox_written: bool = False
    decision: DecisionRow | None = None


@dataclass
class Deps:
    gateway: ModelGateway
    settings: Settings
    persist: bool = True           # False in evaluation: no business writes
    drafting: bool = True          # False in evaluation: stop after routing


# ------------------------------------------------------------------ helpers

def _ctx(state: RelayState) -> CallContext:
    used = sum((u.tokens_in or 0) + (u.tokens_out or 0) for u in state.usage)
    return CallContext(state.tenant_id, state.ticket_id, state.thread_id, state.run_id, used)


def _usage(state: RelayState, u: Usage) -> list[UsageRecord]:
    return [*state.usage, UsageRecord(task=u.task, model=u.model, tokens_in=u.tokens_in,
                                      tokens_out=u.tokens_out, latency_ms=u.latency_ms)]


# Allowlisted, non-sensitive fields per node for the event stream / developer mode.
def _event_meta(node: str, update: dict) -> dict:
    meta: dict = {}
    if (t := update.get("triage")) is not None:
        meta.update(category=t.category.value, priority=t.priority.value,
                    proposed_action=t.proposed_action.value,
                    model_injection_flag=t.injection_suspected, contract="TriageResult")
    if (r := update.get("route")) is not None:
        meta.update(action=r.action.value, reason_code=r.reason_code, overrides=r.overrides,
                    injection_detected=r.injection_detected, contract="RouteDecision")
    if (s := update.get("signals")) is not None:
        meta.update(injection_score=s.injection.score, injection_signals=s.injection.signals)
    if (rr := update.get("retrieval")) is not None:
        meta.update(passages=[{"kb_id": p.kb_id, "score": p.score} for p in rr.passages],
                    top_score=rr.top_score, adequate=rr.adequate,
                    internal_hits=len(rr.internal_hits), contract="RetrievalResult")
    if (d := update.get("draft")) is not None:
        meta.update(kb_refs=d.kb_refs, words=len(d.reply.split()), contract="DraftResult")
    if (c := update.get("check")) is not None:
        meta.update(passed=c.passed, rules=[r.model_dump() for r in c.rules], contract="CheckResult")
    if (u := update.get("usage")) and node in ("triage_agent", "retrieve_knowledge", "draft_reply"):
        last = u[-1]
        meta.update(model=last.model, tokens_in=last.tokens_in, tokens_out=last.tokens_out,
                    model_latency_ms=last.latency_ms)
    for key in ("mode", "final_status", "escalation_reason", "error_code", "repair_stage",
                "approval_id"):
        if update.get(key) is not None:
            v = update[key]
            meta[key] = v.value if hasattr(v, "value") else v
    return meta


def traced(name: str, deps: Deps) -> Callable[[Callable], Callable]:
    """Emit node_started / node_completed / node_failed events with real durations."""

    def wrap(fn: Callable[[RelayState], dict]) -> Callable[[RelayState], dict]:
        @functools.wraps(fn)
        def inner(state: RelayState) -> dict:
            if not deps.persist:
                return fn(state)
            ids = dict(run_id=state.run_id, thread_id=state.thread_id, ticket_id=state.ticket_id)
            store.emit_event(state.tenant_id, **ids, event_type="node_started", node=name,
                             status="running")
            store.update_workflow(state.tenant_id, state.thread_id, node=name)
            t0 = time.perf_counter()
            try:
                update = fn(state)
            except GraphInterrupt:
                store.emit_event(state.tenant_id, **ids, event_type="node_waiting", node=name,
                                 status="waiting_for_approval",
                                 duration_ms=int((time.perf_counter() - t0) * 1000),
                                 metadata={"approval_id": state.approval_id})
                raise
            except Exception as e:
                store.emit_event(state.tenant_id, **ids, event_type="node_failed", node=name,
                                 status="failed", duration_ms=int((time.perf_counter() - t0) * 1000),
                                 metadata={"error_class": getattr(e, "error_class", type(e).__name__),
                                           "transient": getattr(e, "transient", False)})
                raise
            store.emit_event(state.tenant_id, **ids, event_type="node_completed", node=name,
                             status="completed", duration_ms=int((time.perf_counter() - t0) * 1000),
                             metadata=_event_meta(name, update))
            return update

        return inner

    return wrap


# ------------------------------------------------------------------ graph

def build_graph(deps: Deps, checkpointer=None):
    return build_workflow(deps).compile(checkpointer=checkpointer)


def build_workflow(deps: Deps) -> StateGraph:
    """The uncompiled graph. The orchestrator compiles it per operation against a
    checkpointer bound to one tenant."""
    s = deps.settings

    def validate_input(state: RelayState) -> dict:
        ticket = state.ticket or store.load_ticket(state.tenant_id, state.ticket_id)
        if ticket is None:
            return {"error_code": "ticket_not_found"}
        if ticket.tenant != state.tenant_id:
            return {"error_code": "tenant_mismatch"}
        if not (ticket.subject.strip() or ticket.body.strip()):
            return {"ticket": ticket, "escalation_reason": "empty_ticket"}
        return {"ticket": ticket}

    def validate_tenant(state: RelayState) -> dict:
        try:
            require_tenant(state.tenant_id)
        except UnknownTenantError:
            return {"error_code": "unknown_tenant"}
        t = next(t for t in tenant_registry() if t["tenant_id"] == state.tenant_id)
        return {"tenant_name": t["name"], "clinical_restricted": t["clinical_restricted"]}

    def triage_agent(state: RelayState) -> dict:
        hint = state.repair_hint if state.repair_stage == "triage" else None
        try:
            result, usage = triage.run_triage(deps.gateway, state.ticket, state.tenant_name,
                                              _ctx(state), hint,
                                              recheck=state.triage_recheck == "pending")
        except BudgetExceeded:
            return {"final_status": DecisionStatus.held_budget, "error_code": "budget_exceeded"}
        except InvalidModelOutput as e:
            return _invalid_output(state, "triage", e)
        return {"triage": result, "usage": _usage(state, usage),
                "repair_stage": None, "repair_hint": None,
                "triage_recheck": "done" if state.triage_recheck == "pending" else state.triage_recheck}

    def _invalid_output(state: RelayState, stage: str, e: InvalidModelOutput) -> dict:
        update: dict = {"usage": _usage(state, e.usage)} if hasattr(e, "usage") else {}
        if state.schema_repairs.get(stage, 0) < MAX_SCHEMA_REPAIRS:
            return {**update, "repair_stage": stage, "repair_hint": e.detail}
        return {**update, "final_status": DecisionStatus.failed,
                "error_code": f"invalid_model_output:{stage}", "repair_stage": None}

    def route_decision(state: RelayState) -> dict:
        sig = signals.compute_signals(state.tenant_id, state.ticket.text(),
                                      state.clinical_restricted)
        if lead.needs_clinical_recheck(state.triage, sig, state.clinical_restricted,
                                       state.triage_recheck is not None):
            return {"signals": sig, "triage_recheck": "pending"}
        return {"signals": sig, "route": lead.plan_route(state.triage, sig)}

    def retrieve_knowledge(state: RelayState) -> dict:
        try:
            result, usage = retrieval.retrieve(deps.gateway, state.ticket, _ctx(state),
                                               s.retrieval_top_k, s.retrieval_min_score)
        except BudgetExceeded:
            return {"final_status": DecisionStatus.held_budget, "error_code": "budget_exceeded"}
        mode, route = lead.route_after_retrieval(state.route, state.triage, result)
        update = {"retrieval": result, "route": route, "usage": _usage(state, usage)}
        if mode == "escalate":
            return {**update, "escalation_reason": route.reason_code}
        return {**update, "mode": mode}

    def request_information(state: RelayState) -> dict:
        """Fix the list of details the question back must ask for (lead-validated)."""
        return {"missing_info": state.route.missing_info or ["the details needed to help"]}

    def draft_reply(state: RelayState) -> dict:
        hint = state.repair_hint if state.repair_stage == "draft" else None
        feedback = "; ".join(x for x in (hint, state.feedback) if x) or None
        passages = [] if state.mode == "courtesy" else state.retrieval.passages
        try:
            result, usage = drafter.run_drafter(
                deps.gateway, state.ticket, state.tenant_name, passages, state.mode,
                state.missing_info, feedback, _ctx(state))
        except BudgetExceeded:
            return {"final_status": DecisionStatus.held_budget, "error_code": "budget_exceeded"}
        except InvalidModelOutput as e:
            return _invalid_output(state, "draft", e)
        update = {"draft": result, "usage": _usage(state, usage), "check": None,
                  "repair_stage": None, "repair_hint": None}
        if state.mode == "draft_reply" and not result.answerable and not result.kb_refs:
            # The drafter found no passage that answers the question and cited none:
            # the KB does not cover it, so escalate and draft nothing. (A cited draft is
            # still verified by the checker and a human, so a hesitant flag alone is not
            # enough; the 3B model sets it on answerable tickets.)
            return {**update, "route": lead.escalate_not_covered(state.route),
                    "escalation_reason": "kb_not_covered"}
        return update

    def safety_check(state: RelayState) -> dict:
        kb = checker.load_kb_index(state.tenant_id)
        action = Action.ask_for_info if state.mode == "ask_for_info" else Action.draft_reply
        passages = [] if state.mode == "courtesy" else state.retrieval.passages
        return {"check": checker.check_draft(state.draft, action, passages, state.ticket, kb,
                                             requested=state.missing_info)}

    def repair_invalid_output(state: RelayState) -> dict:
        """Bounded repair: one schema repair per model stage, one checker-driven redraft."""
        if state.repair_stage:
            counts = dict(state.schema_repairs)
            counts[state.repair_stage] = counts.get(state.repair_stage, 0) + 1
            return {"schema_repairs": counts}
        reasons = "; ".join(f"{r.rule_id}: {r.message}" for r in state.check.failures)
        return {"check_repairs": state.check_repairs + 1, "repair_stage": "draft",
                "repair_hint": reasons}

    def escalate_ticket(state: RelayState) -> dict:
        reason = state.escalation_reason or (state.route.reason_code if state.route else "escalated")
        if deps.persist:
            store.open_case(tenant_id=state.tenant_id, ticket_id=state.ticket_id,
                            thread_id=state.thread_id, kind="escalation", reason_code=reason,
                            reason=_case_reason(state, reason),
                            priority=state.route.priority.value if state.route else "normal")
        return {"final_status": DecisionStatus.escalated, "escalation_reason": reason,
                "draft": None}

    def record_failure(state: RelayState) -> dict:
        code = state.error_code or "unknown_failure"
        if deps.persist and state.ticket is not None and code != "unknown_tenant":
            store.open_case(tenant_id=state.tenant_id, ticket_id=state.ticket_id,
                            thread_id=state.thread_id, kind="dead_letter",
                            reason_code=code.split(":")[0], reason=f"classified failure: {code}",
                            priority="high")
        return {"final_status": DecisionStatus.failed, "draft": None}

    def record_decision(state: RelayState) -> dict:
        """The lead agent writes the decision record (one row per ticket)."""
        status = state.final_status or DecisionStatus.pending_approval
        route = state.route
        row = DecisionRow(
            ticket_id=state.ticket_id, tenant=state.tenant_id, run_id=state.run_id,
            category=(route.category if route else (state.triage.category if state.triage else "other")),
            priority=(route.priority if route else (state.triage.priority if state.triage else "normal")),
            action=_final_action(state, status),
            injection_detected=bool(route and route.injection_detected),
            kb_refs=state.draft.kb_refs if state.draft and status == DecisionStatus.pending_approval else [],
            draft_reply=state.draft.reply if state.draft and status == DecisionStatus.pending_approval else None,
            status=status,
            model="+".join(dict.fromkeys(u.model for u in state.usage)) or "none",
            tokens_in=sum(u.tokens_in or 0 for u in state.usage),
            tokens_out=sum(u.tokens_out or 0 for u in state.usage),
            latency_ms=int(time.time() * 1000) - state.started_ms,
        )
        if deps.persist:
            store.upsert_decision(row, state.thread_id,
                                  state.escalation_reason or state.error_code or
                                  (route.reason_code if route else None))
        return {"final_status": status, "decision": row}

    def await_human_approval(state: RelayState) -> dict:
        attempt = state.human_redrafts + 1
        kind = "ask_for_info" if state.mode == "ask_for_info" else "draft_reply"
        row, created = store.ensure_approval(
            tenant_id=state.tenant_id, ticket_id=state.ticket_id, thread_id=state.thread_id,
            attempt=attempt, kind=kind, draft_text=state.draft.reply,
            kb_refs=state.draft.kb_refs, check_result=state.check.model_dump())
        if created:
            store.update_workflow(state.tenant_id, state.thread_id, status="awaiting_approval")
            store.emit_event(state.tenant_id, run_id=state.run_id, thread_id=state.thread_id,
                             ticket_id=state.ticket_id, event_type="approval_required",
                             node="await_human_approval", status="waiting_for_approval",
                             metadata={"approval_id": row["approval_id"], "attempt": attempt})
        # Pauses here; the checkpoint is persisted and the process may exit.
        raw = interrupt({"approval_id": row["approval_id"], "attempt": attempt})
        decision = ApprovalDecision.model_validate(raw)
        if decision.approval_id != row["approval_id"]:
            raise ValueError("resume payload is for a different approval")
        store.update_workflow(state.tenant_id, state.thread_id, status="running")
        return {"approval": decision, "approval_id": row["approval_id"],
                "approval_attempt": attempt}

    def prepare_redraft(state: RelayState) -> dict:
        return {"human_redrafts": state.human_redrafts + 1, "feedback": state.approval.reason,
                "check_repairs": 0, "draft": None, "check": None, "final_status": None}

    def write_approved_outbox(state: RelayState) -> dict:
        written = store.write_outbox(tenant_id=state.tenant_id, ticket_id=state.ticket_id,
                                     thread_id=state.thread_id, approval_id=state.approval_id)
        return {"outbox_written": written or state.outbox_written}

    def finalize_run(state: RelayState) -> dict:
        if deps.persist:
            wf_status = {
                DecisionStatus.held_budget: "held_budget", DecisionStatus.failed: "failed",
            }.get(state.final_status, "completed")
            store.update_workflow(state.tenant_id, state.thread_id, status=wf_status)
            store.emit_event(state.tenant_id, run_id=state.run_id, thread_id=state.thread_id,
                             ticket_id=state.ticket_id, event_type="run_finished",
                             node="finalize_run", status=wf_status,
                             metadata={"final_status": state.final_status.value
                                       if state.final_status else None,
                                       "outbox_written": state.outbox_written})
        return {}

    # -------------------------------------------------------------- routing functions
    def after_input(state: RelayState) -> str:
        return "record_failure" if state.error_code else "validate_tenant"

    def after_tenant(state: RelayState) -> str:
        if state.error_code:
            return "record_failure"
        return "escalate_ticket" if state.escalation_reason else "triage_agent"

    def after_model(next_node: str) -> Callable[[RelayState], str]:
        def route(state: RelayState) -> str:
            if state.final_status == DecisionStatus.held_budget:
                return "record_decision"
            if state.final_status == DecisionStatus.failed:
                return "record_failure"
            if state.repair_stage:
                return "repair_invalid_output"
            if state.escalation_reason:
                return "escalate_ticket"
            return next_node
        return route

    def after_route(state: RelayState) -> str:
        if state.triage_recheck == "pending":
            return "triage_agent"
        return "escalate_ticket" if state.route.action == Action.escalate else "retrieve_knowledge"

    def after_retrieval(state: RelayState) -> str:
        if state.final_status == DecisionStatus.held_budget:
            return "record_decision"
        if state.escalation_reason:
            return "escalate_ticket"
        if not deps.drafting:
            return "record_decision"
        return "request_information" if state.mode == "ask_for_info" else "draft_reply"

    def after_repair(state: RelayState) -> str:
        return "triage_agent" if state.repair_stage == "triage" else "draft_reply"

    def after_check(state: RelayState) -> str:
        if state.check.passed:
            return "record_decision"
        if state.check_repairs < MAX_CHECK_REPAIRS:
            return "repair_invalid_output"
        return "checker_failed"

    def after_decision(state: RelayState) -> str:
        if state.final_status == DecisionStatus.pending_approval and deps.persist:
            return "await_human_approval"
        return "finalize_run"

    def after_approval(state: RelayState) -> str:
        if state.approval.decision == "approved":
            return "write_approved_outbox"
        if state.human_redrafts < MAX_HUMAN_REDRAFTS:
            return "prepare_redraft"
        return "rejected_twice"

    def checker_failed(state: RelayState) -> dict:
        return {"escalation_reason": "checker_failed"}

    def rejected_twice(state: RelayState) -> dict:
        return {"escalation_reason": "rejected_twice"}

    nodes: dict[str, Callable] = {
        "validate_input": validate_input, "validate_tenant": validate_tenant,
        "triage_agent": triage_agent, "route_decision": route_decision,
        "retrieve_knowledge": retrieve_knowledge, "request_information": request_information,
        "draft_reply": draft_reply, "safety_check": safety_check,
        "repair_invalid_output": repair_invalid_output, "escalate_ticket": escalate_ticket,
        "record_failure": record_failure, "record_decision": record_decision,
        "await_human_approval": await_human_approval, "prepare_redraft": prepare_redraft,
        "write_approved_outbox": write_approved_outbox, "finalize_run": finalize_run,
        "checker_failed": checker_failed, "rejected_twice": rejected_twice,
    }
    g = StateGraph(RelayState)
    for name, fn in nodes.items():
        g.add_node(name, traced(name, deps)(fn))

    g.add_edge(START, "validate_input")
    g.add_conditional_edges("validate_input", after_input, ["validate_tenant", "record_failure"])
    g.add_conditional_edges("validate_tenant", after_tenant,
                            ["triage_agent", "escalate_ticket", "record_failure"])
    g.add_conditional_edges("triage_agent", after_model("route_decision"),
                            ["route_decision", "repair_invalid_output", "record_decision",
                             "record_failure", "escalate_ticket"])
    g.add_conditional_edges("route_decision", after_route,
                            ["escalate_ticket", "retrieve_knowledge", "triage_agent"])
    g.add_conditional_edges("retrieve_knowledge", after_retrieval,
                            ["escalate_ticket", "request_information", "draft_reply",
                             "record_decision"])
    g.add_edge("request_information", "draft_reply")
    g.add_conditional_edges("draft_reply", after_model("safety_check"),
                            ["safety_check", "repair_invalid_output", "record_decision",
                             "record_failure", "escalate_ticket"])
    g.add_conditional_edges("safety_check", after_check,
                            ["record_decision", "repair_invalid_output", "checker_failed"])
    g.add_conditional_edges("repair_invalid_output", after_repair, ["triage_agent", "draft_reply"])
    g.add_edge("checker_failed", "escalate_ticket")
    g.add_edge("escalate_ticket", "record_decision")
    g.add_edge("record_failure", "record_decision")
    g.add_conditional_edges("record_decision", after_decision,
                            ["await_human_approval", "finalize_run"])
    g.add_conditional_edges("await_human_approval", after_approval,
                            ["write_approved_outbox", "prepare_redraft", "rejected_twice"])
    g.add_edge("prepare_redraft", "draft_reply")
    g.add_edge("rejected_twice", "escalate_ticket")
    g.add_edge("write_approved_outbox", "finalize_run")
    g.add_edge("finalize_run", END)
    return g


def _final_action(state: RelayState, status: DecisionStatus) -> Action:
    if status == DecisionStatus.escalated:
        return Action.escalate
    if state.route is not None:
        if state.mode == "ask_for_info":
            return Action.ask_for_info
        return state.route.action
    if state.triage is not None:
        return state.triage.proposed_action
    return Action.escalate  # failed before triage: a human must look at it


def _case_reason(state: RelayState, reason: str) -> str:
    """Safe, human-readable case reason. Never includes internal KB text."""
    labels = {
        "injection": "Ticket contains instructions aimed at the system; not obeyed.",
        "clinical": "Clinical question; route to the on-duty dentist. No advice was drafted.",
        "legal_threat": "Customer threatens legal action.",
        "money_dispute": "Money is disputed or a refund is demanded under pressure.",
        "kb_not_covered": "The knowledge base does not cover this request.",
        "checker_failed": "Drafts failed the safety checker twice.",
        "rejected_twice": "Draft was rejected by a human twice.",
        "empty_ticket": "Ticket has no subject or body.",
        "model_proposed_escalation": "Triage judged that this needs a human.",
    }
    summary = state.triage.summary if state.triage else ""
    return f"{labels.get(reason, reason)} {summary}".strip()[:500]
