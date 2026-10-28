"""Lead agent policy: turns the triage proposal plus deterministic signals into a route.

The lead is deterministic code, not an LLM. The triage model *proposes*; this module
*decides*. Overrides only ever make outcomes safer (escalate, raise priority, ask for
consent), with one consistency exception: an ask_for_info whose only "missing" detail
is already in the ticket becomes a draft_reply (both still wait for human approval).
It never downgrades a model's escalation. Every override is recorded on the decision.
"""
from __future__ import annotations

import re

from relay.agents.signals import MODEL_CORROBORATION
from relay.contracts import (
    Action, Category, PolicySignals, Priority, RetrievalResult, RouteDecision, TriageResult,
)

_ORDER_WORDS = re.compile(r"\border\b", re.I)
_RANK = {Priority.low: 0, Priority.normal: 1, Priority.high: 2, Priority.urgent: 3}


def _at_least(p: Priority, floor: Priority) -> Priority:
    return p if _RANK[p] >= _RANK[floor] else floor


def plan_route(triage: TriageResult, signals: PolicySignals) -> RouteDecision:
    action, category, priority = triage.proposed_action, triage.category, triage.priority
    overrides: list[str] = []
    reasons: list[str] = []

    # Model vote counts only when the deterministic detector has some evidence too;
    # a 1.5B model alone over-flags ordinary urgency as "instructions to the system".
    injection = signals.injection.detected or (
        triage.injection_suspected and signals.injection.score >= MODEL_CORROBORATION
    )

    def escalate(reason: str, floor: Priority | None = None) -> None:
        nonlocal action, priority
        if action != Action.escalate:
            overrides.append(f"action:{action.value}->escalate({reason})")
        action = Action.escalate
        if floor and _RANK[priority] < _RANK[floor]:
            overrides.append(f"priority:{priority.value}->{floor.value}({reason})")
            priority = floor
        reasons.append(reason)

    if injection:
        escalate("injection", Priority.high)
    # `signals.clinical` is only set for clinical-restricted tenants; a model-assigned
    # clinical category escalates on any tenant (escalating is the safe direction).
    if signals.clinical or category == Category.clinical:
        if category != Category.clinical:
            overrides.append(f"category:{category.value}->clinical")
            category = Category.clinical
        escalate("clinical", Priority.urgent if signals.severe_symptoms else None)
    if signals.legal_threat:
        escalate("legal_threat", Priority.urgent)
    if signals.money_dispute:
        escalate("money_dispute", Priority.high)
    # An ask_for_info must name something that is actually missing. Order references
    # already present in the ticket do not count; "ask for nothing" becomes a reply.
    missing = [m for m in triage.missing_info if m.strip()]
    if signals.has_order_reference:
        missing = [m for m in missing if not _ORDER_WORDS.search(m)]
    if action == Action.ask_for_info and not missing and not signals.third_party_records:
        overrides.append("action:ask_for_info->draft_reply(nothing_missing)")
        action = Action.draft_reply
    if action != Action.escalate and signals.third_party_records:
        if action != Action.ask_for_info:
            overrides.append(f"action:{action.value}->ask_for_info(consent_required)")
        action = Action.ask_for_info
        missing.append("written consent from the person whose records are requested")
        reasons.append("consent_required")
    if action == Action.escalate and not reasons:
        reasons.append("model_proposed_escalation")

    return RouteDecision(
        action=action, category=category, priority=priority, injection_detected=injection,
        reason_code=reasons[0] if reasons else "model_proposed", overrides=overrides,
        missing_info=missing[:3] if action == Action.ask_for_info else [],
    )


def needs_clinical_recheck(triage: TriageResult, signals: PolicySignals,
                           clinical_restricted: bool, already_rechecked: bool) -> bool:
    """Model and detector disagree: model says clinical, detector sees no clinical language.
    The lead asks triage once more; a repeated 'clinical' is then trusted (escalation)."""
    return (clinical_restricted and not already_rechecked
            and triage.category == Category.clinical and not signals.clinical)


def route_after_retrieval(route: RouteDecision, triage: TriageResult,
                          retrieval: RetrievalResult) -> tuple[str, RouteDecision]:
    """Returns (mode, route). mode is draft_reply | ask_for_info | courtesy | escalate."""
    # Courtesy needs two consistent model signals: a 1.5B model alone sometimes marks
    # real requests as "asks for nothing", and courtesy drafts carry no citations.
    if (route.action == Action.draft_reply and not triage.asks_for_something
            and route.category == Category.other):
        return "courtesy", route  # thanks / feedback: brief acknowledgement, no facts
    if retrieval.adequate:
        return route.action.value, route
    return "escalate", escalate_not_covered(route)


def escalate_not_covered(route: RouteDecision) -> RouteDecision:
    """No retrieved passage (or the drafter found none answers the question)."""
    return route.model_copy(update={
        "action": Action.escalate, "reason_code": "kb_not_covered",
        "overrides": [*route.overrides, f"action:{route.action.value}->escalate(kb_not_covered)"],
    })
