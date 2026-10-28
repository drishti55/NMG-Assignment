"""Health checks. Each check reports what it actually observed; nothing defaults to OK."""
from __future__ import annotations

import time

from relay.config import get_settings
from relay.gateway import ModelGateway


def _check(fn) -> dict:
    t0 = time.perf_counter()
    try:
        ok, detail = fn()
    except Exception as e:  # a failed check is a result, not a crash
        ok, detail = False, f"{type(e).__name__}: {str(e)[:160]}"
    return {"ok": ok, "detail": detail, "checked_ms": int((time.perf_counter() - t0) * 1000)}


def run_checks(gateway: ModelGateway, sse_stats: dict | None = None) -> dict[str, dict]:
    from relay.db import platform_tx

    s = get_settings()
    results: dict[str, dict] = {}

    def postgres():
        with platform_tx() as conn:
            v = conn.execute("SELECT current_setting('server_version') AS v, current_user AS u").fetchone()
        return True, f"PostgreSQL {v['v']} as {v['u']}"

    def pgvector():
        with platform_tx() as conn:
            r = conn.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'").fetchone()
        return (r is not None), (f"vector {r['extversion']}" if r else "extension not installed")

    def ollama():
        names = gateway.backend.available_models()
        return True, f"{len(names)} model(s) at {s.ollama_base_url}"

    def models():
        status = gateway.check_models()
        missing = [m for m, ok in status.items() if not ok]
        return not missing, ("all required models present: " + ", ".join(status)) if not missing \
            else "missing: " + ", ".join(missing)

    def workers():
        with platform_tx() as conn:
            r = conn.execute(
                "SELECT count(DISTINCT lease_owner) AS n FROM jobs "
                "WHERE status = 'leased' AND lease_expires_at > now()").fetchone()
        n = r["n"]
        return True, (f"{n} worker(s) holding live leases" if n else
                      "no worker is processing right now (idle is normal between runs)")

    def queue():
        with platform_tx() as conn:
            rows = conn.execute("SELECT status, count(*) AS n FROM jobs GROUP BY status").fetchall()
            stale = conn.execute(
                "SELECT count(*) AS n FROM jobs WHERE status = 'leased' AND lease_expires_at < now()"
            ).fetchone()["n"]
        counts = {r["status"]: r["n"] for r in rows}
        detail = ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "empty"
        return stale == 0, detail + (f"; {stale} expired lease(s) awaiting reclaim" if stale else "")

    def checkpoints():
        with platform_tx() as conn:
            n = conn.execute("SELECT count(DISTINCT thread_id) AS n FROM checkpoints").fetchone()["n"]
        return True, f"PostgresSaver tables readable; {n} thread(s) checkpointed"

    for name, fn in (("postgres", postgres), ("pgvector", pgvector), ("ollama", ollama),
                     ("required_models", models), ("workers", workers), ("queue", queue),
                     ("checkpoints", checkpoints)):
        results[name] = _check(fn)
    if results["ollama"]["ok"] is False:
        results["required_models"] = {"ok": False, "detail": "not checked: Ollama unreachable",
                                      "checked_ms": 0}
    if sse_stats is not None:
        results["sse"] = {"ok": True, "checked_ms": 0, "detail": (
            f"{sse_stats['clients']} client(s) connected, {sse_stats['delivered']} event(s) "
            "delivered by this API process")}
    return results

