# AI Usage

## Tools and Models

- **Claude Code (Anthropic, Claude Opus 5.5):** I used Claude Code as my primary coding assistant for building Project Relay. It helped implement the Python backend, database migrations, tests, React console, and documentation. I provided the assessment brief and a detailed build specification, then reviewed the implementation and test results through follow-up iterations.

- **Ollama models:** The application uses `qwen2.5:1.5b` for triage, `qwen2.5:3b` for drafting, and `nomic-embed-text` for embeddings. These are runtime models used by Project Relay, not coding assistants.


## How Much Was AI-Generated?

Most of the code and documentation were generated with AI assistance. My main contribution was defining the requirements, architecture, security constraints, expected behaviour, and how the finished application should be tested.

In particular, I specified:

- **Agent responsibilities:** The roles, inputs, outputs, and limitations of the lead/orchestrator, triage, retrieval, drafting, and safety-checker components. I also defined which decisions must be deterministic rather than left to an LLM.
- **Frontend requirements:** The eight console pages, their expected functionality, and the visual direction, including dark graphite surfaces, green accents, and real rather than simulated metrics.
- **Documentation:** The required README and architecture, decisions, evaluation, runbook, AI usage, and limitations documents.
- **Technical and security constraints:** The technology stack, tenant isolation, approval requirements, evaluation expectations, and the need to verify implementation claims rather than simply trust generated code.

## My Review and Testing

I did not treat the initial generated implementation as automatically correct. I reviewed the reported results and asked for additional investigation when issues appeared.

My work included:

- Writing the build specification based on the assessment requirements.
- Requesting a focused audit of LangGraph checkpoint tenant isolation after identifying it as a potential security risk.
- Asking for tests, demo behaviour, and evaluation claims to be verified against actual results.
- Reviewing the resulting reports and investigating unexpected behaviour.
- Running the demo and making approval and rejection decisions through the CLI and web console.

I am continuing to study the implementation so that I can explain the architecture, important design decisions, and security controls during the technical walkthrough.

**My preparation before the walkthrough:** I will review `docs/TECHNICAL_GUIDE.md` and make sure I can explain the code paths, tests, and trade-offs rather than relying only on the generated documentation.

## Cases Where the AI Was Wrong and How They Were Caught

### 1. Row-level security initially failed open

The earlier scaffold contained an RLS policy that could return rows from every tenant when the tenant context was missing. This was caught by reading the migration before building on it. The policy was replaced with one that matches no rows when the tenant context is unset, and `test_missing_tenant_context_returns_zero_rows` verifies this behaviour.

### 2. The safety checker allowed invented facts

Some real drafts included an unsupported delivery estimate of around three days, a made-up email address, and a reference to a non-existent sales team. These drafts passed the original checker. After reviewing persisted outputs, additional checks were introduced: `NUMBERS_GROUNDED`, `NO_UNGROUNDED_CONTACTS`, and `CONTENT_SUPPORTED`.

### 3. Courtesy replies included unsupported claims

The smaller triage model sometimes classified genuine requests as messages that required no action. This sent them down a courtesy-response path, which could produce unsupported statements such as claiming that returns were not handled for a product. The path was tightened so that it also requires the category to be `other`.

### 4. A test fake interpreted the wrong message

One test initially made a correctly behaving graph appear broken because the fake model was reading the wrong message. I identified the issue through the failing test and the persisted execution-event trail, then corrected the test setup.

### 5. Tests overwrote committed output files

The test suite could overwrite the committed `out/` files through the API's export functionality. I caught this by comparing `out/decisions.jsonl` before and after running `make test`. The output directories were subsequently made configurable, allowing tests to write to a temporary directory instead.

### 6. Recovery after a worker crash was incomplete

After a worker was killed during execution, its orphaned jobs retained the old run ID and would not finish correctly on a subsequent run. This was discovered while designing the crash-recovery test. The recovery logic was updated to adopt those jobs, and both an automated test and a real `kill -9` test were used to verify recovery without duplicate decisions.

### 7. SSE pagination could skip events

During review, it was found that event pagination could skip events across tenants when one tenant's page filled up. The merge logic was corrected to stop at the earliest full page.

### 8. LangGraph checkpoints were not covered by tenant RLS

The initial implementation documented checkpoint storage outside RLS as a limitation rather than fixing it. A follow-up audit demonstrated the risk: a database session associated with Harbor could read Northwind ticket text from `checkpoint_blobs`, and the orchestrator accepted thread IDs without validating their tenant ownership.

This was addressed through migration `0002` and a tenant-bound checkpointer. The regression tests were also checked by temporarily disabling the fix and confirming that 9 of the 14 relevant tests failed.

### 9. Approval records needed stronger verification

Earlier local or scratch-database runs included approvals recorded under the candidate's name using `--by drishti`. A verification script also approved a draft that reversed the cancellation policy. These incidents highlighted why an approval record must correspond to a draft that has actually been reviewed.

The committed `out/outbox.jsonl` was generated from a fresh run in which I reviewed the drafts and made the approval decisions myself: through the CLI as `drishti` and through the web console as `operator`.

## Final Reflection

Building Project Relay with AI assistance made it possible to implement a substantial system, but it also showed me why generated code must be tested and reviewed carefully. Several important issues were found only by inspecting actual outputs, tracing execution events, and testing failure scenarios.

The most important lesson I took from this process is that a successful run or passing test suite is not enough on its own. Security boundaries, recovery behaviour, model outputs, and approval decisions all need evidence that they work as intended.