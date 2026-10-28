"""Put LangGraph's checkpoint tables under the same tenant RLS as every other tenant table.

LangGraph's PostgresSaver tables have no tenant column. Each gets a column that
Postgres itself derives from the thread id (`<tenant>:<ticket>:g<n>`), and the
standard fail-closed policy. Authorization still comes from the session's bound
tenant (app.tenant_id), never from the thread id: USING hides other tenants' rows,
and WITH CHECK refuses to write a row whose thread id names a different tenant.
Tenant ids may not contain ':', so the prefix cannot be spoofed.

Requires the checkpoint tables to exist: `relay.db.migrate()` runs
PostgresSaver.setup() before Alembic.

Revision ID: 0002
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

CHECKPOINT_TABLES = ("checkpoints", "checkpoint_blobs", "checkpoint_writes")


def upgrade() -> None:
    op.execute("ALTER TABLE tenants ADD CONSTRAINT tenant_id_format "
               "CHECK (tenant_id ~ '^[a-z0-9][a-z0-9_-]*$')")
    for t in CHECKPOINT_TABLES:
        op.execute(f"ALTER TABLE {t} ADD COLUMN tenant_id text "
                   "GENERATED ALWAYS AS (split_part(thread_id, ':', 1)) STORED")
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY tenant_isolation ON {t}
              USING (tenant_id = current_setting('app.tenant_id', true))
              WITH CHECK (tenant_id = current_setting('app.tenant_id', true))
        """)
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {t} TO relay_app")
    # LangGraph's own schema-version table: the app reads it, only migrations write it.
    op.execute("REVOKE ALL ON checkpoint_migrations FROM relay_app")
    op.execute("GRANT SELECT ON checkpoint_migrations TO relay_app")


def downgrade() -> None:
    for t in CHECKPOINT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {t}")
        op.execute(f"ALTER TABLE {t} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} DROP COLUMN IF EXISTS tenant_id")
    op.execute("ALTER TABLE tenants DROP CONSTRAINT IF EXISTS tenant_id_format")
