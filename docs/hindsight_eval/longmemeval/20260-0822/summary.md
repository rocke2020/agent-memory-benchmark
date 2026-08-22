# LongMemEval Hindsight DeepSeek reproduction summary

> **TL;DR:** Our matched 500-question run scored **448/500 (89.6%)**, versus the official Hindsight result's **473/500 (94.6%)**; the gap has two unresolved contributor classes, full-pipeline LLM nondeterminism and the DeepSeek-versus-Gemini model-stack change, while DeepSeek used default thinking at middle-tier `high` effort.

## 1. Headline result

The reproduction is a complete 500-result evaluation over the same questions, gold answers, and metadata, with a score 5.0 percentage points below the official result.

| Result | Correct | Accuracy | Hindsight / answer / judge |
|---|---:|---:|---|
| [Official Hindsight](https://agentmemorybenchmark.ai/run/outputs%2Flongmemeval%2Fhindsight%2Frag%2Fs.json.gz?id=e47becba) | 473/500 | 94.6% | Gemini 2.5 Flash Lite / Gemini 3.1 Pro Preview / Gemini 2.5 Flash Lite |
| Our local DeepSeek run | 448/500 | 89.6% | DeepSeek V4 Flash 0731 / DeepSeek V4 Pro 0813 / DeepSeek V4 Flash 0731 |
| Difference | -25 | -5.0 pp | Not a model-only A/B test |

The public result artifact records the official answer and judge models, while the [official catalog at the frozen baseline](https://github.com/vectorize-io/agent-memory-benchmark/blob/decbb07f4f9899deac28a76293564cf263872652/catalog.json#L81-L87) specifies Gemini 2.5 Flash Lite as embedded Hindsight's extraction model; the artifact does not independently attest that internal runtime field.

Three Hindsight retain batches returned HTTP 500; resume-4 later reran those three complete question units successfully and left the overall score at 448/500.

## 2. Ctx tokens

The report's **Ctx tokens** is the rounded mean of `results[].context_tokens`, where each value is the local `cl100k_base` count of the saved formatted retrieval context.

| Result | Context-token sum | Exact mean | UI value |
|---|---:|---:|---:|
| Official | 21,812,237 | 43,624.474 | 43,624 |
| Ours | 24,812,616 | 49,625.232 | 49,625 |

This is not supplier billing usage: it excludes answer/judge outputs, Hindsight extraction calls, retries, and other prompt fields, so the supplier total of 743,356,436 tokens cannot be split retroactively from the saved evaluation artifact.

## 3. Paired comparison

The local deficit is concentrated in multi-session and temporal-reasoning questions, while preference is one verdict better locally.

| Paired verdict | Questions |
|---|---:|
| Both pass | 434 |
| Official pass, local fail | 39 |
| Official fail, local pass | 14 |
| Both fail | 13 |

| Question type | Official | Ours | Correct delta |
|---|---:|---:|---:|
| knowledge-update | 76/78 | 75/78 | -1 |
| multi-session | 121/133 | 107/133 | -14 |
| single-session-assistant | 56/56 | 54/56 | -2 |
| single-session-preference | 23/30 | 24/30 | +1 |
| single-session-user | 68/70 | 66/70 | -2 |
| temporal-reasoning | 129/133 | 122/133 | -7 |

The discordant split is 39 versus 14; an exact paired McNemar test gives `p = 0.000802`, which describes a systematic artifact difference but does not identify its cause.

Gold answers are not infallible: audited case `6d550036` has a defensible gold of `2` only under a narrow team-leading interpretation, while the natural project-leading reading supports `3` and the declared evidence omits the strongest team-lead session, making the gold ambiguous and arguably wrong; see the [gold-answer case study](../gold-answer-case-studies.md).

## 4. Attribution boundary

The 5-point gap must be analyzed against two unresolved contributor classes: full-pipeline LLM nondeterminism and the model-stack change across Hindsight memory extraction, final answer generation, and answer judging.

Both runs follow the same model-selection principle, so the comparison preserves the role hierarchy even though it changes the model family:

| Role | Official | Ours | Shared selection principle |
|---|---|---|---|
| Hindsight memory extraction | Gemini 2.5 Flash Lite | DeepSeek V4 Flash | Use the lighter, lower-cost model for high-volume internal processing. |
| Final answer generation | Gemini 3.1 Pro Preview | DeepSeek V4 Pro | Use the stronger model for the final synthesis task. |
| Answer judging | Gemini 2.5 Flash Lite | DeepSeek V4 Flash | Use the lighter, lower-cost model for the constrained grading task. |

- All 500 query IDs, questions, gold answers, and metadata match exactly.
- Zero of 500 saved contexts are byte-identical between the two runs.
- The intended model change covers the complete LLM stack, not only the answer model: DeepSeek V4 Flash/Pro replaces Gemini 2.5 Flash Lite/3.1 Pro Preview in the corresponding roles.
- The five-error pilot proves that this full pipeline is nondeterministic, but its error-selected sample cannot estimate the whole-run variance.

The defensible conclusion is therefore a **combined pipeline-performance difference**. Model-family capability and behavior are likely contributors, and nondeterminism is demonstrably present, but the current artifacts cannot apportion the 25 verdicts between them; doing so requires matched repeated runs or the stage-isolation experiment in Section 7.

## 5. DeepSeek runtime versions and thinking mode

The evaluation used the supplier-resolved model versions `deepseek-v4-flash-0731` and `deepseek-v4-pro-0813`; thinking was enabled by default at `high`, which is the middle level in the supplier's three-level `low` / `high` / `max` presentation.

| Role | Requested model | Actual supplier version | Thinking configuration |
|---|---|---|---|
| Hindsight memory extraction | `deepseek-v4-flash` | `deepseek-v4-flash-0731` | Default thinking, effort `high` |
| Final answer generation | `deepseek-v4-pro` | `deepseek-v4-pro-0813` | Default thinking, effort `high` |
| Answer judging | `deepseek-v4-flash` | `deepseek-v4-flash-0731` | Default thinking, effort `high` |

The three effort levels and resolved version suffixes above come from manual inspection of this run's DeepSeek supplier records. In that supplier presentation, `high` is the middle tier and must not be read as maximum reasoning effort.

Although the benchmark caller sent `temperature=0`, it did not explicitly send `thinking` or `reasoning_effort`; [DeepSeek's thinking-mode documentation](https://api-docs.deepseek.com/guides/thinking_mode) states that thinking defaults to enabled, ordinary requests default to effort `high`, and this sampling setting has no effect in thinking mode. It was therefore not an active determinism control for the evaluated calls.

## 6. Five-error nondeterminism pilot

Three fresh full-pipeline replicas establish observable nondeterminism under this runtime configuration, although the error-only sample is too small and selected to estimate whole-benchmark variance.

Each replica used a new create-only Hindsight profile and reran retain → recall → answer → judge for the same five original errors; all 227 expected document IDs, successful-response trace gates, requested/resolved models, usage fields, and daemon shutdowns passed postflight.

| Query | Type | Original | Replica 1 | Replica 2 | Replica 3 |
|---|---|---:|---:|---:|---:|
| `0ddfec37_abs` | abstention | fail | pass | pass | pass |
| `0a995998` | counting | fail | fail | fail | fail |
| `15745da0` | duration | fail | pass | pass | pass |
| `b0479f84` | preference | fail | fail | fail | pass |
| `gpt4_f420262d` | temporal | fail | fail | fail | fail |
| **Replica total** |  | **0/5** | **2/5** | **2/5** | **3/5** |

Every question had three distinct answer, context, exact raw-response, and identity-normalized raw-response hashes; retrieved source-document sets also varied across replicas despite each bank containing the exact expected documents.

All successfully traced DeepSeek calls returned `reasoning_content` with no explicit `thinking` request, confirming that default thinking was active; outputs and verdicts still varied across replicas.

The completed pilot's tracer captured successful responses but could not exclude retries internal to OMB's OpenAI SDK client, whose default was two; this does not invalidate the differing saved contexts, answers, and verdicts, but it means the pilot trace is not a billing-complete attempt ledger, while the reusable tool now disables opaque SDK retries and records paired attempt/terminal events for future runs.

The only mixed benchmark verdict was `b0479f84 = false, false, true`, but an auxiliary DeepSeek Pro re-judge marked all three answers false because each incorrectly discounted the user's prior enjoyment of `Tiger King`; this points to both generation/retrieval variation and a Flash-judge boundary, rather than one clean answer recovery.

DeepSeek Pro also re-judged all five original errors as false and agreed that the clearer replica 1/2 answers for `0ddfec37_abs` and `15745da0` were correct; an independently dispatched Terra review was more generous on the original `15745da0`, illustrating that judge strictness itself is a measurement variable, and its runtime model route was not independently exposed for verification.

## 7. Recommendation

The cost-bounded follow-up reuses the five pilot errors' three completed replicas and adds **55 new DeepSeek full-pipeline runs: 25 additional original errors plus 30 category-matched originally correct controls**. This yields 60 unique questions and 70 rerun observations for an approximate DeepSeek recovery, regression, and verdict-flip estimate relative to the 25-verdict gap, not an exact causal percentage.

Each question retains equal weight: the five pilot questions contribute their per-question mean across three replicas, while every new question contributes one result. The estimate is then weighted back to the eligible error and correct populations by question type; the three pilot replicas are also substituted one at a time to show sensitivity to their uneven repeat count.

The first-stage sample is not assumed sufficient in advance: if its uncertainty or pilot-sensitivity range misses the predeclared precision gate, extend the same deterministic selection with the remaining eligible errors and additional controls in cost-approved batches. The frozen selection, stopping rule, implementation gate, paid-run command, recovery procedure, estimators, and acceptance checks are in the [DeepSeek nondeterminism design and runbook](deepseek-non-deterimination.md).

## 8. Reusable artifacts

The analysis code is provider-agnostic where practical and lives in [`eval_analysis`](../../../../eval_analysis/README.md); compact, redacted evidence for the claims above is published in [`eval_analysis/evidence`](../../../../eval_analysis/evidence/README.md), while the large paid-run artifacts remain local and are identified there by SHA-256.

- `amb_report.py`: inspect, compare, and serve AMB `.json` / `.json.gz` results through the official UI offline.
- `variance_probe.py` and `variance_trace.py`: isolated full-pipeline replicas, completion traces, stable hashes, and fail-closed attestations.
- `hindsight_repair.py` and `hindsight_repair_daemon.py`: frozen incident recovery path.
- `tests/`: offline regression tests for reports, comparison, isolation, traces, shutdown, and merge boundaries.
- [Supplementary run-cohort analysis](resume-1-38-question-cohort.md): provenance and difficulty analysis for the separately supplied resume-1 slice.

The local UI is started with:

```bash
uv run python eval_analysis/amb_report.py serve \
  outputs/longmemeval/hindsight-deepseek/rag/s.json \
  --id 6d550036 \
  --host 127.0.0.1 \
  --port 7979
```
