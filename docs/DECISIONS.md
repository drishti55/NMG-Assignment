# Decisions and trade-offs

## 1. The lead agent is deterministic code, not an LLM
**Chose:** triage proposes; a Python policy module decides, overriding only towards
safety (escalate, raise priority, ask for consent), recording every override.
**Rejected:** an LLM lead/supervisor that routes. A 1.5B or 3B model cannot be trusted
to enforce "never answer a clinical question" or "always escalate an injection"; a
prompt is not a control. Deterministic routing is also reproducible, testable
without a model, and cheap. Cost: the policy encodes rules by hand (regex signals),
which can miss phrasings; the model's own vote and the human gate are the backstops.

## 2. Injection detection: a scored multi-signal detector, with the model as corroboration
**Chose:** seven evidence families (fake role markers, override phrasing,
exfiltration requests, other-party data, privileged actions, addressing the AI,
separators) with weights and a threshold, after Unicode/zero-width normalization.
The model's `injection_suspected` counts only with some deterministic evidence.
**Rejected:** a keyword list (brittle, as the brief says), and trusting the model
alone (in testing the 1.5B model flagged angry-but-honest tickets as "instructions").
**How we know it works:** a hand-written suite of 14 attacks and 12 benign
look-alikes ("ignore the previous order", "my system shows…", "please print my
invoice") runs in the tests and in EVAL.md: 100% recall, 0 false positives on that
suite; 100% / 0% on the golden set. It is still a pattern detector: novel phrasing in
another language would get through to triage, then to a draft that the checker
and a human must still pass.

## 3. Hybrid retrieval (pgvector + Postgres full text, reciprocal-rank fusion)
**Chose:** cosine similarity from pgvector fused with `ts_rank_cd` (k=10).
**Rejected:** vector-only. Measured on the seed tickets, nomic-embed-text ranked the
assembly article above shipping for "Where is my cycle" and missed shipping for the
Hinglish ticket; similarity scores were also not a usable "KB covers this" signal
(an uncovered bulk-discount question scored 0.70, a covered Hinglish question 0.53).
So coverage is decided by (a) a low cosine floor, (b) the drafter citing nothing and
saying the passages do not answer, and (c) the checker's content-support rule
escalating drafts that are mostly not grounded in the cited passages.

## 4. Checker includes grounding heuristics, not just the brief's minimum rules
**Chose:** besides the required rules, `NUMBERS_GROUNDED` (every number must appear
in a cited passage or the ticket), `NO_UNGROUNDED_CONTACTS` (no invented emails,
links, phones) and `CONTENT_SUPPORTED` (≥65% of content words found in cited
passages/ticket). Each was added after real drafts invented "3 days", an email
address, and a "sales team".
**Rejected:** an LLM judge (not deterministic, and the brief requires a no-model
checker). The 65% threshold was calibrated on the seed drafts, so it may be too
strict or too lax on unseen tickets; failures route to one redraft, then escalation,
never to the customer.

## 5. Postgres as queue, checkpoint store and event bus
**Chose:** one Postgres for jobs (`SKIP LOCKED` + leases), LangGraph checkpoints,
and the SSE event log (polled every 0.5 s).
**Rejected:** Redis/RabbitMQ and LISTEN/NOTIFY. A broker adds an operational
dependency for a workload of tens of tickets; keeping job state, decisions and
checkpoints in one database means one backup and transactional idempotency (unique
constraints). Polling costs a query per open stream per 0.5 s; LISTEN/NOTIFY would
cut that at scale (see §10).

## 6. Approval: decide in a transaction, then resume the graph (with reconciliation)
**Chose:** the approval row is locked, decided and audited in one transaction; the
graph is then resumed and its `write_approved_outbox` node inserts the outbox row
under unique constraints. `reconcile()` resumes any decided-but-not-resumed thread.
**Rejected:** writing the outbox directly from the CLI (bypasses the graph, so the
UI would not show the path) and resuming the graph *before* committing the decision
(a crash would leave a resumed thread with no durable human decision).

## 7. Re-runs: same ticket id = same business decision
**Chose:** a decided ticket (pending approval or escalated) is skipped on re-run;
`held_budget` and `failed` tickets are re-processed as a new LangGraph thread
generation; unfinished jobs from a killed run are adopted.
**Rejected:** re-deciding on content change. The brief says a ticket sent twice has
the same id, and re-deciding would orphan pending approvals.

## 8. Checkpoint isolation: RLS on LangGraph's own tables, plus a tenant-bound saver
**Chose:** add a database-generated `tenant_id` column (from the thread id) to the
three checkpoint tables, apply the standard fail-closed RLS policy, and run each
orchestrator operation on a connection whose session is bound to the authorized
tenant (cleared on exit and on pool reset).
**Rejected:** (a) relying on `tenant:` prefixed thread ids checked in Python only:
any code path, bug or direct SQL could still read another tenant's checkpoint, and
an audit proved a Harbor-bound session could read Northwind ticket text; (b) a
custom checkpointer that rewrites LangGraph's SQL: more code to maintain against a
fast-moving library, for the same guarantee; (c) a schema or database per tenant:
stronger separation, but a pool and migration per tenant is heavy for two tenants.
Cost: the generated column depends on LangGraph keeping its table layout (pinned
version), and each operation holds one connection for its duration.

## 9. Disagreements with golden labels (rules kept as written)
* **HD-T05 / HD-T06 (insurance, root-canal price → draft_reply).** We agree with the
  label, but our system escalates them as clinical because the 1.5B model insists on
  "clinical" for dental pricing even after the lead's re-check, and we chose to trust
  a repeated clinical vote: wrongly escalating a price question costs a human a
  minute; wrongly answering a clinical question is the failure the brief forbids.
* **NW-T03 (cracked frame → ask_for_info).** The 1.5B model proposes ask_for_info
  but does not name the missing item, and our consistency rule turns "ask for
  nothing" into a reply. The draft still asks for order id and photos (it quotes
  NW-KB-03), so the customer outcome matches; the label does not.
* **NW-T08 (worn tyre → priority low).** We'd argue `normal`: the customer asks a
  specific coverage question about their order, not general information.

## 10. Scaling to 50 tenants (S4)
* **Tenancy:** keep shared tables + RLS (all tenant-keyed, checkpoints included); add
  per-tenant budgets and rate limits to `tenants`.
* **Workers:** run N stateless workers (the queue already supports concurrent
  claimers); add per-tenant fairness (`ORDER BY` a round-robin key) so one noisy
  inbox cannot starve the others.
* **Models:** 50 tenants × ~100 tickets/day ≈ 5,000 tickets/day ≈ 3 model calls each.
  One GPU box with Ollama/vLLM serving 1.5B + 3B handles that comfortably
  (a 3B draft is ~2–3 s on an M-series laptop; far less on a GPU). Add a second box
  behind the gateway for availability, with a circuit breaker in the gateway to fail
  over (not built yet: today the gateway only has chaos injection and the queue's retries).
* **Events:** replace SSE polling with LISTEN/NOTIFY fan-out from one listener per API process.
* **Hosting and rough cost:** managed Postgres with pgvector (e.g. a 2 vCPU/8 GB
  instance, roughly USD 100–200/month), one GPU VM for inference (an L4-class GPU,
  roughly USD 400–700/month on-demand, less reserved), two small app VMs/containers
  for API + workers (~USD 50–100/month). Order of USD 600–1,000/month before
  egress and backups. These are estimates from public list prices, not quotes.
