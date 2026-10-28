"""S2 reports. The client view shows outcomes only: never models, prompts, tokens or agent names."""
from __future__ import annotations

import statistics

from relay.db import platform_tx, require_tenant, tenant_ids, tenant_tx


def internal_report() -> str:
    lines = ["RELAY INTERNAL REPORT", ""]
    with platform_tx() as conn:
        runs = conn.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT 10").fetchall()
    lines.append("Recent runs:")
    for r in runs:
        dur = (r["finished_at"] - r["started_at"]).total_seconds() if r["finished_at"] else None
        lines.append(f"  {r['run_id']:<34} {r['kind']:<6} tokens in={r['tokens_in']:>7} "
                     f"out={r['tokens_out']:>6} " + (f"{dur:.1f}s" if dur is not None else "running"))
    for tenant in sorted(tenant_ids()):
        with tenant_tx(tenant) as conn:
            models = conn.execute(
                """SELECT model, task, count(*) AS calls, sum(tokens_in) AS tin, sum(tokens_out) AS tout,
                          round(avg(latency_ms)) AS lat, sum((NOT ok)::int) AS errors
                   FROM model_calls GROUP BY model, task ORDER BY model""").fetchall()
            cases = conn.execute(
                "SELECT kind, reason_code, count(*) AS n FROM cases GROUP BY 1, 2 ORDER BY 1, 2").fetchall()
        lines += ["", f"[{tenant}] model usage:"]
        lines += [f"  {m['model']:<18} {m['task']:<7} calls={m['calls']:>4} in={m['tin'] or 0:>7} "
                  f"out={m['tout'] or 0:>6} avg_latency={m['lat']}ms errors={m['errors']}" for m in models]
        lines.append(f"[{tenant}] cases: " + (", ".join(
            f"{c['kind']}/{c['reason_code']}={c['n']}" for c in cases) or "none"))
    return "\n".join(lines)


def client_report(tenant: str) -> str:
    require_tenant(tenant)
    with tenant_tx(tenant) as conn:
        cats = conn.execute("SELECT category, count(*) AS n FROM decisions GROUP BY 1 ORDER BY 2 DESC").fetchall()
        acts = conn.execute("SELECT action, count(*) AS n FROM decisions GROUP BY 1 ORDER BY 2 DESC").fetchall()
        approved = conn.execute("SELECT count(*) AS n FROM outbox").fetchone()["n"]
        times = [r["s"] for r in conn.execute(
            """SELECT extract(epoch FROM a.created_at - t.created_at) AS s
               FROM approvals a JOIN tickets t USING (tenant_id, ticket_id) WHERE a.attempt = 1""")]
    median = f"{statistics.median(times):.0f}s" if times else "n/a"
    return "\n".join([
        f"Support summary for {tenant}", "",
        "Tickets by category: " + ", ".join(f"{c['category']}={c['n']}" for c in cats),
        "Outcomes: " + ", ".join(f"{a['action']}={a['n']}" for a in acts),
        f"Replies approved and sent: {approved}",
        f"Median time from ticket intake to a draft ready for review: {median}",
    ])
