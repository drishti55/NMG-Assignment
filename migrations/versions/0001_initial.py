"""Initial schema: tenants, KB with pgvector, tickets, workflows, decisions, approvals,
outbox, cases, audit, model calls, events, queue, runs. Row-level security on every
tenant-owned table, bound to the transaction-local setting app.tenant_id.

Revision ID: 0001
"""
import os

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# Tables that hold tenant data. Each gets ENABLE + FORCE RLS and one policy.
TENANT_TABLES = (
    "kb_articles", "tickets", "workflows", "decisions", "approvals", "outbox",
    "cases", "audit_log", "model_calls", "events",
)
APPEND_ONLY = ("audit_log", "outbox", "model_calls", "events")

SCHEMA = """
CREATE EXTENSION IF NOT EXISTS vector;

-- Platform registry. Not tenant data: names are needed by the checker to detect leaks.
CREATE TABLE tenants (
    tenant_id   text PRIMARY KEY,
    name        text NOT NULL,
    kb_prefix   text NOT NULL,
    aliases     text[] NOT NULL DEFAULT '{}',
    clinical_restricted boolean NOT NULL DEFAULT false,  -- clinical questions always escalate
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE kb_articles (
    tenant_id   text NOT NULL REFERENCES tenants(tenant_id),
    kb_id       text NOT NULL,
    title       text NOT NULL,
    body        text NOT NULL,
    internal    boolean NOT NULL,
    embedding   vector(768) NOT NULL,
    embed_model text NOT NULL,
    tsv         tsvector GENERATED ALWAYS AS (to_tsvector('english', title || ' ' || body)) STORED,
    PRIMARY KEY (tenant_id, kb_id)
);
CREATE INDEX kb_articles_tsv_idx ON kb_articles USING gin (tsv);

CREATE TABLE tickets (
    tenant_id    text NOT NULL REFERENCES tenants(tenant_id),
    ticket_id    text NOT NULL,
    customer_ref text NOT NULL,
    subject      text NOT NULL,
    body         text NOT NULL,
    received_at  timestamptz NOT NULL,
    content_hash text NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, ticket_id)
);

-- Runs span tenants (one inbox can hold both); they hold counters, never ticket content.
CREATE TABLE runs (
    run_id      text PRIMARY KEY,
    kind        text NOT NULL,          -- inbox | eval
    source      text,
    started_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    tokens_in   bigint NOT NULL DEFAULT 0,
    tokens_out  bigint NOT NULL DEFAULT 0,
    token_budget bigint NOT NULL
);

-- One LangGraph thread per (ticket, generation). Mirrors the checkpoint for querying.
CREATE TABLE workflows (
    thread_id    text PRIMARY KEY,
    tenant_id    text NOT NULL REFERENCES tenants(tenant_id),
    ticket_id    text NOT NULL,
    run_id       text NOT NULL REFERENCES runs(run_id),
    status       text NOT NULL,     -- running | awaiting_approval | completed | failed | held_budget
    current_node text,
    started_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id, ticket_id) REFERENCES tickets(tenant_id, ticket_id)
);
CREATE INDEX workflows_ticket_idx ON workflows (tenant_id, ticket_id);

-- Exactly one business decision per ticket (C7 idempotent re-runs).
CREATE TABLE decisions (
    tenant_id   text NOT NULL REFERENCES tenants(tenant_id),
    ticket_id   text NOT NULL,
    run_id      text NOT NULL REFERENCES runs(run_id),
    thread_id   text NOT NULL REFERENCES workflows(thread_id),
    category    text NOT NULL,
    priority    text NOT NULL,
    action      text NOT NULL CHECK (action IN ('draft_reply','ask_for_info','escalate')),
    injection_detected boolean NOT NULL,
    kb_refs     text[] NOT NULL DEFAULT '{}',
    draft_reply text,
    status      text NOT NULL CHECK (status IN ('pending_approval','escalated','held_budget','failed')),
    model       text NOT NULL,
    tokens_in   integer NOT NULL,
    tokens_out  integer NOT NULL,
    latency_ms  integer NOT NULL,
    reason_code text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, ticket_id),
    FOREIGN KEY (tenant_id, ticket_id) REFERENCES tickets(tenant_id, ticket_id)
);

CREATE TABLE approvals (
    approval_id text PRIMARY KEY,
    tenant_id   text NOT NULL REFERENCES tenants(tenant_id),
    ticket_id   text NOT NULL,
    thread_id   text NOT NULL REFERENCES workflows(thread_id),
    attempt     integer NOT NULL CHECK (attempt BETWEEN 1 AND 2),
    kind        text NOT NULL CHECK (kind IN ('draft_reply','ask_for_info')),
    draft_text  text NOT NULL,
    kb_refs     text[] NOT NULL DEFAULT '{}',
    check_result jsonb NOT NULL,
    status      text NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending','approved','rejected')),
    decided_by  text,
    decided_at  timestamptz,
    reason      text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (thread_id, attempt),
    CHECK (status <> 'rejected' OR (reason IS NOT NULL AND length(btrim(reason)) > 0))
);
CREATE INDEX approvals_status_idx ON approvals (tenant_id, status);

-- Exactly-once outbox: one row per ticket, enforced by two unique constraints.
CREATE TABLE outbox (
    tenant_id   text NOT NULL REFERENCES tenants(tenant_id),
    ticket_id   text NOT NULL,
    approval_id text NOT NULL REFERENCES approvals(approval_id),
    text        text NOT NULL,
    approved_by text NOT NULL,
    approved_at timestamptz NOT NULL,
    idempotency_key text NOT NULL UNIQUE,
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, ticket_id)
);

-- Escalation cases and dead letters.
CREATE TABLE cases (
    case_id     text PRIMARY KEY,
    tenant_id   text NOT NULL REFERENCES tenants(tenant_id),
    ticket_id   text NOT NULL,
    thread_id   text,
    kind        text NOT NULL CHECK (kind IN ('escalation','dead_letter')),
    owner       text NOT NULL,
    severity    text NOT NULL CHECK (severity IN ('sev1','sev2','sev3','sev4')),
    reason_code text NOT NULL,
    reason      text NOT NULL,
    status      text NOT NULL DEFAULT 'open',
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, ticket_id, kind)
);

-- Every state change: who, when, what, why.
CREATE TABLE audit_log (
    audit_id    bigserial PRIMARY KEY,
    tenant_id   text NOT NULL REFERENCES tenants(tenant_id),
    ticket_id   text,
    thread_id   text,
    actor       text NOT NULL,
    action      text NOT NULL,
    from_state  text,
    to_state    text,
    reason      text,
    details     jsonb NOT NULL DEFAULT '{}',
    at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX audit_ticket_idx ON audit_log (tenant_id, ticket_id);

CREATE TABLE model_calls (
    call_id     bigserial PRIMARY KEY,
    tenant_id   text NOT NULL REFERENCES tenants(tenant_id),
    ticket_id   text,
    thread_id   text,
    run_id      text,
    task        text NOT NULL,      -- triage | draft | embed
    model       text NOT NULL,
    tokens_in   integer,            -- NULL = runtime did not report it
    tokens_out  integer,
    latency_ms  integer NOT NULL,
    ok          boolean NOT NULL,
    error_class text,
    at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX model_calls_thread_idx ON model_calls (thread_id);

-- Execution events streamed to the UI over SSE.
CREATE TABLE events (
    event_id    bigserial PRIMARY KEY,
    tenant_id   text NOT NULL REFERENCES tenants(tenant_id),
    run_id      text,
    thread_id   text,
    ticket_id   text,
    event_type  text NOT NULL,
    node        text,
    status      text,
    duration_ms integer,
    metadata    jsonb NOT NULL DEFAULT '{}',
    at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX events_thread_idx ON events (thread_id, event_id);

-- Durable work queue. Holds references only (tenant + ticket id), never ticket text,
-- so a worker can claim across tenants and then open a tenant-bound session.
CREATE TABLE jobs (
    job_id          bigserial PRIMARY KEY,
    idempotency_key text NOT NULL UNIQUE,  -- tenant:ticket_id:generation
    tenant_id       text NOT NULL REFERENCES tenants(tenant_id),
    ticket_id       text NOT NULL,
    generation      integer NOT NULL DEFAULT 1,
    run_id          text NOT NULL REFERENCES runs(run_id),
    status          text NOT NULL DEFAULT 'queued'
                    CHECK (status IN ('queued','leased','done','dead')),
    attempts        integer NOT NULL DEFAULT 0,
    max_attempts    integer NOT NULL,
    available_at    timestamptz NOT NULL DEFAULT now(),
    lease_owner     text,
    lease_expires_at timestamptz,
    last_error      text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX jobs_claim_idx ON jobs (status, available_at);

-- Rejected input (unknown tenant, malformed rows). No tenant to bind, so platform-level.
CREATE TABLE rejections (
    rejection_id bigserial PRIMARY KEY,
    run_id      text,
    raw_tenant  text,
    ticket_id   text,
    reason      text NOT NULL,
    at          timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE eval_runs (
    eval_id     text PRIMARY KEY,
    runs        integer NOT NULL,
    summary     jsonb NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);
"""


def upgrade() -> None:
    op.execute(SCHEMA)

    password = os.environ.get("RELAY_APP_DB_PASSWORD", "relay_app").replace("'", "''")
    op.execute(f"""
        DO $$ BEGIN
          IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'relay_app') THEN
            CREATE ROLE relay_app LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE
              PASSWORD '{password}';
          END IF;
        END $$;
    """)
    op.execute("ALTER ROLE relay_app NOSUPERUSER NOBYPASSRLS")
    op.execute("GRANT USAGE ON SCHEMA public TO relay_app")
    op.execute("GRANT SELECT ON tenants TO relay_app")
    op.execute("GRANT SELECT, INSERT, UPDATE ON runs, jobs, rejections, eval_runs TO relay_app")
    for t in TENANT_TABLES:
        # The audit trail, outbox, usage meter and event log are append-only for the app.
        verbs = "SELECT, INSERT" if t in APPEND_ONLY else "SELECT, INSERT, UPDATE"
        op.execute(f"GRANT {verbs} ON {t} TO relay_app")
    op.execute("GRANT DELETE ON kb_articles TO relay_app")
    op.execute("GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO relay_app")

    # Fail closed: current_setting(..., true) returns NULL (or '') when unset, and
    # `tenant_id = NULL` is never true, so missing context yields zero rows and
    # every INSERT/UPDATE is rejected by WITH CHECK. There is no "default tenant".
    for t in TENANT_TABLES:
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY tenant_isolation ON {t}
              USING (tenant_id = current_setting('app.tenant_id', true))
              WITH CHECK (tenant_id = current_setting('app.tenant_id', true))
        """)


def downgrade() -> None:
    op.execute("""
        DROP TABLE IF EXISTS eval_runs, rejections, jobs, events, model_calls, audit_log,
          cases, outbox, approvals, decisions, workflows, runs, tickets, kb_articles, tenants
          CASCADE
    """)
