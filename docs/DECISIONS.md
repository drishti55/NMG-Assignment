# Decisions and Trade-offs

## 1. Why I used a deterministic lead

- The triage model suggests what to do, but Python makes the final routing decision.
- I chose this because prompts alone cannot guarantee that clinical questions or prompt injections will always be escalated.
- The downside is that manually written rules can miss some unusual wording, so the model and human review still help.

## 2. How I detect prompt injection

- I used multiple signals instead of relying on a simple keyword list or the model alone.
- The detector checks suspicious role instructions, override attempts, data extraction requests, and other patterns.
- My test suite achieved 100% recall and 0% false positives on its tested examples.
- This does not guarantee detection of every new attack, especially unfamiliar phrasing or other languages.

## 3. Why I used hybrid retrieval

- I combined pgvector similarity search with PostgreSQL full-text search.
- Vector search alone sometimes ranked irrelevant articles above the correct ones, especially for short or Hinglish tickets.
- Combining both methods improves retrieval, but the system still needs additional checks to decide whether the knowledge base actually supports an answer.

## 4. Why the checker uses extra rules

- I added checks for unsupported numbers, invented contact details, and content that is not supported by the ticket or cited articles.
- These rules were added after I found drafts inventing details such as delivery timelines and email addresses.
- I avoided an LLM-based checker because I wanted the safety checks to be deterministic.
- The rules can still be too strict or too lenient on tickets outside the seed dataset.

## 5. Why I used PostgreSQL for multiple components

- PostgreSQL handles the queue, workflow checkpoints, application data, and event logs.
- This keeps the setup simpler and avoids adding Redis or RabbitMQ for a relatively small workload.
- The trade-off is that live events are currently polled every 0.5 seconds, which may become expensive as the system grows.

## 6. How approval and recovery work

- Approval decisions are saved and audited in a database transaction before the workflow resumes.
- The graph then handles writing approved replies to the outbox.
- I chose this approach because a crash should not leave the system without a recorded approval decision.
- A reconciliation process handles cases where approval was saved but the workflow did not resume.

## 7. How duplicate tickets are handled

- Tickets that already have a decision are skipped when the inbox is processed again.
- Failed or budget-held tickets can be retried as a new workflow generation.
- Unfinished jobs can be recovered after a worker crash.
- This prevents duplicate business outcomes, although a model call itself may run more than once after a crash.

## 8. How I handled checkpoint security

- LangGraph checkpoints can contain ticket text and drafts, so protecting normal application tables was not enough.
- I added tenant-aware RLS policies to the checkpoint tables and bound checkpoint operations to the authorized tenant.
- I found a real isolation issue during testing and added tests for cross-tenant reads, writes, and workflow resumes.
- The trade-off is that this solution depends on LangGraph's checkpoint table structure, so its version must be managed carefully.

## 9. Differences from some golden labels

- **HD-T05 and HD-T06:** The system escalates dental insurance and root-canal pricing questions because the triage model repeatedly classifies them as clinical. This is more conservative than the expected labels.
- **NW-T03:** The expected action is `ask_for_info`, but the system chooses `draft_reply` because the model does not clearly identify missing information. The generated reply still asks for the order ID and photos.
- **NW-T08:** The expected priority is `low`. I believe `normal` is more appropriate because the customer is asking about coverage for a specific order.

These differences show that conservative safety rules can reduce some classification accuracy.

## 10. How I would scale it to 50 tenants

- Keep shared PostgreSQL tables with RLS, including checkpoint isolation.
- Add tenant-specific budgets, rate limits, and fair scheduling so one tenant cannot dominate the queue.
- Run multiple workers and add a second inference server for better availability.
- Replace frequent SSE polling with PostgreSQL `LISTEN/NOTIFY` as traffic grows.
- Use managed PostgreSQL and separate application and inference servers.

The estimated hosting cost is around **$600–$1,000 per month**, excluding some additional costs such as backups and data transfer. This is only a rough estimate, not a confirmed quote.

