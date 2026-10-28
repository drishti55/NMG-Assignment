# Limitations

## Missing or partial
* **S1 circuit breaker + fallback model: not built.** `CHAOS_FAIL_RATE` injects
  gateway failures and the queue retries with backoff, but there is no breaker that
  stops calling a failing provider, and no fallback model.
* **Optional free hosted route:** not built. Only Ollama.
* **Authentication is a local stand-in.** Bearer tokens map to an operator name and
  tenant grant via `RELAY_API_TOKENS`; the Vite dev proxy injects the dev token. No
  login, sessions, roles, or token rotation. `/health` is unauthenticated and reveals
  model names and the Postgres version. Do not expose the API beyond localhost.
* **CLI approver identity is self-declared** (`--by name`). The audit trail records
  it, but nothing verifies it.
* **The web console was checked by a production build, a TypeScript check, API calls
  through the proxy, and headless-Chrome screenshots of the main pages**, not by
  automated browser tests.

## Fragile
* **Model quality.** On the golden set, action accuracy is 79% and priority accuracy
  63% (see EVAL.md). Drafts that pass every checker rule can still be wrong: in the
  final demo the NW-T08 draft misstated the tyre warranty and cited the wrong article
  for "order ID". During final verification a Harbor draft inverted the cancellation
  policy ("more than 24 hours … a fee will be charged"; the KB says *less* than 24
  hours) and still passed all 13 rules. The human gate is the control for that; the
  checker cannot judge meaning.
* **Case summaries are model-written** (triage `summary`) and can be inaccurate
  (HD-T10's says "double charge" for an overcharge). They contain only the customer's
  own ticket content, never KB text.
* **Pattern-based signals.** The injection, clinical, legal and money-dispute
  detectors are English (plus some Hinglish) regex families. Novel phrasing or other
  languages can slip past them; the model vote, the checker and the human remain.
* **Thresholds tuned on visible data.** The retrieval floor (0.5), RRF k (10), the
  checker's content-support threshold (65%) and the prompts were adjusted while
  looking at the 24 seed tickets. Expect lower accuracy on unseen tickets.
* **Hinglish retrieval.** NW-T11 ("Order kab aayega") does not retrieve the shipping
  article; it safely escalates (`checker_failed`) instead of being answered.
* **Budget pre-check uses an estimate** (characters / 4) for the next prompt; actual
  usage is recorded from Ollama. Concurrent workers can overshoot the run budget by
  at most the calls already in flight.
* **SSE is Postgres polling** (0.5 s per open stream).

## Security assumptions
* Checkpoint tables are under RLS since migration 0002, keyed by a column Postgres
  derives from the thread id. The isolation relies on thread ids always being created
  as `<tenant>:...` by `thread_id_for()`; a thread id with another tenant's prefix is
  rejected on write, but a tenant could still create oddly named threads for itself.
  If LangGraph changes its checkpoint schema in a future version, migration 0002 must
  be re-checked (it is pinned at langgraph-checkpoint-postgres 3.2.0).
* The `jobs`, `runs`, `rejections` and `eval_runs` tables are platform tables without
  RLS; they hold ids, counters and reasons, never ticket text.
* `rejections.raw_tenant` stores whatever tenant string an unknown ticket claimed.
* Postgres runs locally with default passwords from `.env.example`; change them for any
  shared environment.
* Model output is treated as untrusted: it is schema-validated and checked, but a
  human must still read every customer-facing text.

## With one more day
1. Circuit breaker + fallback model in the gateway; LISTEN/NOTIFY for SSE.
2. A larger, held-out evaluation set (paraphrases, Hinglish, multi-intent tickets)
   so thresholds are not tuned on the visible set; per-signal precision/recall.
3. Better coverage detection: a calibrated retrieval re-ranker instead of the
   content-support heuristic.
4. Playwright tests for the approval flow in the console.
