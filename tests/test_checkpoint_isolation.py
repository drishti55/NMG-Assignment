"""LangGraph checkpoint tables are tenant-isolated by the database (RLS, migration 0002)
and by the orchestrator (every operation names its tenant). A thread id never grants access."""
import psycopg
import pytest

from relay import approvals
from relay.db import UnknownTenantError, checkpoint_pool, platform_tx, tenant_tx
from relay.orchestrator import Orchestrator, ThreadAccessError, tenant_checkpointer
from tests.conftest import make_gateway
from tests.test_workflow import _pending, _run

NW_THREAD = "northwind:NW-1:g1"
TABLES = ("checkpoints", "checkpoint_blobs", "checkpoint_writes")


@pytest.fixture
def paused_northwind_thread(tmp_path):
    orch, *_ = _run(tmp_path, "return")  # pauses at the approval interrupt
    assert orch.phase("northwind", NW_THREAD) == "paused"
    return orch


def _count(conn, table: str) -> int:
    return conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]


# ------------------------------------------------------------------ database layer

def test_checkpoint_tables_force_rls(admin_conn):
    rows = admin_conn.execute(
        "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
        "WHERE relname IN ('checkpoints','checkpoint_blobs','checkpoint_writes')").fetchall()
    assert len(rows) == 3 and all(r[1] and r[2] for r in rows), rows


def test_other_tenant_cannot_read_checkpoints(paused_northwind_thread):
    with tenant_tx("northwind") as conn:
        assert all(_count(conn, t) > 0 for t in TABLES)
    with tenant_tx("harbor") as conn:
        assert all(_count(conn, t) == 0 for t in TABLES)
        # An explicit predicate on the other tenant's thread id changes nothing.
        assert conn.execute("SELECT * FROM checkpoint_blobs WHERE thread_id = %s",
                            (NW_THREAD,)).fetchall() == []


@pytest.mark.parametrize("setting", [None, ""])
def test_missing_tenant_context_sees_no_checkpoints(paused_northwind_thread, setting):
    with platform_tx() as conn:
        if setting is not None:
            conn.execute("SELECT set_config('app.tenant_id', %s, true)", (setting,))
        assert all(_count(conn, t) == 0 for t in TABLES)


def test_other_tenant_cannot_write_or_modify_checkpoints(paused_northwind_thread):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with tenant_tx("harbor") as conn:  # forge a checkpoint for a northwind thread
            conn.execute("INSERT INTO checkpoints (thread_id, checkpoint_ns, checkpoint_id, checkpoint) "
                         "VALUES (%s, '', 'forged', '{}'::jsonb)", (NW_THREAD,))
    with tenant_tx("harbor") as conn:
        assert conn.execute("UPDATE checkpoints SET metadata = '{}'::jsonb WHERE thread_id = %s",
                            (NW_THREAD,)).rowcount == 0
        assert conn.execute("DELETE FROM checkpoint_blobs WHERE thread_id = %s",
                            (NW_THREAD,)).rowcount == 0
    with tenant_tx("northwind") as conn:
        assert conn.execute("SELECT count(*) AS n FROM checkpoints WHERE checkpoint_id = 'forged'"
                            ).fetchone()["n"] == 0
        assert _count(conn, "checkpoint_blobs") > 0


def test_missing_tenant_context_cannot_write_checkpoints():
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with platform_tx() as conn:
            conn.execute("INSERT INTO checkpoints (thread_id, checkpoint_ns, checkpoint_id, checkpoint) "
                         "VALUES ('northwind:X:g1', '', 'c1', '{}'::jsonb)")


def test_tenant_ids_cannot_contain_the_thread_separator(admin_conn):
    with pytest.raises(psycopg.errors.CheckViolation):
        admin_conn.execute("INSERT INTO tenants (tenant_id, name, kb_prefix) "
                           "VALUES ('harbor:evil', 'x', 'X-')")


def test_app_role_cannot_rewrite_langgraph_schema_versions():
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with platform_tx() as conn:
            conn.execute("DELETE FROM checkpoint_migrations")


# ------------------------------------------------------------------ checkpointer / orchestrator

def test_tenant_bound_checkpointer_cannot_load_another_tenants_thread(paused_northwind_thread):
    cfg = {"configurable": {"thread_id": NW_THREAD, "checkpoint_ns": ""}}
    with tenant_checkpointer("northwind") as saver:
        assert saver.get_tuple(cfg) is not None
    with tenant_checkpointer("harbor") as saver:  # bypasses the orchestrator's own check
        assert saver.get_tuple(cfg) is None
        assert list(saver.list(None)) == []


def test_orchestrator_refuses_cross_tenant_read_and_resume(paused_northwind_thread):
    orch = Orchestrator(make_gateway())
    with pytest.raises(ThreadAccessError):
        orch.thread_state("harbor", NW_THREAD)
    aid = _pending()[0]["approval_id"]
    payload = {"approval_id": aid, "decision": "approved", "actor": "mallory", "reason": None}
    with pytest.raises(ThreadAccessError):
        orch.resume_with_decision("harbor", NW_THREAD, payload)
    assert orch.phase("northwind", NW_THREAD) == "paused"
    with tenant_tx("northwind") as conn:
        assert _count(conn, "outbox") == 0


def test_orchestrator_rejects_unknown_tenants(paused_northwind_thread):
    orch = Orchestrator(make_gateway())
    with pytest.raises(UnknownTenantError):
        orch.thread_state("acme", "acme:X:g1")
    with pytest.raises(UnknownTenantError):
        with tenant_checkpointer(""):
            pass


def test_approver_without_the_tenant_grant_cannot_resume(paused_northwind_thread):
    aid = _pending()[0]["approval_id"]
    with pytest.raises(approvals.ApprovalError):
        approvals.approve(Orchestrator(make_gateway()), aid, "hana", tenants=frozenset({"harbor"}))
    assert _pending()[0]["status"] == "pending"


def test_tenant_binding_never_leaks_back_into_the_pool(paused_northwind_thread):
    with pytest.raises(RuntimeError):
        with tenant_checkpointer("northwind"):
            raise RuntimeError("operation failed mid-way")
    pool = checkpoint_pool()
    for _ in range(pool.max_size):  # every pooled connection is unbound again
        with pool.connection() as conn:
            assert conn.execute("SELECT current_setting('app.tenant_id', true) AS t"
                                ).fetchone()["t"] in (None, "")
            assert _count(conn, "checkpoints") == 0


def test_resume_and_crash_recovery_still_work_under_rls(paused_northwind_thread):
    aid = _pending()[0]["approval_id"]
    out = approvals.approve(Orchestrator(make_gateway()), aid, "alice")  # fresh process view
    assert (out.thread_phase, out.outbox_written) == ("finished", True)
