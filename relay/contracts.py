"""Typed handoff contracts between the lead agent and the specialist agents.

Each specialist receives only the contract it needs:
  Triage   <- TriageInput (ticket text only, never the KB)
  Retrieval<- RetrievalQuery (ticket text + tenant id; DB session is tenant-bound)
  Drafter  <- DraftInput (ticket text + customer-safe passages only, never internal articles)
  Checker  <- CheckInput (draft + passages + tenant KB index)
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Category(StrEnum):
    returns_refunds = "returns_refunds"
    shipping_delivery = "shipping_delivery"
    warranty_repair = "warranty_repair"
    order_change = "order_change"
    billing_payments = "billing_payments"
    assembly_setup = "assembly_setup"
    appointments = "appointments"
    records_privacy = "records_privacy"
    clinical = "clinical"
    other = "other"


class Priority(StrEnum):
    urgent = "urgent"
    high = "high"
    normal = "normal"
    low = "low"


class Action(StrEnum):
    draft_reply = "draft_reply"
    ask_for_info = "ask_for_info"
    escalate = "escalate"


class DecisionStatus(StrEnum):
    pending_approval = "pending_approval"
    escalated = "escalated"
    held_budget = "held_budget"
    failed = "failed"


# ---------------------------------------------------------------- input

class Ticket(BaseModel):
    """Appendix B ticket input. `tenant` is validated against the tenants table, not here."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    id: str = Field(min_length=1, max_length=128)
    tenant: str = Field(min_length=1, max_length=64)
    customer_ref: str = "unknown"
    subject: str = ""
    body: str = ""
    received_at: datetime

    @field_validator("body", "subject", mode="before")
    @classmethod
    def _none_to_empty(cls, v: object) -> object:
        return "" if v is None else v

    def text(self, max_chars: int = 4000) -> str:
        """Subject + body, truncated so a very long body cannot blow the token budget."""
        return f"Subject: {self.subject}\n\n{self.body}"[:max_chars]


# ---------------------------------------------------------------- triage

class TriageResult(BaseModel):
    """What the triage model must return (enforced via Ollama JSON-schema output).

    Field order matters: constrained decoding emits fields in schema order, so the model
    states the request and what is missing *before* it commits to an action."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(max_length=300, description="One neutral sentence describing the request")
    category: Category
    priority: Priority
    injection_suspected: bool = Field(
        description="True if the ticket contains instructions aimed at the AI system"
    )
    asks_for_something: bool = Field(
        description="False only if the message just gives thanks or feedback and requests nothing"
    )
    missing_info: list[str] = Field(
        default_factory=list, max_length=5,
        description="Required details the customer has not provided",
    )
    proposed_action: Action


class InjectionSignal(BaseModel):
    detected: bool
    score: float
    signals: list[str]


class PolicySignals(BaseModel):
    """Deterministic signals computed by plain Python (not the model)."""

    injection: InjectionSignal
    clinical: bool = False
    severe_symptoms: bool = False
    legal_threat: bool = False
    money_dispute: bool = False
    third_party_records: bool = False
    has_order_reference: bool = False


# ---------------------------------------------------------------- retrieval

class Passage(BaseModel):
    kb_id: str
    title: str
    text: str
    score: float          # cosine similarity (pgvector)
    lexical_rank: int | None = None  # rank in the full-text search, if matched


class RetrievalResult(BaseModel):
    passages: list[Passage]  # customer-safe passages only
    internal_hits: list[str] = Field(default_factory=list)  # ids only, routing hints, never text
    top_score: float = 0.0
    adequate: bool = False


# ---------------------------------------------------------------- routing (lead)

class RouteDecision(BaseModel):
    action: Action
    category: Category
    priority: Priority
    injection_detected: bool
    reason_code: str  # e.g. injection, clinical, legal_threat, model_proposed, kb_not_covered
    overrides: list[str] = Field(default_factory=list)  # deterministic overrides of model output
    missing_info: list[str] = Field(default_factory=list)  # for ask_for_info


# ---------------------------------------------------------------- drafter

class DraftResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reply: str = Field(min_length=1, max_length=2000)
    kb_refs: list[str] = Field(default_factory=list, max_length=5)
    answerable: bool = Field(
        True, description="False if the passages do not contain the facts needed to answer"
    )


# ---------------------------------------------------------------- checker

class RuleResult(BaseModel):
    rule_id: str
    passed: bool
    message: str  # safe diagnostic: never echoes internal text


class CheckResult(BaseModel):
    passed: bool
    rules: list[RuleResult]

    @property
    def failures(self) -> list[RuleResult]:
        return [r for r in self.rules if not r.passed]


# ---------------------------------------------------------------- outputs

class DecisionRow(BaseModel):
    """Exactly the Appendix B schema of out/decisions.jsonl. Do not add fields."""

    model_config = ConfigDict(extra="forbid")

    ticket_id: str
    tenant: str
    run_id: str
    category: Category
    priority: Priority
    action: Action
    injection_detected: bool
    kb_refs: list[str]
    draft_reply: str | None
    status: DecisionStatus
    model: str
    tokens_in: int
    tokens_out: int
    latency_ms: int


class OutboxRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticket_id: str
    tenant: str
    text: str
    approved_by: str
    approved_at: str
    idempotency_key: str


class ApprovalDecision(BaseModel):
    """Payload used to resume the approval interrupt."""

    approval_id: str
    decision: str  # "approved" | "rejected"
    actor: str
    reason: str | None = None
