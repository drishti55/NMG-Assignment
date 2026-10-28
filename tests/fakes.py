"""Deterministic fake model backend: no Ollama, same gateway code path."""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Callable

import numpy as np

from relay.gateway import ChatResponse, GatewayError

DIM = 768


def fake_embedding(text: str) -> list[float]:
    """Hashed bag-of-words: texts sharing words get high cosine similarity."""
    v = np.zeros(DIM, dtype=np.float32)
    for word, n in Counter(re.findall(r"[a-z]{3,}", text.lower())).items():
        h = int(hashlib.md5(word.encode()).hexdigest(), 16)
        v[h % DIM] += n
        v[(h >> 16) % DIM] += 0.5 * n
    v += 0.05  # shared floor so unrelated texts are not orthogonal
    return (v / np.linalg.norm(v)).tolist()


def _ticket(messages: list[dict]) -> str:
    for m in messages:
        if m["role"] == "user" and "<ticket>" in m["content"]:
            return m["content"].split("<ticket>", 1)[1].split("</ticket>", 1)[0]
    return ""


def default_triage(ticket: str) -> dict:
    t = ticket.lower()
    action, category, missing = "draft_reply", "other", []
    if "return" in t or "refund" in t:
        category = "returns_refunds"
    if "deliver" in t or "arrive" in t:
        category = "shipping_delivery"
    if "warranty" in t or "crack" in t:
        category, action, missing = "warranty_repair", "ask_for_info", ["photos of the defect"]
    if "appointment" in t or "open on" in t:
        category = "appointments"
    if "records" in t:
        category = "records_privacy"
    return {"summary": "fake triage", "category": category, "priority": "normal",
            "injection_suspected": "ignore" in t, "asks_for_something": "thank" not in t,
            "missing_info": missing, "proposed_action": action}


def default_draft(messages: list[dict]) -> dict:
    """Quote the first passage verbatim and cite it: always grounded."""
    user = next(m["content"] for m in messages if m["role"] == "user" and "<passages>" in m["content"])
    passages = user.split("<passages>", 1)[1].split("</passages>", 1)[0].strip().splitlines()
    first = next((p for p in passages if p.startswith("[")), None)
    if "Ask ONE clear question" in messages[0]["content"]:
        if first:
            kb_id = first[1:first.index("]")]
            return {"reply": f"Could you please send the photos of the defect [{kb_id}]?",
                    "kb_refs": [kb_id], "answerable": True}
        return {"reply": "Could you please share the missing details?", "kb_refs": [],
                "answerable": True}
    if first is None:
        return {"reply": "Thank you for your kind words.", "kb_refs": [], "answerable": True}
    kb_id = first[1:first.index("]")]
    body = first.split(": ", 1)[1]
    return {"reply": f"{body} [{kb_id}]", "kb_refs": [kb_id], "answerable": True}


class FakeBackend:
    """`script` maps a task ('triage'/'draft') to a list of overrides consumed in order:
    a dict (returned as JSON), a str (returned raw, e.g. invalid JSON) or an Exception."""

    def __init__(self, triage: Callable[[str], dict] = default_triage,
                 script: dict[str, list] | None = None, tokens: tuple[int, int] = (100, 20)):
        self.triage = triage
        self.script = {k: list(v) for k, v in (script or {}).items()}
        self.tokens = tokens
        self.calls: list[str] = []

    def chat(self, model: str, messages: list[dict], schema: dict, options: dict) -> ChatResponse:
        task = "triage" if "proposed_action" in schema.get("properties", {}) else "draft"
        self.calls.append(task)
        if self.script.get(task):
            item = self.script[task].pop(0)
            if isinstance(item, Exception):
                raise item
            content = item if isinstance(item, str) else json.dumps(item)
        elif task == "triage":
            content = json.dumps(self.triage(_ticket(messages)))
        else:
            content = json.dumps(default_draft(messages))
        return ChatResponse(content, *self.tokens)

    def embed(self, model: str, inputs: list[str]) -> tuple[list[list[float]], int | None]:
        return [fake_embedding(t) for t in inputs], 5 * len(inputs)

    def available_models(self) -> list[str]:
        return ["qwen2.5:1.5b", "qwen2.5:3b", "nomic-embed-text:latest"]


class Boom(GatewayError):
    """A transient provider failure for retry tests."""
    error_class = "model_unavailable"
    transient = True
