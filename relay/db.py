"""Database access. Every tenant read/write goes through `tenant_tx`, which binds the
transaction-local setting app.tenant_id that the RLS policies check.

Two connection roles:
  * admin (schema owner)   -> migrations and tenant registry seeding only
  * relay_app (no BYPASSRLS) -> everything else, including the LangGraph checkpointer
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from relay.config import ROOT, get_settings


class UnknownTenantError(ValueError):
    """Raised for any tenant id not in the registry. There is no default tenant."""


def _configure(conn: psycopg.Connection) -> None:
    register_vector(conn)


@lru_cache
def app_pool() -> ConnectionPool:
    s = get_settings()
    return ConnectionPool(
        s.database_url, min_size=1, max_size=10, open=True,
        kwargs={"row_factory": dict_row}, configure=_configure,
    )


def clear_tenant(conn: psycopg.Connection) -> None:
    """Unbind any session-level tenant. '' matches no tenant, so RLS fails closed."""
    conn.execute("SELECT set_config('app.tenant_id', '', false)")


@lru_cache
def checkpoint_pool() -> ConnectionPool:
    """Pool for PostgresSaver (it requires autocommit + dict rows). Autocommit means a
    transaction-local tenant setting would not survive between its statements, so the
    tenant is bound per *session* (see orchestrator.tenant_checkpointer) and cleared
    whenever a connection goes back to the pool."""
    s = get_settings()
    return ConnectionPool(
        s.database_url, min_size=1, max_size=10, open=True,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        configure=clear_tenant, reset=clear_tenant,
    )


def reset_pools() -> None:
    """Close and forget pools (tests switch databases)."""
    for factory in (app_pool, checkpoint_pool):
        if factory.cache_info().currsize:
            factory().close()
        factory.cache_clear()


@lru_cache
def tenant_ids() -> frozenset[str]:
    with app_pool().connection() as conn:
        rows = conn.execute("SELECT tenant_id FROM tenants").fetchall()
    return frozenset(r["tenant_id"] for r in rows)


def require_tenant(tenant_id: str) -> str:
    if tenant_id not in tenant_ids():
        raise UnknownTenantError(f"unknown tenant {tenant_id!r}")
    return tenant_id


@contextmanager
def tenant_tx(tenant_id: str) -> Iterator[psycopg.Connection]:
    """A transaction in which RLS exposes exactly one tenant's rows."""
    require_tenant(tenant_id)
    with app_pool().connection() as conn, conn.transaction():
        conn.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
        yield conn


@contextmanager
def platform_tx() -> Iterator[psycopg.Connection]:
    """A transaction with no tenant bound: tenant tables return zero rows here."""
    with app_pool().connection() as conn, conn.transaction():
        yield conn


def tenant_registry() -> list[dict]:
    with platform_tx() as conn:
        return conn.execute(
            "SELECT tenant_id, name, kb_prefix, aliases, clinical_restricted FROM tenants "
            "ORDER BY tenant_id"
        ).fetchall()


# ------------------------------------------------------------------ admin operations

def migrate(admin_url: str | None = None, app_url: str | None = None) -> None:
    """Create LangGraph's checkpoint tables, apply Alembic migrations (which put those
    tables under RLS), and register tenants."""
    s = get_settings()
    admin_url = admin_url or s.admin_database_url
    env = {"RELAY_MIGRATION_URL": admin_url, "RELAY_APP_DB_PASSWORD": s.app_db_password}
    # LangGraph's checkpoint tables first (idempotent), owned by the admin role.
    from langgraph.checkpoint.postgres import PostgresSaver

    with psycopg.connect(admin_url, autocommit=True, prepare_threshold=0,
                         row_factory=dict_row) as cp_conn:
        PostgresSaver(cp_conn).setup()
    subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ROOT / "alembic.ini"), "upgrade", "head"],
        check=True, cwd=ROOT, env={**os.environ, **env},
    )
    tenants = json.loads((ROOT / "config" / "tenants.json").read_text())
    with psycopg.connect(admin_url, autocommit=True) as conn:
        for t in tenants:
            conn.execute(
                """INSERT INTO tenants (tenant_id, name, kb_prefix, aliases, clinical_restricted)
                   VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (tenant_id) DO UPDATE
                   SET name = EXCLUDED.name, kb_prefix = EXCLUDED.kb_prefix,
                       aliases = EXCLUDED.aliases,
                       clinical_restricted = EXCLUDED.clinical_restricted""",
                (t["tenant_id"], t["name"], t["kb_prefix"], t["aliases"],
                 t.get("clinical_restricted", False)),
            )
    tenant_ids.cache_clear()
