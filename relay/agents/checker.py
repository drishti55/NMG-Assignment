"""Deterministic checker (C4). No model. A failed rule blocks approval.

Messages are safe diagnostics: they name the rule and the offending *reference* (a KB id,
a tenant alias, a word count) but never echo internal article text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from relay.contracts import Action, CheckResult, DraftResult, Passage, RuleResult, Ticket

MAX_WORDS = 150
# Share of a cited draft's content words that must occur in the cited passages or the
# ticket. Calibrated on the seed drafts: sound drafts scored >= 0.71, drafts that invented
# facts ("3 days", "our sales team") or misread the policy scored <= 0.64.
MIN_CONTENT_SUPPORT = 0.65
_STOPWORDS = frozenset(
    "this that with from your have will been were they them their there what when where which "
    "while would could should about into than then also only just very more most some such each "
    "other over under after before because being does done here like make made need needs want "
    "wants within ours yours".split())
# Connective / courtesy vocabulary a grounded reply may use without a source.
_FREE_WORDS = frozenset(
    "thank thanks please sorry help happy assist understand glad hope welcome regards dear hello "
    "kindly information details further assistance provide note available unfortunately send "
    "share could confirm".split())
UNCITED_MAX_WORDS = 60
SHINGLE = 6  # words; overlap of this length with an internal article counts as leakage

_KB_ID = re.compile(r"\b[A-Z]{2,5}-KB-\d{1,4}\b")
_CANARY = re.compile(r"canary[-_ ]?[a-z0-9]", re.I)
# Customer / patient / order style identifiers: letters, dash, digits (e.g. HD-3310, C-1001).
_ENTITY_ID = re.compile(r"\b[A-Z]{1,4}-\d{3,}\b")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_CONTACT = re.compile(
    r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+|\[email[^\]]*\]|https?://\S+|www\.\S+|"
    r"(?<!\w)\+?\d[\d\s-]{8,}\d(?!\w)", re.I)
_STAFF_ONLY = re.compile(r"\bstaff[- ]only\b", re.I)
# Harbor-style clinical restriction: diagnosis, medication or dosage language.
_CLINICAL_ADVICE = re.compile(
    r"\b(ibuprofen|paracetamol|acetaminophen|aspirin|antibiotics?|amoxicillin|painkillers?|"
    r"analgesics?|medication|medicine|tablets?|pills?|dos(e|es|age)|\d+\s?mg|milligrams?|"
    r"diagnos\w*|you (may|might|probably|likely) have|sounds like (an? )?\w+|infection|"
    r"gingivitis|periodontitis|dry socket|abscess|rinse with|apply (ice|heat)|cold compress|"
    r"salt ?water|is (normal|nothing to worry))\b",
    re.I,
)
_PROMISE = re.compile(
    r"\b(refund|credit|compensat\w*|guarantee\w*|waive\w*|free of charge|we will|we'll|"
    r"will be (sent|delivered|refunded|processed))\b|\d", re.I,
)


@dataclass(frozen=True)
class TenantKbIndex:
    """What the checker knows about the tenant: every KB id, which are internal, the
    internal bodies (for leak detection only), and the other tenants' names."""

    tenant_id: str
    public_ids: frozenset[str]
    internal_ids: frozenset[str]
    internal_bodies: tuple[str, ...]
    other_tenant_aliases: tuple[str, ...]
    clinical_restricted: bool


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def _shingles(text: str, n: int = SHINGLE) -> set[tuple[str, ...]]:
    w = _words(text)
    return {tuple(w[i:i + n]) for i in range(max(0, len(w) - n + 1))}


def _stem(w: str) -> str:
    return re.sub(r"(ing|ed|es|s)$", "", w)


def _content_words(text: str) -> list[str]:
    return [_stem(w) for w in re.findall(r"[a-z]+", text.lower())
            if len(w) >= 4 and w not in _STOPWORDS]


def content_support(text: str, sources: str) -> tuple[float, list[str]]:
    """(share of content words found in sources, the unsupported words)."""
    words = _content_words(_KB_ID.sub("", text))
    known = set(_content_words(sources)) | {_stem(w) for w in _FREE_WORDS}
    unsupported = [w for w in words if w not in known]
    return (1 - len(unsupported) / len(words) if words else 1.0), unsupported


def check_draft(draft: DraftResult, action: Action | str, passages: list[Passage],
                ticket: Ticket, kb: TenantKbIndex, requested: list[str] | None = None) -> CheckResult:
    """`requested`: for ask_for_info, the details the lead asked for (allowed vocabulary)."""
    text = draft.reply
    rules: list[RuleResult] = []

    def rule(rule_id: str, ok: bool, msg_fail: str, msg_ok: str = "ok") -> None:
        rules.append(RuleResult(rule_id=rule_id, passed=ok, message=msg_ok if ok else msg_fail))

    cited = set(draft.kb_refs) | set(_KB_ID.findall(text))
    known = kb.public_ids | kb.internal_ids
    retrieved = {p.kb_id for p in passages}

    unknown = sorted(cited - known)
    rule("CITE_EXISTS", not unknown, f"cites ids not in this tenant's KB: {', '.join(unknown)}")
    internal = sorted(cited & kb.internal_ids)
    rule("CITE_NOT_INTERNAL", not internal, f"cites internal article(s): {', '.join(internal)}")
    ungrounded = sorted((cited & kb.public_ids) - retrieved)
    rule("CITE_RETRIEVED", not ungrounded,
         f"cites passages that were not retrieved for this ticket: {', '.join(ungrounded)}")
    # Text-cited ids must be declared in kb_refs (keeps the decision row truthful).
    undeclared = sorted(set(_KB_ID.findall(text)) - set(draft.kb_refs))
    rule("CITE_DECLARED", not undeclared, f"ids cited in text but not in kb_refs: {', '.join(undeclared)}")

    leaked = bool(_CANARY.search(text)) or bool(_STAFF_ONLY.search(text)) or any(
        _shingles(text) & _shingles(body) for body in kb.internal_bodies
    )
    rule("NO_INTERNAL_TEXT", not leaked, "draft contains internal-only text or a canary reference")

    other = [a for a in kb.other_tenant_aliases if re.search(rf"\b{re.escape(a)}\b", text, re.I)]
    rule("NO_OTHER_TENANT", not other, f"mentions another tenant: {', '.join(sorted(set(other)))}")

    if kb.clinical_restricted:
        m = _CLINICAL_ADVICE.search(text)
        rule("NO_CLINICAL_ADVICE", m is None,
             f"clinical/medication language not allowed for this tenant: '{m.group(0) if m else ''}'")

    allowed_ids = set(_ENTITY_ID.findall(ticket.text())) | {ticket.customer_ref}
    foreign = sorted({i for i in _ENTITY_ID.findall(text) if i not in allowed_ids and i not in known})
    rule("NO_FOREIGN_IDS", not foreign, f"mentions ids that are not this customer's: {', '.join(foreign)}")

    # Every number (days, amounts, percentages) must come from a cited passage or the ticket.
    cited_text = " ".join(p.text for p in passages if p.kb_id in cited)
    source_numbers = {n.replace(",", "") for n in _NUMBER.findall(cited_text + " " + ticket.text())}
    stray = sorted({n for n in _NUMBER.findall(_KB_ID.sub("", text))
                    if n.replace(",", "") not in source_numbers})
    rule("NUMBERS_GROUNDED", not stray,
         f"numbers not found in cited passages or the ticket: {', '.join(stray)}")

    # Contact details (emails, links, phone numbers) are never invented.
    sources = (cited_text + " " + ticket.text()).lower()
    contacts = sorted({c.strip() for c in _CONTACT.findall(text) if c.strip().lower() not in sources})
    rule("NO_UNGROUNDED_CONTACTS", not contacts,
         f"{len(contacts)} contact detail(s) not found in cited passages or the ticket")

    n_words = len(text.split())
    rule("MAX_150_WORDS", n_words <= MAX_WORDS, f"{n_words} words exceeds {MAX_WORDS}")

    action = Action(action)
    if action == Action.ask_for_info:
        rule("ASK_HAS_QUESTION", "?" in text, "ask_for_info draft contains no question")
    if not cited:
        # Uncited text has no grounding, so it may not state facts or make commitments.
        m = _PROMISE.search(text)
        ok = action != Action.draft_reply or not passages
        rule("GROUNDED", ok, "draft cites no retrieved passage")
        rule("UNCITED_NO_COMMITMENTS", m is None and n_words <= UNCITED_MAX_WORDS,
             "uncited draft must not contain numbers, money or commitments and must be short")
    else:
        rule("GROUNDED", bool(cited & retrieved), "draft cites no retrieved passage")
        cited_sources = " ".join(f"{p.title} {p.text}" for p in passages if p.kb_id in cited)
        support, unsupported = content_support(
            text, f"{cited_sources} {ticket.text()} {' '.join(requested or [])}")
        rule("CONTENT_SUPPORTED", support >= MIN_CONTENT_SUPPORT,
             f"only {support:.0%} of the draft's content words appear in the cited passages; "
             f"unsupported: {', '.join(dict.fromkeys(unsupported[:6]))}",
             f"{support:.0%} of content words supported")

    return CheckResult(passed=all(r.passed for r in rules), rules=rules)


def load_kb_index(tenant_id: str) -> TenantKbIndex:
    from relay.db import tenant_registry, tenant_tx

    with tenant_tx(tenant_id) as conn:
        rows = conn.execute("SELECT kb_id, body, internal FROM kb_articles").fetchall()
    registry = tenant_registry()
    me = next(t for t in registry if t["tenant_id"] == tenant_id)
    others = tuple(a for t in registry if t["tenant_id"] != tenant_id
                   for a in [*t["aliases"], t["name"], t["kb_prefix"].rstrip("-")])
    return TenantKbIndex(
        tenant_id=tenant_id,
        public_ids=frozenset(r["kb_id"] for r in rows if not r["internal"]),
        internal_ids=frozenset(r["kb_id"] for r in rows if r["internal"]),
        internal_bodies=tuple(r["body"] for r in rows if r["internal"]),
        other_tenant_aliases=others,
        clinical_restricted=me["clinical_restricted"],
    )
