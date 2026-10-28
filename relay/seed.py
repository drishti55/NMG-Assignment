"""Seed each tenant's KB from data/<tenant>/kb.jsonl, embedding through the gateway."""
from __future__ import annotations

import json
from pathlib import Path

from relay.agents.retrieval import document_text
from relay.db import tenant_ids, tenant_tx
from relay.gateway import CallContext, ModelGateway


def seed_kb(gateway: ModelGateway, data_dir: Path) -> dict[str, int]:
    """Idempotent: an article is (re-)embedded only when its text or the model changed."""
    counts: dict[str, int] = {}
    model = gateway.model_for("embed")
    for tenant in sorted(tenant_ids()):
        path = data_dir / tenant / "kb.jsonl"
        if not path.exists():
            continue
        articles = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        foreign = [a["id"] for a in articles if a["tenant"] != tenant]
        if foreign:
            raise ValueError(f"{path} contains articles for another tenant: {foreign}")
        with tenant_tx(tenant) as conn:
            existing = {r["kb_id"]: r for r in conn.execute(
                "SELECT kb_id, title, body, internal, embed_model FROM kb_articles").fetchall()}
        todo = [a for a in articles if not (
            (e := existing.get(a["id"])) and e["title"] == a["title"] and e["body"] == a["body"]
            and e["internal"] == a["internal"] and e["embed_model"] == model)]
        if todo:
            vectors, _ = gateway.embed([document_text(a["title"], a["body"]) for a in todo],
                                       CallContext(tenant, None, None, None))
            with tenant_tx(tenant) as conn:
                for a, v in zip(todo, vectors):
                    conn.execute(
                        """INSERT INTO kb_articles (tenant_id, kb_id, title, body, internal,
                               embedding, embed_model)
                           VALUES (%s,%s,%s,%s,%s,%s::vector,%s)
                           ON CONFLICT (tenant_id, kb_id) DO UPDATE SET title = EXCLUDED.title,
                               body = EXCLUDED.body, internal = EXCLUDED.internal,
                               embedding = EXCLUDED.embedding, embed_model = EXCLUDED.embed_model""",
                        (tenant, a["id"], a["title"], a["body"], a["internal"], str(v), model),
                    )
        counts[tenant] = len(todo)
    return counts
