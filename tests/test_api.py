"""API authorization and approval endpoints. The browser never chooses its tenant scope."""
import pytest
from fastapi.testclient import TestClient

from relay import api
from relay.config import get_settings
from tests.conftest import make_gateway
from tests.test_workflow import _pending, _run

HARBOR_ONLY = {"Authorization": "Bearer harbor-token"}
BOTH = {"Authorization": "Bearer both-token"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("RELAY_API_TOKENS", "both-token=alice@northwind|harbor,harbor-token=hana@harbor")
    get_settings.cache_clear()
    monkeypatch.setattr(api, "default_gateway", make_gateway)
    return TestClient(api.app)


def test_requests_without_a_valid_token_are_refused(client):
    assert client.get("/api/tickets").status_code == 401
    assert client.get("/api/tickets", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_operator_cannot_read_a_tenant_it_was_not_granted(client, tmp_path):
    _run(tmp_path, "return", "clinical")
    assert client.get("/api/tickets?tenant=northwind", headers=HARBOR_ONLY).status_code == 403
    assert client.get("/api/tickets/northwind/NW-1", headers=HARBOR_ONLY).status_code == 403
    assert client.get("/api/runs/northwind:NW-1:g1", headers=HARBOR_ONLY).status_code == 403
    rows = client.get("/api/tickets", headers=HARBOR_ONLY).json()
    assert {r["tenant_id"] for r in rows} == {"harbor"}
    events = client.get("/api/metrics", headers=HARBOR_ONLY).json()["recent_events"]
    assert events and {e["tenant_id"] for e in events} == {"harbor"}


def test_unknown_tenant_is_rejected(client):
    assert client.get("/api/tickets?tenant=acme", headers=BOTH).status_code == 403


def test_operator_cannot_approve_another_tenants_draft(client, tmp_path):
    _run(tmp_path, "return")
    aid = _pending()[0]["approval_id"]
    assert client.post(f"/api/approvals/{aid}/approve", headers=HARBOR_ONLY).status_code == 409
    assert _pending()[0]["status"] == "pending"


def test_approve_via_api_is_idempotent_and_records_the_operator(client, tmp_path):
    _run(tmp_path, "return")
    aid = _pending()[0]["approval_id"]
    first = client.post(f"/api/approvals/{aid}/approve", headers=BOTH).json()
    second = client.post(f"/api/approvals/{aid}/approve", headers=BOTH).json()
    assert (first["changed"], first["outbox_written"]) == (True, True)
    assert (second["changed"], second["outbox_written"]) == (False, True)
    detail = client.get(f"/api/approvals/{aid}", headers=BOTH).json()
    assert detail["outbox"]["approved_by"] == "alice"


def test_reject_via_api_requires_a_reason(client, tmp_path):
    _run(tmp_path, "return")
    aid = _pending()[0]["approval_id"]
    assert client.post(f"/api/approvals/{aid}/reject", json={"reason": ""}, headers=BOTH).status_code == 422


def test_graph_endpoint_exposes_the_compiled_langgraph(client):
    g = client.get("/api/graph", headers=BOTH).json()
    assert {"triage_agent", "retrieve_knowledge", "draft_reply", "safety_check",
            "await_human_approval", "write_approved_outbox"} <= set(g["nodes"])
    assert {"source": "safety_check", "target": "record_decision", "conditional": True} in g["edges"]
