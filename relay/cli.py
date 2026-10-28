"""`relay` command line: migrate, seed, run, worker, approvals, cases, eval, report, health."""
from __future__ import annotations

import argparse
import json
import sys
import textwrap
import time
from pathlib import Path

from relay.config import ROOT, get_settings


def _gateway():
    from relay.gateway import default_gateway

    return default_gateway()


def _require_models(gateway) -> None:
    from relay.gateway import ModelUnavailable

    try:
        status = gateway.check_models()
    except ModelUnavailable:
        sys.exit(f"ERROR: Ollama is not reachable at {get_settings().ollama_base_url}. "
                 "Start it with `ollama serve` (or open the Ollama app).")
    missing = [m for m, ok in status.items() if not ok]
    if missing:
        sys.exit("ERROR: required models are not pulled (no substitute is used):\n" +
                 "\n".join(f"  ollama pull {m}" for m in missing))


def cmd_migrate(args) -> None:
    from relay.db import migrate

    migrate()
    print("migrations applied; tenants registered; checkpoint tables ready")


def cmd_seed(args) -> None:
    from relay.seed import seed_kb

    gw = _gateway()
    _require_models(gw)
    counts = seed_kb(gw, Path(args.data))
    print("KB seeded (articles embedded this run): " +
          ", ".join(f"{t}={n}" for t, n in counts.items()))


def cmd_run(args) -> None:
    from relay import outputs, store
    from relay.orchestrator import Orchestrator
    from relay.queue import Worker, enqueue_inbox

    s = get_settings()
    gw = _gateway()
    _require_models(gw)
    inbox = Path(args.inbox)
    if not inbox.exists():
        sys.exit(f"ERROR: inbox file not found: {inbox}")
    run_id = store.new_run_id()
    store.create_run(run_id, "inbox", str(inbox), s.max_tokens_per_run)
    report = enqueue_inbox(inbox, run_id)
    print(f"run {run_id}: {len(report.enqueued)} enqueued, "
          f"{len(report.skipped_duplicates)} already decided (skipped), "
          f"{len(report.adopted)} unfinished from an earlier run (resumed), "
          f"{len(report.rejected)} rejected")
    for r in report.rejected:
        print(f"  REJECTED line {r['line']} ({r['ticket_id']}): {r['reason']}")
    t0 = time.perf_counter()

    def show(job, outcome):
        d = store.get_decision(job["tenant_id"], job["ticket_id"]) or {}
        print(f"  {job['tenant_id']:<10} {job['ticket_id']:<8} {outcome:<6} "
              f"{d.get('action', '-'):<13} {d.get('status', '-'):<17} "
              f"{d.get('category', '-'):<18} {d.get('priority', '-'):<7} "
              f"inj={d.get('injection_detected', '-')} {d.get('latency_ms', 0)}ms")

    Worker(Orchestrator(gw)).drain(run_id, on_job=show)
    store.finish_run(run_id)
    outputs.export_decisions()
    outputs.export_rejections()
    print(f"run {run_id} finished in {time.perf_counter() - t0:.1f}s -> out/decisions.jsonl")


def cmd_worker(args) -> None:
    from relay.approvals import reconcile
    from relay.orchestrator import Orchestrator
    from relay.queue import Worker

    gw = _gateway()
    _require_models(gw)
    orch = Orchestrator(gw)
    resumed = reconcile(orch)
    if resumed:
        print(f"reconciled {len(resumed)} decided approval(s) whose thread had not resumed")
    print("worker started; Ctrl-C to stop")
    Worker(orch).drain(idle_exit=False)


def _print_approval(a: dict, full: bool) -> None:
    print(f"{a['approval_id']}  {a['tenant_id']:<10} {a['ticket_id']:<8} {a['kind']:<13} "
          f"attempt={a['attempt']} status={a['status']}")
    if full:
        checks = a["check_result"]["rules"]
        print("  draft:")
        print(textwrap.indent(textwrap.fill(a["draft_text"], 90), "    "))
        print(f"  kb_refs: {', '.join(a['kb_refs']) or '(none)'}")
        print(f"  checker: {'PASS' if a['check_result']['passed'] else 'FAIL'} "
              f"({sum(r['passed'] for r in checks)}/{len(checks)} rules)")
        if a.get("decided_by"):
            print(f"  decided by {a['decided_by']} at {a['decided_at']}"
                  + (f" reason: {a['reason']}" if a.get("reason") else ""))


def cmd_approvals(args) -> None:
    from relay import approvals, outputs

    if args.action == "list":
        rows = approvals.list_approvals(status=None if args.all else "pending")
        if not rows:
            print("no pending approvals" if not args.all else "no approvals")
        for a in rows:
            _print_approval(a, args.verbose)
        return
    if args.action == "show":
        a = approvals.find(args.approval_id)
        if a is None:
            sys.exit(f"approval {args.approval_id} not found")
        _print_approval(a, True)
        return

    from relay.orchestrator import Orchestrator

    gw = _gateway()
    orch = Orchestrator(gw)
    try:
        if args.action == "approve":
            out = approvals.approve(orch, args.approval_id, args.by)
        else:
            if args.action == "reject":
                _require_models(gw)  # a rejection triggers one redraft
            out = approvals.reject(orch, args.approval_id, args.by, args.reason)
    except approvals.ApprovalError as e:
        sys.exit(f"ERROR: {e}")
    outputs.export_outbox()
    outputs.export_decisions()
    note = "" if out.changed else " (already decided: no change)"
    print(f"{out.approval_id}: {out.status}{note}; thread {out.thread_phase}; "
          f"outbox_written={out.outbox_written}")
    if args.action == "reject" and out.thread_phase == "paused":
        print("  a revised draft is waiting: run `relay approvals list`")


def cmd_cases(args) -> None:
    from relay.db import tenant_ids, tenant_tx

    for tenant in sorted(tenant_ids()):
        with tenant_tx(tenant) as conn:
            for c in conn.execute("SELECT * FROM cases ORDER BY created_at").fetchall():
                print(f"{c['case_id']}  {tenant:<10} {c['ticket_id']:<8} {c['kind']:<11} "
                      f"{c['severity']} owner={c['owner']:<16} {c['reason_code']}: {c['reason']}")


def cmd_eval(args) -> None:
    from relay.evaluation import run_evaluation

    gw = _gateway()
    _require_models(gw)
    summary = run_evaluation(gw, runs=args.runs, golden=Path(args.golden), data_dir=Path(args.data),
                             draft=not args.no_draft)
    print(json.dumps(summary["aggregate"], indent=2))
    print("wrote eval/runs/run_N.jsonl, eval/runs/summary.json and docs/EVAL.md")


def cmd_report(args) -> None:
    from relay.report import client_report, internal_report

    if args.view == "client":
        if not args.tenant:
            sys.exit("--tenant is required for the client view")
        print(client_report(args.tenant))
    else:
        print(internal_report())


def cmd_health(args) -> None:
    from relay.health import run_checks

    results = run_checks(_gateway())
    for name, r in results.items():
        print(f"{'OK  ' if r['ok'] else 'FAIL'} {name:<22} {r['detail']}")
    sys.exit(0 if all(r["ok"] for r in results.values()) else 1)


def cmd_export(args) -> None:
    from relay import outputs

    outputs.export_decisions()
    outputs.export_outbox()
    print("exported out/decisions.jsonl and out/outbox.jsonl")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="relay", description="Project Relay support-desk agents")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate").set_defaults(fn=cmd_migrate)
    sp = sub.add_parser("seed")
    sp.add_argument("--data", default=str(ROOT / "data"))
    sp.set_defaults(fn=cmd_seed)
    sp = sub.add_parser("run")
    sp.add_argument("--inbox", required=True)
    sp.set_defaults(fn=cmd_run)
    sub.add_parser("worker").set_defaults(fn=cmd_worker)

    sp = sub.add_parser("approvals")
    asub = sp.add_subparsers(dest="action", required=True)
    lp = asub.add_parser("list")
    lp.add_argument("--all", action="store_true")
    lp.add_argument("-v", "--verbose", action="store_true")
    asub.add_parser("show").add_argument("approval_id")
    ap = asub.add_parser("approve")
    ap.add_argument("approval_id")
    ap.add_argument("--by", required=True)
    rp = asub.add_parser("reject")
    rp.add_argument("approval_id")
    rp.add_argument("--reason", required=True)
    rp.add_argument("--by", required=True)
    sp.set_defaults(fn=cmd_approvals)

    sub.add_parser("cases").set_defaults(fn=cmd_cases)
    sp = sub.add_parser("eval")
    sp.add_argument("--runs", type=int, default=5)
    sp.add_argument("--golden", default=str(ROOT / "data" / "golden.jsonl"))
    sp.add_argument("--data", default=str(ROOT / "data"))
    sp.add_argument("--no-draft", action="store_true", help="triage + routing only (faster)")
    sp.set_defaults(fn=cmd_eval)
    sp = sub.add_parser("report")
    sp.add_argument("--view", choices=["internal", "client"], default="internal")
    sp.add_argument("--tenant")
    sp.set_defaults(fn=cmd_report)
    sub.add_parser("health").set_defaults(fn=cmd_health)
    sub.add_parser("export").set_defaults(fn=cmd_export)

    args = p.parse_args(argv)
    sys.stdout.reconfigure(line_buffering=True)  # progress stays visible if the process is killed
    args.fn(args)


if __name__ == "__main__":
    main()
