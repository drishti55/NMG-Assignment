"""C4 deterministic checker: each rule blocks the unsafe draft it targets."""
from datetime import datetime, timezone

import pytest

from relay.agents.checker import TenantKbIndex, check_draft, load_kb_index
from relay.contracts import DraftResult, Passage, Ticket

RETURNS = Passage(kb_id="NW-KB-01", title="Returns and refunds", score=0.8,
                  text="Unused items can be returned within 30 days of delivery with the original "
                       "invoice. Refunds go to the original payment method within 7 business days.")
CLINIC = Passage(kb_id="HD-KB-05", title="Clinical questions", score=0.8,
                 text="For severe pain, facial swelling or bleeding that will not stop, advise the "
                      "patient to call the clinic emergency line.")


def _ticket(tenant="northwind", body="Can I return my unused helmet? Order NW-55821.", ref="C-1001"):
    return Ticket(id="T1", tenant=tenant, customer_ref=ref, subject="Return", body=body,
                  received_at=datetime(2026, 10, 20, tzinfo=timezone.utc))


@pytest.fixture
def nw() -> TenantKbIndex:
    return load_kb_index("northwind")


@pytest.fixture
def hd() -> TenantKbIndex:
    return load_kb_index("harbor")


def _failed(result) -> set[str]:
    return {r.rule_id for r in result.failures}


GOOD = ("Unused items can be returned within 30 days of delivery with the original invoice "
        "[NW-KB-01]. Refunds go to the original payment method within 7 business days.")


def test_grounded_draft_passes(nw):
    r = check_draft(DraftResult(reply=GOOD, kb_refs=["NW-KB-01"]), "draft_reply", [RETURNS], _ticket(), nw)
    assert r.passed, r.failures


def test_cited_id_must_exist_in_tenant_kb(nw):
    r = check_draft(DraftResult(reply=GOOD + " See [NW-KB-99].", kb_refs=["NW-KB-01", "NW-KB-99"]),
                    "draft_reply", [RETURNS], _ticket(), nw)
    assert "CITE_EXISTS" in _failed(r)


def test_cited_id_must_not_be_internal(nw):
    r = check_draft(DraftResult(reply=GOOD, kb_refs=["NW-KB-01", "NW-KB-07"]), "draft_reply",
                    [RETURNS], _ticket(), nw)
    assert "CITE_NOT_INTERNAL" in _failed(r)


def test_other_tenant_kb_id_is_rejected(nw):
    r = check_draft(DraftResult(reply=GOOD + " [HD-KB-01]", kb_refs=["NW-KB-01", "HD-KB-01"]),
                    "draft_reply", [RETURNS], _ticket(), nw)
    assert {"CITE_EXISTS", "NO_OTHER_TENANT"} <= _failed(r)


def test_canary_code_is_blocked(nw):
    r = check_draft(DraftResult(reply=GOOD + " Ref CANARY-NW-4K9P.", kb_refs=["NW-KB-01"]),
                    "draft_reply", [RETURNS], _ticket(), nw)
    assert "NO_INTERNAL_TEXT" in _failed(r)


def test_internal_article_text_is_blocked_even_paraphrase_free(nw):
    leak = GOOD + " A supervisor may approve a goodwill credit of up to INR 500."
    r = check_draft(DraftResult(reply=leak, kb_refs=["NW-KB-01"]), "draft_reply", [RETURNS], _ticket(), nw)
    assert "NO_INTERNAL_TEXT" in _failed(r)
    # The diagnostic never echoes the internal text.
    assert all("goodwill" not in f.message for f in r.failures)


def test_other_tenant_name_is_blocked(nw):
    r = check_draft(DraftResult(reply=GOOD + " Harbor Dental has a similar policy.", kb_refs=["NW-KB-01"]),
                    "draft_reply", [RETURNS], _ticket(), nw)
    assert "NO_OTHER_TENANT" in _failed(r)


@pytest.mark.parametrize("advice", [
    "Take 400 mg ibuprofen every 6 hours.",
    "It sounds like an infection.",
    "You may have gingivitis.",
    "Rinse with warm salt water.",
])
def test_harbor_medication_and_diagnosis_language_is_blocked(hd, advice):
    reply = f"Please call the clinic emergency line [HD-KB-05]. {advice}"
    t = _ticket("harbor", body="My face is swelling and pain is severe. 400 mg ok? every 6 hours?", ref="P-1")
    r = check_draft(DraftResult(reply=reply, kb_refs=["HD-KB-05"]), "draft_reply", [CLINIC], t, hd)
    assert "NO_CLINICAL_ADVICE" in _failed(r)


def test_harbor_emergency_wording_is_allowed(hd):
    reply = ("For severe pain, facial swelling or bleeding that will not stop, please call the clinic "
             "emergency line [HD-KB-05].")
    t = _ticket("harbor", body="Severe swelling after extraction", ref="P-1")
    r = check_draft(DraftResult(reply=reply, kb_refs=["HD-KB-05"]), "draft_reply", [CLINIC], t, hd)
    assert r.passed, r.failures


def test_more_than_150_words_is_blocked(nw):
    long = " ".join([GOOD] * 6)
    r = check_draft(DraftResult(reply=long, kb_refs=["NW-KB-01"]), "draft_reply", [RETURNS], _ticket(), nw)
    assert "MAX_150_WORDS" in _failed(r)


def test_ids_of_other_customers_are_blocked(nw):
    r = check_draft(DraftResult(reply=GOOD + " Customer C-2002 had the same issue with NW-50000.",
                                kb_refs=["NW-KB-01"]), "draft_reply", [RETURNS], _ticket(), nw)
    assert "NO_FOREIGN_IDS" in _failed(r)


def test_customers_own_ids_are_allowed(nw):
    r = check_draft(DraftResult(reply=GOOD.replace("Unused", "For order NW-55821, unused"),
                                kb_refs=["NW-KB-01"]), "draft_reply", [RETURNS], _ticket(), nw)
    assert "NO_FOREIGN_IDS" not in _failed(r)


def test_invented_numbers_are_blocked(nw):
    r = check_draft(DraftResult(reply=GOOD + " You will receive it in 3 days.", kb_refs=["NW-KB-01"]),
                    "draft_reply", [RETURNS], _ticket(), nw)
    assert "NUMBERS_GROUNDED" in _failed(r)


def test_invented_contact_details_are_blocked(nw):
    r = check_draft(DraftResult(reply=GOOD + " Email returns@northwind-help.com.", kb_refs=["NW-KB-01"]),
                    "draft_reply", [RETURNS], _ticket(), nw)
    assert "NO_UNGROUNDED_CONTACTS" in _failed(r)


def test_unsupported_content_is_blocked(nw):
    reply = "Please contact our sales department about special corporate pricing [NW-KB-01]."
    r = check_draft(DraftResult(reply=reply, kb_refs=["NW-KB-01"]), "draft_reply", [RETURNS], _ticket(), nw)
    assert "CONTENT_SUPPORTED" in _failed(r)


def test_citing_a_passage_that_was_not_retrieved_is_blocked(nw):
    r = check_draft(DraftResult(reply=GOOD + " [NW-KB-02]", kb_refs=["NW-KB-01", "NW-KB-02"]),
                    "draft_reply", [RETURNS], _ticket(), nw)
    assert "CITE_RETRIEVED" in _failed(r)


def test_uncited_answer_is_blocked_when_passages_exist(nw):
    r = check_draft(DraftResult(reply="Sure, no problem at all.", kb_refs=[]), "draft_reply",
                    [RETURNS], _ticket(), nw)
    assert "GROUNDED" in _failed(r)


def test_ask_for_info_needs_a_question(nw):
    r = check_draft(DraftResult(reply="Send photos of the defect [NW-KB-01].", kb_refs=["NW-KB-01"]),
                    "ask_for_info", [RETURNS], _ticket(), nw)
    assert "ASK_HAS_QUESTION" in _failed(r)


def test_uncited_courtesy_reply_cannot_make_commitments(nw):
    r = check_draft(DraftResult(reply="Thanks! We will refund you 500 rupees.", kb_refs=[]),
                    "draft_reply", [], _ticket(body="Thanks for the help"), nw)
    assert "UNCITED_NO_COMMITMENTS" in _failed(r)
