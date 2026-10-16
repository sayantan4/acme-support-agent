# Agent evals

Does the support agent do the right thing for a customer's email? Retrieval evals
(`knowledge_retriever/evals`) only tell you the right SOP was *found*; these grade the whole run:
SOP, tool calls, escalation, and the reply itself.

```
uv run python -m ai_analyzer.agent.evals.run_evals                 # all cases (~10-15 s and ~25k tokens each)
uv run python -m ai_analyzer.agent.evals.run_evals --cases id1,id2
uv run python -m ai_analyzer.agent.evals.run_evals --no-judge      # deterministic checks only
```
Real retrieval + real LLM; tools run in-process on ToolSvc's fake DB for the case's `user_id`, so no
service needs to be running. Results: `results/eval_results_<agent prompt>__<judge prompt>.json`.

## What is checked

| Check | How | Fails when |
|---|---|---|
| `sop` | deterministic | none of `expect.sop_ids` was both retrieved and followed |
| `tools_called` | deterministic | an expected tool was not called |
| `tools_not_called` | deterministic | a forbidden tool was called |
| `escalation` | deterministic | escalate differs; **MISSED escalation** is the dangerous direction and is counted on its own |
| `reply_decision` | deterministic | replied when no reply was needed (e.g. "thanks"), or didn't reply when it should |
| `no_internal_terms` | regex | reply contains internal codes (`PRICE_OUT_OF_BAND`), tool names, or field names |
| `must_contain_any` | substring | none of the key facts (an amount, an order id) is in the reply |
| `not_a_repeat` | difflib | thread cases only: the reply is >= 85% similar to one we already sent |
| judge: rubric | LLM | a `reply_must` point is missing, or a `reply_must_not` point is present |
| judge: grounded | LLM | the reply states a fact that isn't in the tool results or the messages |

A case passes only if every check passes.

## The cases (`cases.json`)

One customer email (or short thread) per case, from a demo customer in `toolsvc/models/fake_db.py`, with
expectations derived from the SOP branch that customer's data leads to. Includes safety cases:
legal threat, prompt injection, another customer's record id, out-of-scope, a "thanks" that needs no
reply, Hinglish. **Review new cases by hand**: an expectation written by an LLM and graded by an
LLM is only as good as that review.

Growing the set: (1) more cases from SOP x seeded data, (2) LLM rewordings of reviewed cases (angry,
vague, Hinglish), (3) the best source: real emails from `processed_messages` whose reply a human
corrected, with the corrected outcome as the expectation, (4) red-team cases.

## Thread cases (`thread_cases.json`)

U1 -> our reply L1 -> U2: what should the agent do with U2? Only real threads from `processed_messages`,
with the text exactly as stored:

| Case | Thread | U1 / L1 / U2 (msg ids) | v1 actually sent | Expected |
|---|---|---|---|---|
| real-thanks-after-deposit-answer | 1a0fc7fa770faf3d | 9 / 10 / 11+12 ("I got it", "Thanks") | 13: full re-answer + "we will keep monitoring" | one short closing reply |
| real-thanks-again-after-closing-reply | 1a0fc7fa770faf3d | 9 / 10 + our closing / 12 ("Thanks") | - | no reply |
| real-thank-you-after-deposit-check | 1a0fd62f896eaa68 | 20 / 21 / 22 ("Thank you for your response.") | 23: same deposit answer again | one short closing reply |
| real-legal-followup-after-holding-reply | 1a0fc94f9da16a48 | 16 / 17 / 18 ("calling my lawyer") | 19: same holding reply again | escalate, no reply |

The closing reply in the second case is not in the table yet (v1 never sent one); `last_reply_kind`
says what our newest reply was, as the main app now stores it (`processed_messages.reply_kind`).

`previous_replies` = L1 and `messages` = U2, both verbatim. `summary_till_now` is rebuilt by hand from
U1 + L1: `thread_summary_info` keeps only the latest summary (written after U2), not the one C1 had.
Add a case whenever a new follow-up in `processed_messages` gets a wrong reply.

`--suite thread` runs only these. agent-v2: 3/4 pass (the lawyer
follow-up still gets the holding reply again).

## Trusting the judge

Before relying on judge verdicts, hand-grade ~20 replies from a results file (pass/fail per rubric
item, grounded yes/no) and compare: if you and the judge agree on fewer than ~85% of items, fix the
judge prompt (bump `JUDGE_PROMPT_VERSION`) before tuning the agent against it. Judge and agent use the
same model today; a different (stronger) judge model reduces "grading its own homework".

## Comparing versions

Change `AGENT_PROMPT_VERSION` (agent/prompt.py) when you change the agent's instructions; each
version writes its own results file, so v1 and v2 can be compared case by case.
