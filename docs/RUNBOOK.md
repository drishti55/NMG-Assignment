# Runbook

## Setup (once)

```bash
# 1. Prerequisites: Docker, uv, Ollama (all free). Python 3.11 is fetched by uv if missing.
ollama pull qwen2.5:1.5b && ollama pull qwen2.5:3b && ollama pull nomic-embed-text
# 2. Everything else
make demo           # Postgres, migrations, KB embeddings, both inboxes, approval queue
make dev            # API on :8077 + console on http://localhost:5173
```

Configuration is environment-based; `.env.example` lists every variable with a safe
default. Copy it to `.env` to override. `.env` is git-ignored.

| Task | Command |
|---|---|
| Health of every dependency | `make health` |
| Apply migrations | `make migrate` (idempotent; runs as the `postgres` admin role) |
| Re-embed KBs after editing `data/<tenant>/kb.jsonl` | `make seed` (only changed articles) |
| Process an inbox | `make run INBOX=path/to/tickets.jsonl` |
| Long-running worker | `make worker` |
| Approval queue | `make approve` (list) · `make approve ID=apr-… BY=name` · `make reject ID=apr-… REASON='…' BY=name` |
| Cases | `make cases` |
| Reports | `make report` · `make report VIEW=client TENANT=harbor` |
| Re-export outputs from the DB | `make export` |

## Ollama is down

Symptoms: `make health` shows `FAIL ollama`; `relay run` exits with "Ollama is not
reachable"; in a running worker, jobs show `job_retry_scheduled` with
`error_class=model_unavailable` and back off (2 s, 4 s, … capped at 60 s, with jitter).

1. Start it: `ollama serve` (or open the Ollama app). Check `curl localhost:11434/api/tags`.
2. Jobs still inside their retry budget finish by themselves once Ollama is back.
3. Jobs that exhausted `RELAY_MAX_ATTEMPTS` are dead letters: a `dead_letter` case
   (owner `ops-oncall`) and a `failed` decision. Re-run the same inbox
   (`make run INBOX=…`): failed tickets are re-processed as a new generation; decided
   tickets are skipped.
4. If a model is missing (`model_missing`, never retried), `ollama pull <model>` and re-run.
   The system never substitutes another model.

## The worker died

Nothing is lost: job state, checkpoints and pending approvals are in Postgres.

1. Start again: `make worker`, or re-run the same inbox with `make run INBOX=…`
   (it adopts jobs left unfinished by the dead run).
2. A job the dead process was holding becomes claimable when its lease expires
   (`RELAY_LEASE_SECONDS`, default 30 s; a live worker's heartbeat keeps extending it).
3. The new worker resumes each LangGraph thread from its last checkpoint; completed
   steps (e.g. triage) are not redone. Decisions, approvals and outbox rows are
   unique-constrained, so there are no duplicates.
4. `make worker` also runs `reconcile()`: approvals decided but not yet resumed
   (process died between the DB commit and the graph resume) are resumed.

Check: `make health` (queue line shows expired leases awaiting reclaim), or the
Run inspector's "Queue lease" panel.

## A budget ran out

Symptoms: tickets with status `held_budget` (dashboard "held on budget", decisions.jsonl).
The run does not crash; other tickets continue until the run budget is spent.

1. Find which budget: Run inspector → node timeline shows `error_code: budget_exceeded`;
   `make report` shows run token totals.
2. Raise the budget in `.env`: `RELAY_MAX_TOKENS_PER_TICKET` (default 6000; a normal
   ticket uses ~1,000–2,500) or `RELAY_MAX_TOKENS_PER_RUN` (default 200,000).
3. Re-run the same inbox: only held/failed tickets are re-processed.
4. If a single ticket keeps exceeding the per-ticket budget, it is probably very long
   (ticket text is truncated at 4,000 characters) or looping through repairs: inspect it.

## The database is unreachable

Symptoms: `make health` FAIL postgres; CLI errors `connection refused`; API 500s.

1. `docker compose ps`; start with `make db` (waits for healthy).
2. Jobs that failed with `OperationalError` mid-run were classified transient and
   retried; if they were dead-lettered, re-run the inbox.
3. Data lives in the `relay-pgdata` volume. `make down` keeps it; `make clean` deletes it.
4. Migrations missing (`relation … does not exist`): `make migrate`.

## A draft is rejected twice

By design the drafter gets one revision using the reviewer's reason. A second
rejection escalates: the decision becomes `escalated`, a case `rejected_twice` is
opened (owner `support-lead`), and nothing reaches the outbox.

1. `make cases` or the Escalation center: the audit trail lists both reasons.
2. A human writes the reply outside the agent (the system does not send manual replies).
3. If the same kind of ticket keeps being rejected, the KB is probably missing the
   fact the reviewers want; add it to `data/<tenant>/kb.jsonl` and run `make seed`.

## Other situations

* **Unknown tenant in an inbox:** the line is rejected, logged to `rejections` and
  `out/rejected.jsonl`, never processed. Add the tenant to `config/tenants.json` and
  `make migrate` if it is legitimate.
* **Injection flagged:** an escalation case owned by `security-review`. Confirm in the
  Run inspector that nothing was drafted, then answer the genuine question by hand.
* **Checker keeps failing for one tenant:** look at the failing rule ids in the
  Approval center or Run inspector; messages never contain internal text.
* **Chaos test:** `CHAOS_FAIL_RATE=0.3 make run INBOX=…` makes 30% of model calls fail;
  watch retries in the Live monitor.
