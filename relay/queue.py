"""Durable Postgres work queue (E2).

* claim:   UPDATE ... WHERE job_id = (SELECT ... FOR UPDATE SKIP LOCKED LIMIT 1)
* lease:   lease_expires_at, extended by a heartbeat while a job runs; an expired lease
           (dead worker) makes the job claimable again
* retries: exponential backoff with full jitter, bounded by max_attempts
* dead letter: a `dead_letter` case (owner, severity, reason) + a `failed` decision
* idempotency: jobs.idempotency_key = tenant:ticket:generation (unique); the graph
  thread id is derived from the same key, so a re-run resumes instead of duplicating.
"""
from __future__ import annotations

import json
import os
import random
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import psycopg
from pydantic import ValidationError

from relay import store
from relay.config import get_settings
from relay.contracts import DecisionRow, DecisionStatus, Ticket
from relay.db import UnknownTenantError, platform_tx, require_tenant
from relay.gateway import GatewayError
from relay.orchestrator import Orchestrator, thread_id_for


@dataclass
class EnqueueReport:
    run_id: str
    enqueued: list[str]
    skipped_duplicates: list[str]
    adopted: list[str]
    rejected: list[dict]


def _job_key(tenant_id: str, ticket_id: str, generation: int) -> str:
    return f"{tenant_id}:{ticket_id}:{generation}"


def enqueue_ticket(ticket: Ticket, run_id: str, max_attempts: int) -> str:
    """Returns 'enqueued', 'adopted' or 'duplicate'. Same ticket id => same business decision.
    'adopted': a job left queued/leased by an earlier (e.g. killed) run joins this run, so this
    run waits for it and reclaims it once its lease expires."""
    store.upsert_ticket(ticket)
    decision = store.get_decision(ticket.tenant, ticket.id)
    with platform_tx() as conn:
        last = conn.execute(
            "SELECT generation, status FROM jobs WHERE tenant_id = %s AND ticket_id = %s "
            "ORDER BY generation DESC LIMIT 1 FOR UPDATE",
            (ticket.tenant, ticket.id),
        ).fetchone()
        retriable = decision is not None and decision["status"] in (
            DecisionStatus.held_budget.value, DecisionStatus.failed.value)
        if last is not None and last["status"] in ("queued", "leased"):
            conn.execute("UPDATE jobs SET run_id = %s, updated_at = now() WHERE tenant_id = %s "
                         "AND ticket_id = %s AND generation = %s",
                         (run_id, ticket.tenant, ticket.id, last["generation"]))
            return "adopted"
        if last is not None and not (retriable and last["status"] in ("done", "dead")):
            return "duplicate"
        if last is None and decision is not None and not retriable:
            return "duplicate"
        generation = (last["generation"] + 1) if last else 1
        conn.execute(
            """INSERT INTO jobs (idempotency_key, tenant_id, ticket_id, generation, run_id,
                   max_attempts)
               VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (idempotency_key) DO NOTHING""",
            (_job_key(ticket.tenant, ticket.id, generation), ticket.tenant, ticket.id,
             generation, run_id, max_attempts),
        )
    return "enqueued"


def enqueue_inbox(path: Path, run_id: str) -> EnqueueReport:
    s = get_settings()
    report = EnqueueReport(run_id, [], [], [], [])
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        raw = None
        try:
            raw = json.loads(line)
            ticket = Ticket.model_validate(raw)
            require_tenant(ticket.tenant)
        except (json.JSONDecodeError, ValidationError) as e:
            reason = f"line {lineno}: invalid ticket ({type(e).__name__})"
            raw = raw if isinstance(raw, dict) else {}
            store.log_rejection(run_id, str(raw.get("tenant")), str(raw.get("id")), reason)
            report.rejected.append({"line": lineno, "ticket_id": raw.get("id"), "reason": reason})
            continue
        except UnknownTenantError:
            reason = f"unknown tenant {ticket.tenant!r}: rejected, never processed under a default"
            store.log_rejection(run_id, ticket.tenant, ticket.id, reason)
            report.rejected.append({"line": lineno, "ticket_id": ticket.id, "reason": reason})
            continue
        outcome = enqueue_ticket(ticket, run_id, s.max_attempts)
        key = f"{ticket.tenant}/{ticket.id}"
        {"enqueued": report.enqueued, "adopted": report.adopted,
         "duplicate": report.skipped_duplicates}[outcome].append(key)
    return report


# ------------------------------------------------------------------ claiming

def claim(worker_id: str, lease_seconds: int, run_id: str | None = None) -> dict | None:
    with platform_tx() as conn:
        return conn.execute(
            """UPDATE jobs SET status = 'leased', lease_owner = %(w)s,
                   lease_expires_at = now() + make_interval(secs => %(lease)s),
                   attempts = attempts + 1, updated_at = now()
               WHERE job_id = (
                   SELECT job_id FROM jobs
                   WHERE ((status = 'queued' AND available_at <= now())
                          OR (status = 'leased' AND lease_expires_at < now()))
                     AND (%(run)s::text IS NULL OR run_id = %(run)s::text)
                   ORDER BY available_at, job_id
                   FOR UPDATE SKIP LOCKED LIMIT 1)
               RETURNING *""",
            {"w": worker_id, "lease": lease_seconds, "run": run_id},
        ).fetchone()


def heartbeat(job_id: int, worker_id: str, lease_seconds: int) -> bool:
    with platform_tx() as conn:
        return conn.execute(
            """UPDATE jobs SET lease_expires_at = now() + make_interval(secs => %s)
               WHERE job_id = %s AND lease_owner = %s AND status = 'leased'
               RETURNING job_id""",
            (lease_seconds, job_id, worker_id),
        ).fetchone() is not None


def complete(job_id: int, worker_id: str) -> None:
    with platform_tx() as conn:
        conn.execute(
            "UPDATE jobs SET status = 'done', lease_owner = NULL, lease_expires_at = NULL, "
            "updated_at = now() WHERE job_id = %s AND lease_owner = %s",
            (job_id, worker_id),
        )


def backoff_seconds(attempt: int, base: float, cap: float, rng: random.Random) -> float:
    """Exponential backoff with full jitter: uniform(0, min(cap, base * 2^(attempt-1)))."""
    return rng.uniform(0, min(cap, base * (2 ** (attempt - 1))))


def fail(job: dict, worker_id: str, error: Exception, rng: random.Random) -> str:
    """Retry with backoff, or dead-letter when attempts are exhausted or the error is permanent."""
    s = get_settings()
    error_class = getattr(error, "error_class", type(error).__name__)
    transient = getattr(error, "transient", isinstance(error, psycopg.OperationalError))
    dead = job["attempts"] >= job["max_attempts"] or (
        isinstance(error, GatewayError) and not transient)
    with platform_tx() as conn:
        if dead:
            conn.execute(
                "UPDATE jobs SET status = 'dead', last_error = %s, lease_owner = NULL, "
                "lease_expires_at = NULL, updated_at = now() WHERE job_id = %s AND lease_owner = %s",
                (error_class, job["job_id"], worker_id),
            )
        else:
            delay = backoff_seconds(job["attempts"], s.backoff_base_s, s.backoff_max_s, rng)
            conn.execute(
                "UPDATE jobs SET status = 'queued', last_error = %s, lease_owner = NULL, "
                "lease_expires_at = NULL, available_at = now() + make_interval(secs => %s), "
                "updated_at = now() WHERE job_id = %s AND lease_owner = %s",
                (error_class, delay, job["job_id"], worker_id),
            )
    thread_id = thread_id_for(job["tenant_id"], job["ticket_id"], job["generation"])
    store.emit_event(job["tenant_id"], run_id=job["run_id"], thread_id=thread_id,
                     ticket_id=job["ticket_id"],
                     event_type="job_dead_lettered" if dead else "job_retry_scheduled",
                     node="queue", status="failed" if dead else "retrying",
                     metadata={"attempt": job["attempts"], "max_attempts": job["max_attempts"],
                               "error_class": error_class})
    if dead:
        _dead_letter(job, thread_id, error_class)
    return "dead" if dead else "retry"


def _dead_letter(job: dict, thread_id: str, error_class: str) -> None:
    store.open_case(tenant_id=job["tenant_id"], ticket_id=job["ticket_id"], thread_id=thread_id,
                    kind="dead_letter", reason_code="dead_letter",
                    reason=f"gave up after {job['attempts']} attempt(s): {error_class}",
                    priority="high")
    store.create_workflow(thread_id, job["tenant_id"], job["ticket_id"], job["run_id"])
    store.update_workflow(job["tenant_id"], thread_id, status="failed")
    store.upsert_decision(DecisionRow(
        ticket_id=job["ticket_id"], tenant=job["tenant_id"], run_id=job["run_id"],
        category="other", priority="high", action="escalate", injection_detected=False,
        kb_refs=[], draft_reply=None, status=DecisionStatus.failed, model="none",
        tokens_in=0, tokens_out=0, latency_ms=0,
    ), thread_id, f"dead_letter:{error_class}", actor="queue")


# ------------------------------------------------------------------ worker

class Worker:
    def __init__(self, orchestrator: Orchestrator, worker_id: str | None = None,
                 rng: random.Random | None = None):
        self.orch = orchestrator
        self.id = worker_id or f"{socket.gethostname()}-{os.getpid()}"
        self.rng = rng or random.Random()
        self.s = get_settings()

    def process(self, job: dict) -> str:
        thread_id = thread_id_for(job["tenant_id"], job["ticket_id"], job["generation"])
        store.emit_event(job["tenant_id"], run_id=job["run_id"], thread_id=thread_id,
                         ticket_id=job["ticket_id"], event_type="job_leased", node="queue",
                         status="running",
                         metadata={"attempt": job["attempts"], "worker": self.id,
                                   "lease_seconds": self.s.lease_seconds})
        stop = threading.Event()
        beat = threading.Thread(target=self._heartbeat, args=(job["job_id"], stop), daemon=True)
        beat.start()
        try:
            self.orch.run_thread(thread_id=thread_id, tenant_id=job["tenant_id"],
                                 ticket_id=job["ticket_id"], run_id=job["run_id"])
        except Exception as e:  # classified in fail(); never crashes the run
            return fail(job, self.id, e, self.rng)
        finally:
            stop.set()
            beat.join(timeout=1)
        complete(job["job_id"], self.id)
        return "done"

    def _heartbeat(self, job_id: int, stop: threading.Event) -> None:
        interval = max(1.0, self.s.lease_seconds / 3)
        while not stop.wait(interval):
            heartbeat(job_id, self.id, self.s.lease_seconds)

    def drain(self, run_id: str | None = None, idle_exit: bool = True,
              on_job=None) -> dict[str, int]:
        """Process jobs until none remain (for this run, if given)."""
        counts: dict[str, int] = {}
        while True:
            job = claim(self.id, self.s.lease_seconds, run_id)
            if job is None:
                if not idle_exit or _has_pending(run_id):
                    time.sleep(0.5)
                    continue
                return counts
            outcome = self.process(job)
            counts[outcome] = counts.get(outcome, 0) + 1
            if on_job:
                on_job(job, outcome)


def _has_pending(run_id: str | None) -> bool:
    with platform_tx() as conn:
        return conn.execute(
            "SELECT 1 FROM jobs WHERE status IN ('queued','leased') "
            "AND (%(run)s::text IS NULL OR run_id = %(run)s::text) LIMIT 1",
            {"run": run_id},
        ).fetchone() is not None


def queue_stats() -> dict[str, int]:
    with platform_tx() as conn:
        rows = conn.execute("SELECT status, count(*) AS n FROM jobs GROUP BY status").fetchall()
    return {r["status"]: r["n"] for r in rows}
