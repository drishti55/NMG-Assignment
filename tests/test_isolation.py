"""C6 tenant isolation: RLS fails closed, tenants cannot read or write each other's rows."""
from datetime import datetime, timezone

import psycopg
import pytest

from relay import store
from relay.contracts import Ticket
from relay.db import UnknownTenantError, platform_tx, tenant_tx
from relay.queue import enqueue_inbox


def _ticket(tenant: str, tid: str, body: str = "hello") -> Ticket:
    return Ticket(id=tid, tenant=tenant, customer_ref="C-1", subject="s", body=body,
                  received_at=datetime(2026, 10, 20, tzinfo=timezone.utc))


def test_app_role_is_not_superuser_and_cannot_bypass_rls():
    with platform_tx() as conn:
        r = conn.execute("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user").fetchone()
    assert r == {"rolsuper": False, "rolbypassrls": False}


def test_every_tenant_table_forces_rls(admin_conn):
    rows = admin_conn.execute(
        "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
        "WHERE relname IN ('kb_articles','tickets','workflows','decisions','approvals','outbox',"
        "'cases','audit_log','model_calls','events')").fetchall()
    assert len(rows) == 10
    assert all(r[1] and r[2] for r in rows), rows


def test_missing_tenant_context_returns_zero_rows():
    store.upsert_ticket(_ticket("northwind", "T-1"))
    with tenant_tx("northwind") as conn:
        assert conn.execute("SELECT count(*) AS n FROM kb_articles").fetchone()["n"] == 7
    with platform_tx() as conn:  # no app.tenant_id set at all
        assert conn.execute("SELECT count(*) AS n FROM kb_articles").fetchone()["n"] == 0
        assert conn.execute("SELECT count(*) AS n FROM tickets").fetchone()["n"] == 0
    with platform_tx() as conn:  # explicitly empty setting
        conn.execute("SELECT set_config('app.tenant_id', '', true)")
        assert conn.execute("SELECT count(*) AS n FROM kb_articles").fetchone()["n"] == 0


def test_missing_tenant_context_cannot_insert():
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with platform_tx() as conn:
            conn.execute("INSERT INTO cases (case_id, tenant_id, ticket_id, kind, owner, severity, "
                         "reason_code, reason) VALUES ('c1','northwind','T','escalation','x','sev1','r','r')")


def test_tenant_cannot_read_other_tenant_rows():
    store.upsert_ticket(_ticket("northwind", "NW-SECRET"))
    store.upsert_ticket(_ticket("harbor", "HD-SECRET"))
    with tenant_tx("harbor") as conn:
        ids = {r["ticket_id"] for r in conn.execute("SELECT ticket_id FROM tickets").fetchall()}
        kb = {r["kb_id"] for r in conn.execute("SELECT kb_id FROM kb_articles").fetchall()}
        # Even an explicit predicate for the other tenant returns nothing.
        other = conn.execute("SELECT * FROM tickets WHERE tenant_id = 'northwind'").fetchall()
    assert ids == {"HD-SECRET"}
    assert kb and all(k.startswith("HD-KB-") for k in kb)
    assert other == []


def test_tenant_cannot_write_or_modify_other_tenant_rows():
    store.upsert_ticket(_ticket("northwind", "NW-1", body="original"))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with tenant_tx("harbor") as conn:
            conn.execute("INSERT INTO tickets (tenant_id, ticket_id, customer_ref, subject, body, "
                         "received_at, content_hash) VALUES ('northwind','X','c','s','b',now(),'h')")
    with tenant_tx("harbor") as conn:
        updated = conn.execute("UPDATE tickets SET body = 'pwned' WHERE ticket_id = 'NW-1'").rowcount
    assert updated == 0
    with tenant_tx("northwind") as conn:
        assert conn.execute("SELECT body FROM tickets").fetchone()["body"] == "original"


def test_app_role_cannot_rewrite_the_audit_log_or_outbox():
    with tenant_tx("northwind") as conn:
        store.audit(conn, "northwind", ticket_id="T", thread_id=None, actor="a", action="x",
                    from_state=None, to_state=None)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with tenant_tx("northwind") as conn:
            conn.execute("UPDATE audit_log SET actor = 'someone else'")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with tenant_tx("northwind") as conn:
            conn.execute("DELETE FROM outbox")


def test_unknown_tenant_has_no_session_and_no_default():
    with pytest.raises(UnknownTenantError):
        with tenant_tx("acme"):
            pass
    with pytest.raises(UnknownTenantError):
        with tenant_tx(""):
            pass


def test_unknown_tenant_ticket_is_rejected_and_logged_never_processed(tmp_path):
    inbox = tmp_path / "inbox.jsonl"
    inbox.write_text(
        '{"id": "X-1", "tenant": "acme", "customer_ref": "c", "subject": "s", "body": "b", '
        '"received_at": "2026-10-20T09:00:00+05:30"}\n'
        '{"id": "X-2", "tenant": "northwind", "customer_ref": "c", "subject": "s", "body": "b", '
        '"received_at": "2026-10-20T09:00:00+05:30"}\n'
        'not json\n')
    store.create_run("r1", "inbox", str(inbox), 10_000)
    report = enqueue_inbox(inbox, "r1")
    assert report.enqueued == ["northwind/X-2"]
    assert [r["ticket_id"] for r in report.rejected] == ["X-1", None]
    with platform_tx() as conn:
        jobs = conn.execute("SELECT tenant_id FROM jobs").fetchall()
        rejections = conn.execute("SELECT raw_tenant, ticket_id FROM rejections ORDER BY 1").fetchall()
    assert [j["tenant_id"] for j in jobs] == ["northwind"]
    assert {"raw_tenant": "acme", "ticket_id": "X-1"} in rejections


def test_events_are_tenant_scoped():
    store.emit_event("harbor", run_id="r", thread_id="t", ticket_id="HD-1", event_type="x")
    store.emit_event("northwind", run_id="r", thread_id="t2", ticket_id="NW-1", event_type="x")
    with tenant_tx("northwind") as conn:
        rows = conn.execute("SELECT ticket_id FROM events").fetchall()
    assert [r["ticket_id"] for r in rows] == ["NW-1"]
