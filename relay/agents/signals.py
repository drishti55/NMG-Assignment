"""Deterministic policy signals. Plain Python, no model.

These enforce the brief's non-negotiable rules (injection, clinical, legal threat,
money dispute, third-party records) so that safety never depends on a 1.5B model
following its prompt. The model's opinion is combined with these, never trusted alone.

Pattern matching here inspects *ticket text* to detect risk; it is never used to parse
model output (model output is parsed with Pydantic against a JSON schema).
"""
from __future__ import annotations

import re
import unicodedata

from relay.contracts import InjectionSignal, PolicySignals

INJECTION_THRESHOLD = 0.5
# A model "injection" vote is accepted only with at least this much deterministic evidence.
MODEL_CORROBORATION = 0.2


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"[​-‏⁠﻿]", "", text)  # zero-width characters
    return re.sub(r"\s+", " ", text).strip().lower()


# Each family is one kind of evidence; a ticket scores the sum of distinct families hit.
_INJECTION_FAMILIES: dict[str, tuple[float, list[str]]] = {
    # Fake role / channel markers that try to impersonate the system prompt.
    "role_marker": (0.6, [
        r"(^|[\s\-=#>\[(])(system|assistant|developer|admin)\s*[:\]>]",
        r"<\|?(im_start|im_end|system)\|?>", r"###\s*(instruction|system)",
    ]),
    # Attempts to override or replace the agent's instructions.
    "override": (0.6, [
        r"\b(ignore|disregard|forget|override|bypass)\b.{0,30}\b(previous|prior|above|earlier|all|your|the)\b.{0,20}\b(instruction|rule|prompt|guideline|polic|direction)",
        r"\byou are (now|no longer)\b", r"\b(admin|developer|god|debug|jailbreak|dan)\s+mode\b",
        r"\bnew (instructions|rules|task)\s*:", r"\bpretend (to be|you are)\b",
        r"\bact as (an? )?(admin|administrator|system|developer|supervisor)",
    ]),
    # Requests to reveal hidden, internal or staff-only material.
    "exfiltration": (0.5, [
        r"\b(include|print|reveal|show|paste|share|output|leak|dump|give me|send me|list)\b.{0,40}\b(staff[- ]only|internal|confidential|hidden|secret|system prompt|your (prompt|instructions))",
        r"\b(staff[- ]only|internal) (notes?|policy|policies|memo|guidelines|finance notes)\b",
    ]),
    # Requests for another customer's or patient's data.
    "other_party_data": (0.5, [
        r"\b(previous|other|another|all|every|someone else'?s?)\s+(patient|customer|client|user)s?'?s?\b.{0,40}\b(record|detail|data|info|history|address|phone)",
        r"\b(print|send|show|give|list)\b.{0,30}\bdetails of (patient|customer)\b",
    ]),
    # Tells the agent to execute privileged actions on its own authority.
    "privileged_action": (0.4, [
        r"\b(approve|issue|process|authori[sz]e|transfer|send)\b.{0,25}\b(full )?(refund|credit|payment|money)\b.{0,40}\b(to|upi|account|wallet)\b",
        r"\bupi id\b.{0,30}@",
    ]),
    # Speaking to the AI rather than to the company.
    "addresses_ai": (0.3, [
        r"\b(dear|hey|hi|attention|note to( the)?|p\.?s\.?)\s*(ai|bot|assistant|model|llm|chatbot|gpt)\b",
        r"\b(ai|language model|llm|chatbot)\b.{0,20}\b(must|should|will|shall)\b",
    ]),
    # Visual separators commonly used to smuggle a second "prompt".
    "separator": (0.2, [r"(-{4,}|={4,}|\*{4,}|#{3,})\s*\w"]),
}
_COMPILED = {k: (w, [re.compile(p) for p in ps]) for k, (w, ps) in _INJECTION_FAMILIES.items()}


def detect_injection(text: str) -> InjectionSignal:
    norm = normalize(text)
    hits = [name for name, (_, pats) in _COMPILED.items() if any(p.search(norm) for p in pats)]
    score = round(min(1.0, sum(_COMPILED[h][0] for h in hits)), 2)
    return InjectionSignal(detected=score >= INJECTION_THRESHOLD, score=score, signals=hits)


_CLINICAL = re.compile(
    r"\b(pain|painful|ache|aching|hurts?|hurting|swell\w*|swollen|bleed\w*|blood|"
    r"medic\w*|ibuprofen|paracetamol|acetaminophen|painkillers?|antibiotics?|amoxicillin|"
    r"dos(e|es|age)|infect\w*|fever|numb\w*|abscess|pus|sensitiv\w*|stitches|"
    r"is (that|this|it) normal)\b"
)
_TREATMENT = re.compile(
    r"\b(extraction|root canal|filling|crown|implant|braces|aligners?|whitening|scaling|"
    r"surgery|treatment|procedure|wisdom tooth|denture|bridge|veneer)\b")
_OUTCOME_CUE = re.compile(
    r"\b(is (it|this|that) (normal|safe|ok|okay)|should i|safe to|how long (will|does) .{0,20}"
    r"(heal|last|recover)|heal\w*|recover\w*|side effects?|loose|feels? (weird|strange|off)|"
    r"went wrong|failed|still (hurts?|sore))\b")
_SEVERE = re.compile(
    r"\b(severe|severely|unbearable|excruciating|getting worse|worse|won'?t stop|"
    r"will not stop|not stopping|can'?t stop|heavy bleeding|face (is )?swell\w*|"
    r"facial swelling|my face|fever|can'?t (breathe|swallow|open))\b"
)
_LEGAL = re.compile(
    r"\b(consumer (court|forum)|court|lawyer|advocate|attorney|legal (action|notice)|"
    r"sue|suing|lawsuit|police complaint|fir\b|ombudsman)"
)
_MONEY_DISPUTE = [
    re.compile(r"\bover-?charg\w*"),
    re.compile(r"\bdouble[- ]?charg\w*"),
    re.compile(r"\bcharged\b.{0,60}\b(but|instead|whereas|while)\b.{0,40}\b(quot\w*|told|agreed|expected|promised)"),
    re.compile(r"\b(refund|money|amount)\b.{0,80}\b(nothing|not|never|still not|hasn'?t|has not)\b.{0,25}\b(arriv\w*|receiv\w*|credit\w*|come|reflect\w*)"),
    re.compile(r"\b(chargeback|dispute|disputing)\b"),
    re.compile(r"\b(wrong|incorrect|extra) (amount|charge|charges|bill)\b"),
]
_THIRD_PARTY_RECORDS = re.compile(
    r"\b(husband|wife|son|daughter|mother|father|mom|mum|dad|brother|sister|partner|friend|"
    r"child|parent|spouse)('s)?\b.{0,40}\b(records?|x-?rays?|reports?|files?|history|documents?)"
)
_ORDER_REF = re.compile(r"\b[A-Z]{2,4}-\d{4,}\b|\border\s*(id|no\.?|number|#)?\s*:?\s*#?\d{4,}", re.I)
_CONSENT = re.compile(r"\b(written consent|consent form|authori[sz]ation letter|signed consent)\b")


def compute_signals(tenant_id: str, text: str, clinical_tenant: bool) -> PolicySignals:
    """`clinical_tenant` marks tenants whose clinical questions must never be answered."""
    norm = normalize(text)
    clinical = clinical_tenant and bool(
        _CLINICAL.search(norm) or (_TREATMENT.search(norm) and _OUTCOME_CUE.search(norm)))
    return PolicySignals(
        injection=detect_injection(text),
        clinical=clinical,
        severe_symptoms=clinical and bool(_SEVERE.search(norm)),
        legal_threat=bool(_LEGAL.search(norm)),
        money_dispute=any(p.search(norm) for p in _MONEY_DISPUTE),
        third_party_records=bool(_THIRD_PARTY_RECORDS.search(norm)) and not _CONSENT.search(norm),
        has_order_reference=bool(_ORDER_REF.search(text)),
    )
