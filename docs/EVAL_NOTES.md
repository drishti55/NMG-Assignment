# Evaluation Analysis

## 1. How I tested the system

- I ran `make eval RUNS=5` on the 24 golden tickets.
- Each ticket went through the same LangGraph workflow used in production, with business writes and human approval disabled for evaluation.
- I compared the predicted actions, categories, priorities, and injection detection against `data/golden.jsonl`.
- Both models ran with temperature 0 and seed 7 to keep results consistent.

## 2. Consistency of results

- All five runs produced the same actions and categories, giving **100% stability**.
- Token usage was also identical in every run: 30,109 input tokens and 2,295 output tokens.
- I reproduced the same results across three evaluation sessions.
- Execution time varied depending on system load, so I treat latency as an estimate rather than a fixed benchmark.

## 3. Where the system struggled

- **HD-T05 and HD-T06:** Dental insurance and root-canal pricing questions were escalated because the triage model repeatedly classified them as clinical.
- **NW-T03 and HD-T11:** The model suggested asking for more information but did not specify what was missing. The system therefore generated replies instead. NW-T03 still asks for the order ID and photos.
- **NW-T11:** The system failed to retrieve the correct shipping article for a Hinglish delivery question. After the draft failed the content-support check twice, the ticket was escalated.
- **NW-T12:** The system correctly escalated the bulk-discount question, but assigned `warranty_repair` instead of `other`.
- **Priority classification:** The model often defaults to `normal`. Existing rules handle urgent cases such as legal threats and severe symptoms, but general questions are not consistently assigned `low`.

## 4. Prompt injection results

- Both golden injection tickets, NW-T07 and HD-T07, were detected in all five runs.
- No false positives were recorded on the golden set.
- The deterministic detector caught both attacks, so detection did not depend on the model.

These results are encouraging, but the evaluation set is small and cannot represent every possible attack.

## 5. What I would improve next

1. Define required information for each category in the tenant configuration, such as an order ID and photos for warranty claims.
2. Add deterministic priority rules to identify general-information questions that should be marked `low`.
3. Test the system on a separate dataset that was not used to design the prompts or rules.
4. Try `qwen2.5:3b` for triage and compare its accuracy against the additional latency and resource usage.

Overall, the system is consistent on the current dataset, but retrieval for Hinglish tickets and some category and priority decisions still need improvement.