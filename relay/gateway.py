"""Model gateway (E1). Every model call in Relay goes through `ModelGateway`.

It routes by task (triage -> small model, draft -> larger model, embed -> embedding
model), enforces the per-ticket and per-run token budgets *before* calling, records
model / tokens in / tokens out / latency for every call, and classifies errors.

Token counts are Ollama's own `prompt_eval_count` and `eval_count`. If the runtime
omits them they are stored as NULL ("unavailable"), never estimated.
"""
from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass
from typing import Literal, Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from relay.config import Settings, get_settings

Task = Literal["triage", "draft", "embed"]
T = TypeVar("T", bound=BaseModel)


# ------------------------------------------------------------------ errors

class GatewayError(Exception):
    error_class = "gateway_error"
    transient = False


class ModelUnavailable(GatewayError):
    error_class = "model_unavailable"
    transient = True


class ModelTimeout(GatewayError):
    error_class = "model_timeout"
    transient = True


class ModelMissing(GatewayError):
    """The required model is not pulled. Never silently substituted."""
    error_class = "model_missing"


class BudgetExceeded(GatewayError):
    error_class = "budget_exceeded"

    def __init__(self, scope: str, used: int, needed: int, limit: int):
        super().__init__(f"{scope} budget exhausted: used={used} needed~{needed} limit={limit}")
        self.scope = scope


class InvalidModelOutput(GatewayError):
    error_class = "invalid_model_output"

    def __init__(self, task: str, detail: str, raw: str):
        super().__init__(f"{task} output failed validation: {detail}")
        self.task = task
        self.detail = detail
        self.raw = raw


# ------------------------------------------------------------------ data

@dataclass(frozen=True)
class CallContext:
    tenant_id: str
    ticket_id: str | None
    thread_id: str | None
    run_id: str | None
    ticket_tokens_used: int = 0  # tokens already spent on this ticket


@dataclass
class Usage:
    task: str
    model: str
    tokens_in: int | None
    tokens_out: int | None
    latency_ms: int
    ok: bool
    error_class: str | None = None

    @property
    def total(self) -> int:
        return (self.tokens_in or 0) + (self.tokens_out or 0)


@dataclass
class ChatResponse:
    content: str
    tokens_in: int | None
    tokens_out: int | None


class Backend(Protocol):
    def chat(self, model: str, messages: list[dict], schema: dict, options: dict) -> ChatResponse: ...
    def embed(self, model: str, inputs: list[str]) -> tuple[list[list[float]], int | None]: ...
    def available_models(self) -> list[str]: ...


class Meter(Protocol):
    """Persists usage and reads the per-run counter."""

    def record(self, ctx: CallContext, usage: Usage) -> None: ...
    def run_tokens(self, run_id: str) -> tuple[int, int]: ...  # (used, budget)


# ------------------------------------------------------------------ Ollama backend

class OllamaBackend:
    def __init__(self, base_url: str, timeout_s: float):
        self._client = httpx.Client(base_url=base_url, timeout=timeout_s)

    def _post(self, path: str, payload: dict) -> dict:
        try:
            r = self._client.post(path, json=payload)
        except httpx.TimeoutException as e:
            raise ModelTimeout(f"ollama timed out on {path}") from e
        except httpx.TransportError as e:
            raise ModelUnavailable(f"ollama unreachable: {type(e).__name__}") from e
        if r.status_code == 404:
            raise ModelMissing(
                f"model {payload.get('model')!r} not found; run: ollama pull {payload.get('model')}"
            )
        if r.status_code >= 500:
            raise ModelUnavailable(f"ollama returned HTTP {r.status_code}")
        r.raise_for_status()
        return r.json()

    def chat(self, model: str, messages: list[dict], schema: dict, options: dict) -> ChatResponse:
        d = self._post("/api/chat", {
            "model": model, "messages": messages, "stream": False,
            "format": schema, "options": options,
        })
        return ChatResponse(d["message"]["content"], d.get("prompt_eval_count"), d.get("eval_count"))

    def embed(self, model: str, inputs: list[str]) -> tuple[list[list[float]], int | None]:
        d = self._post("/api/embed", {"model": model, "input": inputs})
        return d["embeddings"], d.get("prompt_eval_count")

    def available_models(self) -> list[str]:
        try:
            r = self._client.get("/api/tags", timeout=5)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise ModelUnavailable(f"ollama unreachable: {type(e).__name__}") from e
        return [m["name"] for m in r.json().get("models", [])]


# ------------------------------------------------------------------ gateway

def inline_schema(model: type[BaseModel]) -> dict:
    """JSON schema with $defs inlined, for Ollama's `format` constrained decoding."""
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def resolve(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return resolve(defs[node["$ref"].rsplit("/", 1)[-1]])
            return {k: resolve(v) for k, v in node.items()}
        if isinstance(node, list):
            return [resolve(v) for v in node]
        return node

    return resolve(schema)


def estimate_tokens(text: str) -> int:
    """Pre-call estimate used only for the budget gate (never recorded as usage)."""
    return max(1, len(text) // 4)


class ModelGateway:
    def __init__(self, backend: Backend, meter: Meter | None, settings: Settings | None = None,
                 rng: random.Random | None = None):
        self.backend = backend
        self.meter = meter
        self.s = settings or get_settings()
        self._rng = rng or random.Random()

    def model_for(self, task: Task) -> str:
        return {"triage": self.s.triage_model, "draft": self.s.draft_model,
                "embed": self.s.embed_model}[task]

    # -- budget gate -------------------------------------------------------
    def _check_budget(self, ctx: CallContext, needed: int) -> None:
        limit = self.s.max_tokens_per_ticket
        if ctx.ticket_tokens_used + needed > limit:
            raise BudgetExceeded("ticket", ctx.ticket_tokens_used, needed, limit)
        if self.meter and ctx.run_id:
            used, budget = self.meter.run_tokens(ctx.run_id)
            if used + needed > budget:
                raise BudgetExceeded("run", used, needed, budget)

    def _chaos(self) -> None:
        if self.s.chaos_fail_rate > 0 and self._rng.random() < self.s.chaos_fail_rate:
            raise ModelUnavailable("chaos: injected provider failure")

    def _record(self, ctx: CallContext, usage: Usage) -> None:
        if self.meter:
            self.meter.record(ctx, usage)

    # -- calls -------------------------------------------------------------
    def structured(self, task: Task, messages: list[dict], schema: type[T],
                   ctx: CallContext) -> tuple[T, Usage]:
        """One chat call constrained to `schema`. Validation is Pydantic JSON parsing,
        never regex. Raises InvalidModelOutput (with usage attached) on a bad payload."""
        model = self.model_for(task)
        self._check_budget(ctx, estimate_tokens(json.dumps(messages)))
        options = {"temperature": self.s.temperature, "seed": self.s.seed,
                   "num_predict": 400 if task == "draft" else 256}
        t0 = time.perf_counter()
        try:
            self._chaos()
            resp = self.backend.chat(model, messages, inline_schema(schema), options)
        except GatewayError as e:
            usage = Usage(task, model, None, None, _ms(t0), False, e.error_class)
            self._record(ctx, usage)
            raise
        usage = Usage(task, model, resp.tokens_in, resp.tokens_out, _ms(t0), True)
        try:
            result = schema.model_validate_json(resp.content)
        except ValidationError as e:
            usage.ok, usage.error_class = False, InvalidModelOutput.error_class
            self._record(ctx, usage)
            detail = "; ".join(f"{'.'.join(map(str, err['loc'])) or 'root'}: {err['msg']}"
                               for err in e.errors()[:5])
            err = InvalidModelOutput(task, detail, resp.content[:1000])
            err.usage = usage
            raise err from None
        self._record(ctx, usage)
        return result, usage

    def embed(self, texts: list[str], ctx: CallContext) -> tuple[list[list[float]], Usage]:
        model = self.model_for("embed")
        t0 = time.perf_counter()
        try:
            vectors, tokens_in = self.backend.embed(model, texts)
        except GatewayError as e:
            self._record(ctx, Usage("embed", model, None, None, _ms(t0), False, e.error_class))
            raise
        usage = Usage("embed", model, tokens_in, 0 if tokens_in is not None else None, _ms(t0), True)
        if any(len(v) != self.s.embed_dim for v in vectors):
            raise GatewayError(f"embedding dimension mismatch, expected {self.s.embed_dim}")
        self._record(ctx, usage)
        return vectors, usage

    def check_models(self) -> dict[str, bool]:
        names = set(self.backend.available_models())
        def present(m: str) -> bool:
            return m in names or f"{m}:latest" in names
        return {m: present(m) for m in (self.s.triage_model, self.s.draft_model, self.s.embed_model)}


def _ms(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)


# ------------------------------------------------------------------ persistence

class DbMeter:
    """Writes each call to model_calls (tenant-bound) and adds to the run counter."""

    def record(self, ctx: CallContext, usage: Usage) -> None:
        from relay.db import platform_tx, tenant_tx

        with tenant_tx(ctx.tenant_id) as conn:
            conn.execute(
                """INSERT INTO model_calls (tenant_id, ticket_id, thread_id, run_id, task, model,
                       tokens_in, tokens_out, latency_ms, ok, error_class)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (ctx.tenant_id, ctx.ticket_id, ctx.thread_id, ctx.run_id, usage.task, usage.model,
                 usage.tokens_in, usage.tokens_out, usage.latency_ms, usage.ok, usage.error_class),
            )
        if ctx.run_id and (usage.tokens_in or usage.tokens_out):
            with platform_tx() as conn:
                conn.execute(
                    "UPDATE runs SET tokens_in = tokens_in + %s, tokens_out = tokens_out + %s "
                    "WHERE run_id = %s",
                    (usage.tokens_in or 0, usage.tokens_out or 0, ctx.run_id),
                )

    def run_tokens(self, run_id: str) -> tuple[int, int]:
        from relay.db import platform_tx

        with platform_tx() as conn:
            row = conn.execute(
                "SELECT tokens_in + tokens_out AS used, token_budget FROM runs WHERE run_id = %s",
                (run_id,),
            ).fetchone()
        # An unknown run has no budget: fail closed rather than run unmetered.
        return (0, 0) if row is None else (int(row["used"]), int(row["token_budget"]))


def default_gateway() -> ModelGateway:
    s = get_settings()
    return ModelGateway(OllamaBackend(s.ollama_base_url, s.model_timeout_s), DbMeter(), s)
