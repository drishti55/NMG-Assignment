"""Writes out/decisions.jsonl and out/outbox.jsonl from the database.

Both files are regenerated from the source of truth, so re-running never duplicates
rows. Before writing, every row is scanned for internal KB ids, internal text and
canary codes; a hit aborts the export (the brief forbids them anywhere under out/).
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from relay import store
from relay.config import get_settings
from relay.contracts import OutboxRow
from relay.db import tenant_ids, tenant_tx

_CANARY = re.compile(r"canary", re.I)


class LeakError(RuntimeError):
    pass


def _internal_markers() -> tuple[set[str], list[str]]:
    ids: set[str] = set()
    bodies: list[str] = []
    for tenant in tenant_ids():
        with tenant_tx(tenant) as conn:
            for r in conn.execute("SELECT kb_id, body FROM kb_articles WHERE internal").fetchall():
                ids.add(r["kb_id"])
                bodies.append(r["body"])
    return ids, bodies


def _guard(lines: list[str]) -> None:
    ids, bodies = _internal_markers()
    for line in lines:
        if _CANARY.search(line) or any(i in line for i in ids) or any(b in line for b in bodies):
            raise LeakError("export aborted: a row contains internal KB content")


def _write_atomic(path: Path, lines: list[str]) -> None:
    _guard(lines)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
    os.replace(tmp, path)


def export_decisions(path: Path | None = None) -> Path:
    path = path or get_settings().out_dir / "decisions.jsonl"
    lines = [row.model_dump_json() for t in sorted(tenant_ids()) for row in store.decision_rows(t)]
    _write_atomic(path, lines)
    return path


def export_outbox(path: Path | None = None) -> Path:
    path = path or get_settings().out_dir / "outbox.jsonl"
    lines = []
    for tenant in sorted(tenant_ids()):
        with tenant_tx(tenant) as conn:
            for r in conn.execute("SELECT * FROM outbox ORDER BY approved_at").fetchall():
                lines.append(OutboxRow(
                    ticket_id=r["ticket_id"], tenant=r["tenant_id"], text=r["text"],
                    approved_by=r["approved_by"], approved_at=r["approved_at"].isoformat(),
                    idempotency_key=r["idempotency_key"],
                ).model_dump_json())
    _write_atomic(path, lines)
    return path


def export_rejections(path: Path | None = None) -> Path:
    """Tickets refused at intake (unknown tenant, malformed), one row per run and ticket."""
    from relay.db import platform_tx

    path = path or get_settings().out_dir / "rejected.jsonl"
    with platform_tx() as conn:
        rows = conn.execute(
            "SELECT run_id, raw_tenant, ticket_id, reason, at FROM rejections ORDER BY rejection_id"
        ).fetchall()
    _write_atomic(path, [json.dumps(r, default=str) for r in rows])
    return path
