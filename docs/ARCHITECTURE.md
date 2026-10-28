# Architecture

## 1. Project Overview

- Project Relay runs locally using **LangGraph, FastAPI, React, PostgreSQL, and Ollama**.
- PostgreSQL stores tickets, knowledge base articles, decisions, approvals, and execution logs.
- pgvector helps retrieve relevant knowledge base information.
- Ollama runs the AI models locally, so no paid API is needed.
- The frontend communicates with FastAPI using REST APIs and Server-Sent Events (SSE).

## 2. Why LangGraph?

- I used LangGraph because the workflow needs more than just calling AI models.
- It supports conditional routing, controlled retries, and pausing execution for human approval.
- Its PostgreSQL checkpointer saves workflow state, allowing a ticket to resume after a restart.
- This is easier to manage than building the entire state machine and checkpoint system from scratch.

## 3. Agents and Their Responsibilities

- **Lead / Orchestrator:** Uses Python logic to control the workflow, manage budgets, and make the final routing decision.
- **Triage Agent:** Uses `qwen2.5:1.5b` to identify the ticket category, priority, and suggested action.
- **Retrieval:** Searches the tenant's knowledge base using vector search and PostgreSQL full-text search.
- **Drafting Agent:** Uses `qwen2.5:3b` to prepare a reply based on the ticket and relevant knowledge.
- **Safety Checker:** Checks drafts for unsupported claims, internal information, cross-tenant content, and other safety issues.
- **Human Approver:** Reviews drafts before they can be sent to the customer.

The agent handoffs use Pydantic contracts, so model outputs are validated instead of being extracted using regular expressions.

## 4. How a Ticket Is Processed

1. The system validates the ticket and tenant.
2. The triage agent analyses the ticket.
3. The lead reviews the suggested action and applies safety rules.
4. The retrieval component finds relevant knowledge base information.
5. The drafting agent prepares a response.
6. The checker validates the draft. If necessary, one correction attempt is allowed.
7. Safe drafts enter the human approval queue.
8. Approved drafts are written to the outbox; rejected drafts can be revised once.
9. Tickets requiring escalation, or those that fail processing, are recorded accordingly.

The workflow uses bounded retries to avoid getting stuck in repeated loops.

## 5. Human Approval and Recovery

- Approval and rejection are available through the CLI and web interface.
- Every decision is recorded with the relevant user, timestamp, and audit information.
- Duplicate approvals cannot create duplicate outbox entries.
- If a worker crashes, another worker can recover unfinished jobs after the lease expires.
- LangGraph checkpoints allow interrupted workflows to resume from their saved state.

## 6. Security and Tenant Isolation

- Northwind Cycles and Harbor Dental have separate tenant data.
- PostgreSQL Row-Level Security (RLS) restricts which tenant's records the application can access.
- The application uses a restricted database role rather than a superuser.
- Tenant context is validated for database operations and checkpoint access.
- Missing or invalid tenant context fails closed.
- Internal knowledge base content, private identifiers, and other tenants' information are blocked from customer-facing outputs.

Harbor Dental also has stricter rules: clinical questions are escalated instead of receiving AI-generated medical advice.

## 7. Model Gateway and Budgets

- All model calls go through a single gateway.
- It selects the appropriate model, checks token budgets, and records actual token usage when reported by Ollama.
- It also tracks latency and model errors.
- When a budget is exhausted, the ticket is marked `held_budget` instead of crashing the workflow.
- Failed jobs use bounded retries with backoff; permanently failed jobs are moved to the dead-letter process.

## 8. Frontend and Live Monitoring

- The frontend is built using React and TypeScript.
- It displays tickets, drafts, approvals, execution status, and system activity.
- Developer Mode exposes technical details such as graph execution, model usage, retrieval results, and checker outcomes.
- Live monitoring uses actual backend events streamed through SSE.
- A graph node is highlighted only when its corresponding execution event exists; the UI does not simulate agent activity.

## 9. Testing and Evaluation

- Pytest tests cover tenant isolation, approval handling, idempotency, budget limits, and crash recovery.
- Most tests use a deterministic fake model, so they can run without Ollama.
- The evaluation command runs the golden dataset multiple times and records accuracy, injection detection, consistency, token usage, and execution time.
- Results are saved under `eval/runs/` and documented in `docs/EVAL.md`.

## 10. Main Design Decisions

- **LangGraph:** For workflow control, checkpoints, and human approval.
- **PostgreSQL + pgvector:** For persistent storage, semantic retrieval, and tenant isolation.
- **Local Ollama models:** To meet the zero-cost requirement.
- **Deterministic safety checks:** To add safeguards beyond the model's own judgement.
- **Durable queue:** To recover unfinished work after failures.
- **Human approval:** To prevent drafts from being sent without review.

The main trade-off is that local models can still make mistakes, and safety checks cannot guarantee every draft is correct. That is why the system combines model outputs with deterministic rules, tenant isolation, testing, and human review.