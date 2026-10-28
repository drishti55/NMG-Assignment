"""Retrieval agent: hybrid search over the current tenant's KB only.

pgvector cosine similarity (nomic-embed-text) and Postgres full-text search are fused
with reciprocal-rank fusion. Measured on the seed tickets, cosine alone ranked the wrong
article first for short or Hinglish tickets ("Where is my cycle", "Order kab aayega");
place names and policy words ("Delhi", "delivery") fix that lexically.

Deterministic (embedding + SQL), not an LLM: nothing here needs generation, and a
deterministic node is cheaper, reproducible and testable.

Isolation is enforced twice: RLS (the session is bound to one tenant) and an explicit
tenant predicate. Internal articles are returned as ids only (routing hints), never text.
"""
from __future__ import annotations

import numpy as np

from relay.contracts import Passage, RetrievalResult, Ticket
from relay.db import tenant_tx
from relay.gateway import CallContext, ModelGateway, Usage

QUERY_PREFIX = "search_query: "      # nomic-embed-text task prefixes
DOCUMENT_PREFIX = "search_document: "
RRF_K = 10  # small k: the KBs are tiny, so rank differences must stay visible


def document_text(title: str, body: str) -> str:
    return f"{DOCUMENT_PREFIX}{title}. {body}"


_SQL = """
WITH q AS (
    SELECT to_tsquery('english', coalesce(
        (SELECT string_agg(quote_literal(lexeme), ' | ')
         FROM unnest(to_tsvector('english', %(text)s))), '')) AS tsq
),
vec AS (
    SELECT kb_id, 1 - (embedding <=> %(v)s) AS cosine,
           row_number() OVER (ORDER BY embedding <=> %(v)s) AS vrank
    FROM kb_articles
    WHERE tenant_id = current_setting('app.tenant_id', true)
),
lex AS (
    SELECT kb_id, row_number() OVER (ORDER BY ts_rank_cd(tsv, q.tsq) DESC) AS lrank
    FROM kb_articles, q
    WHERE tenant_id = current_setting('app.tenant_id', true) AND tsv @@ q.tsq
)
SELECT a.kb_id, a.title, a.body, a.internal, vec.cosine, vec.vrank, lex.lrank,
       1.0 / (%(k)s + vec.vrank) + coalesce(1.0 / (%(k)s + lex.lrank), 0) AS rrf
FROM kb_articles a
JOIN vec USING (kb_id)
LEFT JOIN lex USING (kb_id)
WHERE a.tenant_id = current_setting('app.tenant_id', true)
ORDER BY rrf DESC, vec.vrank
"""


def search(tenant_id: str, text: str, vector: list[float], top_k: int,
           min_score: float) -> RetrievalResult:
    with tenant_tx(tenant_id) as conn:
        rows = conn.execute(
            _SQL, {"v": np.array(vector, dtype=np.float32), "text": text, "k": RRF_K}
        ).fetchall()
    public = [r for r in rows if not r["internal"]][:top_k]
    passages = [
        Passage(kb_id=r["kb_id"], title=r["title"], text=r["body"],
                score=round(float(r["cosine"]), 4), lexical_rank=r["lrank"])
        for r in public if float(r["cosine"]) >= min_score
    ]
    top = max((float(r["cosine"]) for r in public), default=0.0)
    return RetrievalResult(
        passages=passages,
        internal_hits=[r["kb_id"] for r in rows[:top_k] if r["internal"]],
        top_score=round(top, 4),
        adequate=bool(passages),
    )


def retrieve(gateway: ModelGateway, ticket: Ticket, ctx: CallContext, top_k: int,
             min_score: float) -> tuple[RetrievalResult, Usage]:
    vectors, usage = gateway.embed([QUERY_PREFIX + ticket.text(2000)], ctx)
    lexical = f"{ticket.subject}\n{ticket.body}"[:2000]
    return search(ctx.tenant_id, lexical, vectors[0], top_k, min_score), usage
