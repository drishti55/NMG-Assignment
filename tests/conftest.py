"""Test fixtures. Tests use a dedicated database (RELAY_TEST_DATABASE, default relay_test)
on the same Postgres as the demo, created and migrated once per session. They need
Postgres (RLS cannot be tested without it) but never Ollama."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest

from relay.config import get_settings

ROOT = Path(__file__).resolve().parent.parent


def _with_db(url: str, db: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(parts._replace(path=f"/{db}"))


_base = get_settings()
TEST_DB = _base.test_database
if TEST_DB == urlsplit(_base.database_url).path.lstrip("/"):
    raise RuntimeError("RELAY_TEST_DATABASE must differ from the application database")
ADMIN_URL = _with_db(_base.admin_database_url, TEST_DB)
os.environ["RELAY_ADMIN_DATABASE_URL"] = ADMIN_URL
os.environ["RELAY_DATABASE_URL"] = _with_db(_base.database_url, TEST_DB)
# Tests never write the committed out/ and eval/runs/ files.
_scratch = tempfile.mkdtemp(prefix="relay-test-")
os.environ["RELAY_OUT_DIR"] = os.path.join(_scratch, "out")
os.environ["RELAY_EVAL_DIR"] = os.path.join(_scratch, "eval")
os.environ.setdefault("RELAY_BACKOFF_BASE_S", "0")
os.environ.setdefault("RELAY_BACKOFF_MAX_S", "0")
get_settings.cache_clear()

from relay import db as relay_db  # noqa: E402
from relay import orchestrator  # noqa: E402
from relay.gateway import ModelGateway, DbMeter  # noqa: E402
from tests.fakes import FakeBackend  # noqa: E402

BUSINESS_TABLES = (
    "events, model_calls, audit_log, cases, outbox, approvals, decisions, workflows, jobs, "
    "tickets, runs, rejections, eval_runs, checkpoint_writes, checkpoint_blobs, checkpoints"
)


@pytest.fixture(scope="session", autouse=True)
def database():
    admin_root = _with_db(_base.admin_database_url, "postgres")
    try:
        with psycopg.connect(admin_root, autocommit=True, connect_timeout=5) as conn:
            conn.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                         "WHERE datname = %s AND pid <> pg_backend_pid()", (TEST_DB,))
            conn.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}"')
            conn.execute(f'CREATE DATABASE "{TEST_DB}"')
    except psycopg.OperationalError as e:
        pytest.exit(f"Postgres is required for the test suite (run `make db`): {e}", returncode=2)
    relay_db.reset_pools()
    relay_db.migrate(admin_url=ADMIN_URL)
    from relay.seed import seed_kb

    seed_kb(ModelGateway(FakeBackend(), None), ROOT / "data")
    yield
    relay_db.reset_pools()


@pytest.fixture(autouse=True)
def clean(database):
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        conn.execute(f"TRUNCATE {BUSINESS_TABLES} RESTART IDENTITY CASCADE")
    orchestrator.checkpointer.cache_clear()
    get_settings.cache_clear()
    yield


@pytest.fixture
def admin_conn():
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        yield conn


def make_gateway(backend: FakeBackend | None = None) -> ModelGateway:
    return ModelGateway(backend or FakeBackend(), DbMeter(), get_settings())
