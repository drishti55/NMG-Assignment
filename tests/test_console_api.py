"""Console behaviour: nothing is processed unless a person asks for one specific ticket,
lists come back in one stable order, and each ticket's state comes from persisted records."""
import concurrent.futures
import time

import pytest
from fastapi.testclient import TestClient

from relay import api, store
from relay.config import get_settings
from relay.db import platform_tx, tenant_tx
from relay.ticket_view import allowed_actions, ticket_state
from tests.conftest import make_gateway
from tests.fakes import FakeBackend

AUTH = {"Authorization": "Bearer both-token"}
NW_FILE = "data/northwind/tickets.jsonl"
HD_FILE = "data/harbor/tickets.jsonl"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("RELAY_API_TOKENS", "both-token=alice@northwind|harbor,harbor-token=hana@harbor")
    get_settings.cache_clear()
    monkeypatch.setattr(api, "default_gateway", lambda: make_gateway(FakeBackend()))
    return TestClient(api.app)


def _work_counts() -> tuple[int, int]:
    with platform_tx() as conn:
        jobs = conn.execute("SELECT count(*) AS n FROM jobs").fetchone()["n"]
    calls = 0
    for t in ("northwind", "harbor"):
        with tenant_tx(t) as conn:
            calls += conn.execute("SELECT count(*) AS n FROM model_calls").fetchone()["n"]
    return jobs, calls


def _wait_for_decision(tenant: str, ticket_id: str, timeout: float = 15) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        d = store.get_decision(tenant, ticket_id)
        if d is not None:
            return d
        time.sleep(0.1)
    raise AssertionError(f"{ticket_id} was not processed within {timeout}s")


# ------------------------------------------------------------------ state derivation

@pytest.mark.parametrize("args, state", [
    ((None, None, False, None), "not_processed"),
    ((None, None, False, "queued"), "processing"),
    (("pending_approval", "pending", False, "done"), "waiting_approval"),
    (("pending_approval", "rejected", False, "done"), "processing"),
    (("pending_approval", "approved", True, "done"), "sent"),
    (("escalated", None, False, "done"), "escalated"),
    (("held_budget", None, False, "done"), "held_budget"),
    ((None, None, False, "dead"), "failed"),
])
def test_ticket_state_comes_from_persisted_records(args, state):
    assert ticket_state(*args) == state


def test_only_eligible_states_offer_processing():
    assert allowed_actions("not_processed") == ["process"]
    assert allowed_actions("waiting_approval") == ["approve", "reject"]
    assert "process" not in allowed_actions("sent") + allowed_actions("escalated")


# ------------------------------------------------------------------ nothing happens on its own

def test_import_stores_tickets_without_processing_them(client):
    out = client.post("/api/inbox/import", json={"file": NW_FILE}, headers=AUTH).json()
    assert len(out["imported"]) == 12 and out["rejected"] == []
    assert _work_counts() == (0, 0)  # no job, no model call
    rows = client.get("/api/tickets", headers=AUTH).json()
    assert {r["state"] for r in rows} == {"not_processed"}
    assert all(r["allowed_actions"] == ["process"] for r in rows)
    again = client.post("/api/inbox/import", json={"file": NW_FILE}, headers=AUTH).json()
    assert again["imported"] == [] and len(again["already_present"]) == 12


def test_import_refuses_files_outside_the_inbox_list(client):
    r = client.post("/api/inbox/import", json={"file": "../../etc/passwd"}, headers=AUTH)
    assert r.status_code == 400


def test_reading_every_page_creates_no_work(client):
    client.post("/api/inbox/import", json={"file": HD_FILE}, headers=AUTH)
    before = _work_counts()
    for path in ("/api/me", "/api/tenants", "/api/metrics", "/api/tickets", "/api/tickets/summary",
                 "/api/tickets/harbor/HD-T01", "/api/runs", "/api/approvals", "/api/escalations",
                 "/api/evaluations", "/api/inbox", "/api/graph"):
        assert client.get(path, headers=AUTH).status_code == 200, path
    assert _work_counts() == before


# ------------------------------------------------------------------ deterministic ordering

def test_ticket_list_has_one_stable_order_across_tenants(client):
    client.post("/api/inbox/import", json={"file": NW_FILE}, headers=AUTH)
    client.post("/api/inbox/import", json={"file": HD_FILE}, headers=AUTH)
    first = [(r["tenant_id"], r["ticket_id"]) for r in client.get("/api/tickets", headers=AUTH).json()]
    for _ in range(3):
        again = [(r["tenant_id"], r["ticket_id"]) for r in client.get("/api/tickets", headers=AUTH).json()]
        assert again == first
    rows = client.get("/api/tickets", headers=AUTH).json()
    received = [r["received_at"] for r in rows]
    assert received == sorted(received, reverse=True)  # newest first, tenants interleaved
    assert len({r["tenant_id"] for r in rows[:6]}) == 2


# ------------------------------------------------------------------ explicit processing

def test_processing_runs_exactly_the_requested_ticket_once(client):
    client.post("/api/inbox/import", json={"file": NW_FILE}, headers=AUTH)
    r = client.post("/api/tickets/northwind/NW-T01/process", headers=AUTH)
    assert r.status_code == 202 and r.json()["thread_id"] == "northwind:NW-T01:g1"
    d = _wait_for_decision("northwind", "NW-T01")
    assert d["status"] in ("pending_approval", "escalated")
    with platform_tx() as conn:
        jobs = conn.execute("SELECT tenant_id, ticket_id FROM jobs").fetchall()
    assert jobs == [{"tenant_id": "northwind", "ticket_id": "NW-T01"}]  # no other ticket touched
    states = {r["ticket_id"]: r["state"] for r in client.get("/api/tickets", headers=AUTH).json()}
    assert sum(s == "not_processed" for s in states.values()) == 11
    again = client.post("/api/tickets/northwind/NW-T01/process", headers=AUTH)
    assert again.status_code == 409


def test_concurrent_process_clicks_create_one_job(client):
    client.post("/api/inbox/import", json={"file": NW_FILE}, headers=AUTH)
    with concurrent.futures.ThreadPoolExecutor(4) as pool:
        codes = list(pool.map(lambda _: client.post("/api/tickets/northwind/NW-T09/process",
                                                    headers=AUTH).status_code, range(4)))
    assert sorted(codes).count(202) == 1 and codes.count(409) == 3
    _wait_for_decision("northwind", "NW-T09")
    with platform_tx() as conn:
        assert conn.execute("SELECT count(*) AS n FROM jobs").fetchone()["n"] == 1


def test_processing_requires_the_tenant_grant(client):
    client.post("/api/inbox/import", json={"file": NW_FILE}, headers=AUTH)
    r = client.post("/api/tickets/northwind/NW-T01/process", headers={"Authorization": "Bearer harbor-token"})
    assert r.status_code == 403
    assert _work_counts() == (0, 0)


def test_unknown_ticket_cannot_be_processed(client):
    assert client.post("/api/tickets/northwind/NOPE/process", headers=AUTH).status_code == 404
