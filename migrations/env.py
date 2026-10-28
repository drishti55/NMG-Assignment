"""Alembic environment. Migrations run as the admin (schema owner) role, never as relay_app."""
import os

from alembic import context
from sqlalchemy import create_engine, pool

from relay.config import get_settings


def _url() -> str:
    url = os.environ.get("RELAY_MIGRATION_URL") or get_settings().admin_database_url
    return url.replace("postgresql://", "postgresql+psycopg://", 1)


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
