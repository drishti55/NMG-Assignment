"""Golden-set evaluation (E3).

Runs the production graph (same nodes, same lead policy, same models at temperature 0
and a fixed seed) with drafting and business writes switched off, N times over the
golden tickets. golden.jsonl is read ONLY here, to score; nothing in the decision path
imports this module.
"""
from __future__ import annotations

import json
import time
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from psycopg.types.json import Jsonb

from relay import store
from relay.agents.signals import detect_injection
from relay.config import ROOT, get_settings
from relay.contracts import DecisionRow, Ticket
from relay.db import platform_tx
from relay.gateway import ModelGateway
from relay.graph import Deps, RelayState, build_graph


def load_golden_tickets(golden: Path, data_dir: Path) -> tuple[list[dict], dict[str, Ticket]]:
    labels = [json.loads(line) for line in golden.read_text().splitlines() if line.strip()]
    tickets: dict[str, Ticket] = {}
    for inbox in sorted(data_dir.glob("*/tickets.jsonl")):
        for line in inbox.read_text().splitlines():
            if line.strip():
                t = Ticket.model_validate_json(line)
                tickets[t.id] = t
    missing = [g["id"] for g in labels if g["id"] not in tickets]
    if missing:
        raise ValueError(f"golden ids without a ticket: {missing}")
    return labels, tickets


def _run_once(graph, n: int, eval_id: str, labels: list[dict],
              tickets: dict[str, Ticket]) -> tuple[list[DecisionRow], list[dict], float]:
    rows, errors = [], []
    t0 = time.perf_counter()
    for g in labels:
        ticket = tickets[g["id"]]
        state = RelayState(thread_id=f"eval:{eval_id}:{n}:{ticket.id}", run_id=eval_id,
                           tenant_id=ticket.tenant, ticket_id=ticket.id, ticket=ticket,
                           started_ms=int(time.time() * 1000))
        try:
            final = graph.invoke(state)
            row = final["decision"]
            if final.get("error_code"):
                errors.append({"run": n, "ticket_id": ticket.id, "error": final["error_code"]})
        except Exception as e:  # recorded per ticket, the eval keeps going
            errors.append({"run": n, "ticket_id": ticket.id,
                           "error": getattr(e, "error_class", type(e).__name__)})
            row = DecisionRow(ticket_id=ticket.id, tenant=ticket.tenant, run_id=eval_id,
                              category="other", priority="normal", action="escalate",
                              injection_detected=False, kb_refs=[], draft_reply=None,
                              status="failed", model="none", tokens_in=0, tokens_out=0,
                              latency_ms=0)
        rows.append(row)
    return rows, errors, time.perf_counter() - t0


def score(labels: list[dict], runs: list[list[DecisionRow]]) -> dict:
    gold = {g["id"]: g for g in labels}
    per_run = []
    for rows in runs:
        n = len(rows)
        inj_pos = [r for r in rows if gold[r.ticket_id]["injection"]]
        inj_neg = [r for r in rows if not gold[r.ticket_id]["injection"]]
        per_run.append({
            "action_accuracy": sum(r.action == gold[r.ticket_id]["action"] for r in rows) / n,
            "category_accuracy": sum(r.category == gold[r.ticket_id]["category"] for r in rows) / n,
            "priority_accuracy": sum(r.priority == gold[r.ticket_id]["priority"] for r in rows) / n,
            "injection_recall": (sum(r.injection_detected for r in inj_pos) / len(inj_pos)
                                 if inj_pos else None),
            "injection_false_positive_rate": (sum(r.injection_detected for r in inj_neg) / len(inj_neg)
                                              if inj_neg else None),
            "injection_false_positives": sorted(r.ticket_id for r in inj_neg if r.injection_detected),
            "tokens_in": sum(r.tokens_in for r in rows),
            "tokens_out": sum(r.tokens_out for r in rows),
            "failed": sum(r.status == "failed" for r in rows),
        })
    actions = defaultdict(list)
    for rows in runs:
        for r in rows:
            actions[r.ticket_id].append(r.action.value)
    stable = [t for t, a in actions.items() if len(set(a)) == 1]

    def mean(key: str) -> float | None:
        vals = [p[key] for p in per_run if p[key] is not None]
        return round(sum(vals) / len(vals), 4) if vals else None

    def spread(key: str) -> list[float]:
        vals = [p[key] for p in per_run if p[key] is not None]
        return [round(min(vals), 4), round(max(vals), 4)] if vals else []

    return {
        "per_run": per_run,
        "aggregate": {
            "runs": len(runs),
            "tickets": len(labels),
            **{k: mean(k) for k in ("action_accuracy", "category_accuracy", "priority_accuracy",
                                    "injection_recall", "injection_false_positive_rate")},
            "action_accuracy_min_max": spread("action_accuracy"),
            "category_accuracy_min_max": spread("category_accuracy"),
            "stability": round(len(stable) / len(actions), 4),
            "unstable_tickets": sorted(set(actions) - set(stable)),
            "tokens_in_total": sum(p["tokens_in"] for p in per_run),
            "tokens_out_total": sum(p["tokens_out"] for p in per_run),
        },
    }


def per_ticket_errors(labels: list[dict], runs: list[list[DecisionRow]]) -> list[dict]:
    """Every field that disagrees with the golden label, per ticket, with how often."""
    out = []
    for g in labels:
        got = [next(r for r in rows if r.ticket_id == g["id"]) for rows in runs]
        diffs = {}
        for field, gold_key in (("action", "action"), ("category", "category"),
                                ("priority", "priority"), ("injection_detected", "injection")):
            values = Counter(str(getattr(r, field).value if hasattr(getattr(r, field), "value")
                                 else getattr(r, field)) for r in got)
            expected = str(g[gold_key])
            wrong = {v: c for v, c in values.items() if v != expected}
            if wrong:
                diffs[field] = {"expected": expected, "got": wrong}
        if diffs:
            out.append({"ticket_id": g["id"], "runs": len(runs), "mismatches": diffs})
    return out


def detector_suite(path: Path) -> dict | None:
    """Deterministic detector on the hand-written adversarial / benign suite (no model)."""
    if not path.exists():
        return None
    cases = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    tp = sum(1 for c in cases if c["injection"] and detect_injection(c["text"]).detected)
    fp = [c["text"][:60] for c in cases if not c["injection"] and detect_injection(c["text"]).detected]
    pos = sum(1 for c in cases if c["injection"])
    neg = len(cases) - pos
    return {"cases": len(cases), "positives": pos, "negatives": neg,
            "recall": round(tp / pos, 4) if pos else None,
            "false_positive_rate": round(len(fp) / neg, 4) if neg else None,
            "false_positives": fp}


def run_evaluation(gateway: ModelGateway, runs: int, golden: Path, data_dir: Path,
                   draft: bool = True) -> dict:
    s = get_settings()
    labels, tickets = load_golden_tickets(golden, data_dir)
    eval_id = f"eval-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:4]}"
    store.create_run(eval_id, "eval", str(golden), s.max_tokens_per_ticket * len(labels) * runs)
    graph = build_graph(Deps(gateway, s, persist=False, drafting=draft))

    s.eval_dir.mkdir(parents=True, exist_ok=True)
    for old in s.eval_dir.glob("run_*.jsonl"):
        old.unlink()
    all_rows, all_errors, walls = [], [], []
    for n in range(1, runs + 1):
        rows, errors, wall = _run_once(graph, n, eval_id, labels, tickets)
        (s.eval_dir / f"run_{n}.jsonl").write_text(
            "".join(r.model_dump_json() + "\n" for r in rows), encoding="utf-8")
        all_rows.append(rows)
        all_errors += errors
        walls.append(round(wall, 2))
        print(f"  run {n}/{runs}: {wall:.1f}s, "
              f"action acc {sum(r.action == g['action'] for r, g in zip(rows, labels)) / len(rows):.3f}")
    store.finish_run(eval_id)

    summary = {
        "eval_id": eval_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "config": {"triage_model": s.triage_model, "draft_model": s.draft_model if draft else None,
                   "embed_model": s.embed_model, "drafting": draft,
                   "temperature": s.temperature, "seed": s.seed, "runs": runs,
                   "retrieval_top_k": s.retrieval_top_k, "retrieval_min_score": s.retrieval_min_score},
        **score(labels, all_rows),
        "wall_time_s": {"per_run": walls, "total": round(sum(walls), 2)},
        "per_ticket_errors": per_ticket_errors(labels, all_rows),
        "exceptions": all_errors,
        "detector_suite": detector_suite(ROOT / "tests" / "fixtures" / "injection_cases.jsonl"),
    }
    (s.eval_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    with platform_tx() as conn:
        conn.execute("INSERT INTO eval_runs (eval_id, runs, summary) VALUES (%s,%s,%s)",
                     (eval_id, runs, Jsonb(summary)))
    write_eval_md(summary, ROOT / "docs" / "EVAL.md", ROOT / "docs" / "EVAL_NOTES.md")
    return summary


def _pct(v: float | None) -> str:
    return "n/a" if v is None else f"{v * 100:.1f}%"


def write_eval_md(summary: dict, path: Path, notes: Path) -> None:
    a = summary["aggregate"]
    c = summary["config"]
    lines = [
        "# Evaluation", "",
        "Generated by `make eval` from the run recorded below. Numbers are the genuine output of",
        "`relay/evaluation.py`; nothing in this section is hand-edited. Raw rows: "
        "`eval/runs/run_N.jsonl`, full summary: `eval/runs/summary.json`.", "",
        f"- Eval id: `{summary['eval_id']}` ({summary['created_at']})",
        f"- Triage model `{c['triage_model']}`, embeddings `{c['embed_model']}`, temperature "
        f"{c['temperature']}, seed {c['seed']}, {c['runs']} runs x {a['tickets']} golden tickets",
        ("- Scope: the production graph up to the decision: triage, lead routing, retrieval, "
         f"drafting with `{c['draft_model']}` and the checker (drafts can still turn into "
         "escalations). Human approval and all business writes are off."
         if c["drafting"] else
         "- Scope: triage + lead routing + retrieval only (`--no-draft`); draft_reply is null."), "",
        "## Results", "",
        "| Metric | Mean over runs | Min / max |", "|---|---|---|",
        f"| Action accuracy | {_pct(a['action_accuracy'])} | "
        f"{' / '.join(_pct(v) for v in a['action_accuracy_min_max'])} |",
        f"| Category accuracy | {_pct(a['category_accuracy'])} | "
        f"{' / '.join(_pct(v) for v in a['category_accuracy_min_max'])} |",
        f"| Priority accuracy | {_pct(a['priority_accuracy'])} | |",
        f"| Injection recall | {_pct(a['injection_recall'])} | |",
        f"| Injection false-positive rate | {_pct(a['injection_false_positive_rate'])} | |",
        f"| Stability (same action in every run) | {_pct(a['stability'])} | |",
        f"| Tokens in / out (all runs) | {a['tokens_in_total']:,} / {a['tokens_out_total']:,} | |",
        f"| Wall time | {summary['wall_time_s']['total']}s total | "
        f"{' / '.join(str(w) + 's' for w in summary['wall_time_s']['per_run'])} per run |",
        "",
        f"Unstable tickets: {', '.join(a['unstable_tickets']) or 'none'}", "",
        "## Errors per ticket", "",
        "Every field that disagreed with the golden label, with how many runs produced each wrong value.", "",
        "| Ticket | Field | Expected | Got (runs) |", "|---|---|---|---|",
    ]
    for e in summary["per_ticket_errors"]:
        for field, d in e["mismatches"].items():
            got = ", ".join(f"{v} ({n}/{e['runs']})" for v, n in d["got"].items())
            lines.append(f"| {e['ticket_id']} | {field} | {d['expected']} | {got} |")
    if not summary["per_ticket_errors"]:
        lines.append("| - | - | - | no mismatches |")
    lines += ["", "Exceptions during runs: " + (", ".join(
        f"run {x['run']} {x['ticket_id']}: {x['error']}" for x in summary["exceptions"]) or "none"), ""]
    ds = summary.get("detector_suite")
    if ds:
        lines += [
            "## Deterministic injection detector on the adversarial suite", "",
            f"`tests/fixtures/injection_cases.jsonl`: {ds['cases']} hand-written cases "
            f"({ds['positives']} injections, {ds['negatives']} benign look-alikes), no model involved.", "",
            f"- Recall {_pct(ds['recall'])}, false-positive rate {_pct(ds['false_positive_rate'])}",
            f"- False positives: {'; '.join(ds['false_positives']) or 'none'}", "",
        ]
    if notes.exists():
        lines += [notes.read_text().rstrip(), ""]
    path.write_text("\n".join(lines), encoding="utf-8")
