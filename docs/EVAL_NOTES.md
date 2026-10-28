## Analysis

These notes are written by hand from the generated tables above (they are appended
verbatim by `relay/evaluation.py`; the numbers above are not edited).

**Method.** `make eval RUNS=5` loads the 24 golden tickets from `data/*/tickets.jsonl`,
runs each through the production LangGraph graph with business writes and the
approval interrupt switched off, and scores action, category, priority and injection
against `data/golden.jsonl`. `golden.jsonl` is read only by `relay/evaluation.py`.
Temperature 0 and seed 7 for both models.

**What reproduced.** All five runs produced identical actions and categories
(stability 100%), and token totals were identical too (30,109 in / 2,295 out per
run): at temperature 0 with a fixed seed, Ollama was fully repeatable on this machine.
Two earlier 5-run evaluations (one on a slightly older build) produced the same
accuracies and the same token totals, so the result reproduced three times. Wall time
is the least stable number: 112–136 s per run in this session, 73–125 s in earlier
ones, depending on what else the machine was doing. Treat timings
as indicative. Model output can differ on other hardware, so a re-run elsewhere may not
reproduce these numbers exactly.

**Where it is wrong, and why.**
* *HD-T05, HD-T06 (insurance, root-canal price):* the 1.5B triage model labels dental
  pricing as clinical even after the lead's single re-check, and the policy trusts a
  repeated clinical vote, so they escalate. This is a deliberate safe-side error
  (DECISIONS.md §9).
* *NW-T03 (cracked frame), HD-T11 (records, unknown customer):* the model proposes
  ask_for_info but leaves `missing_info` empty; the lead's consistency rule then makes
  them draft replies. NW-T03's draft still asks for the order id and photos.
* *NW-T11 (Hinglish delivery question):* retrieval misses the shipping article; the
  draft fails the content-support rule twice and escalates.
* *NW-T12:* action correct (escalate, KB does not cover bulk discounts) but category
  `warranty_repair` instead of `other`.
* *Priority:* the model defaults to `normal`; deterministic floors fix urgent/high for
  legal threats, injections, severe symptoms, money disputes, but nothing lowers
  priority to `low` for general questions.

**Injection.** Both golden injections (NW-T07, HD-T07) were flagged in every run, with
no false positives; the deterministic detector alone catches both, so the result does
not depend on the model.

**Next steps.**
1. Name the required details per category in tenant config (e.g. warranty: order id
   + photos) so ask_for_info does not depend on the 1.5B model listing them.
2. Add a priority rubric step (deterministic "general information" detection) to fix `low`.
3. Evaluate on a held-out set written independently of the prompts.
4. Try `qwen2.5:3b` for triage and report the accuracy / latency trade-off.
