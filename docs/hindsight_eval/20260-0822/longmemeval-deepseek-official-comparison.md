# LongMemEval Hindsight DeepSeek reproduction: official comparison

> **TL;DR:** Our matched 500-question run scored **448/500 (89.6%)**, versus the official Hindsight result's **473/500 (94.6%)**; the artifacts prove a 25-verdict gap and the 5-question pilot proves that temperature 0 does not guarantee full-pipeline determinism, but neither result isolates DeepSeek model quality as the sole cause.

## 1. Headline result

The reproduction is a complete 500-result evaluation over the same questions, gold answers, and metadata, with a score 5.0 percentage points below the official result.

| Result | Correct | Accuracy | Answer / judge |
|---|---:|---:|---|
| [Official Hindsight](https://agentmemorybenchmark.ai/run/outputs%2Flongmemeval%2Fhindsight%2Frag%2Fs.json.gz?id=e47becba) | 473/500 | 94.6% | Gemini 3.1 Pro Preview / Gemini 2.5 Flash Lite |
| Our local DeepSeek run | 448/500 | 89.6% | DeepSeek V4 Pro / DeepSeek V4 Flash |
| Difference | -25 | -5.0 pp | Not a model-only A/B test |

The 38 questions sent through Tencent's DeepSeek-compatible supply chain are pooled with the official DeepSeek supplier, as requested; this is an evaluation assumption rather than a verified supplier-quality equivalence.

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

## 4. Attribution boundary

The 5-point gap cannot be assigned solely to the DeepSeek answer model because extraction, retrieval outputs, answer generation, and judging changed together.

- All 500 query IDs, questions, gold answers, and metadata match exactly.
- Zero of 500 saved contexts are byte-identical between the two runs.
- The official and local answer and judge models differ, and the extraction role also differs.

The defensible conclusion is therefore a **combined pipeline-performance difference**, with model quality likely relevant but not causally isolated.

## 5. Five-error nondeterminism pilot

Three fresh full-pipeline replicas establish observable nondeterminism at temperature 0, although the error-only sample is too small and selected to estimate whole-benchmark variance.

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

All successfully traced DeepSeek calls returned `reasoning_content`, while extraction, answer, and judge calls were sent with temperature 0 and no explicit `thinking` request; this proves temperature 0 was insufficient for reproducibility, not that the parameter had no effect.

The completed pilot's tracer captured successful responses but could not exclude retries internal to OMB's OpenAI SDK client, whose default was two; this does not invalidate the differing saved contexts, answers, and verdicts, but it means the pilot trace is not a billing-complete attempt ledger, while the reusable tool now disables opaque SDK retries and records paired attempt/terminal events for future runs.

The only mixed benchmark verdict was `b0479f84 = false, false, true`, but an auxiliary DeepSeek Pro re-judge marked all three answers false because each incorrectly discounted the user's prior enjoyment of `Tiger King`; this points to both generation/retrieval variation and a Flash-judge boundary, rather than one clean answer recovery.

DeepSeek Pro also re-judged all five original errors as false and agreed that the clearer replica 1/2 answers for `0ddfec37_abs` and `15745da0` were correct; an independently dispatched Terra review was more generous on the original `15745da0`, illustrating that judge strictness itself is a measurement variable, and its runtime model route was not independently exposed for verification.

## 6. Recommendation

The next useful experiment is **30 original errors plus 30 category-matched originally correct controls**, with three replicas and stage isolation; rerunning 30 errors alone would measure selected-error recovery, not the contribution of nondeterminism to the 5-point score gap.

Run these layers in order:

1. Judge only with fixed prompt and answer.
2. Answer plus judge with fixed saved context.
3. Recall plus answer plus judge with a fixed complete bank.
4. Full pipeline with a fresh create-only bank per replica.

Report per-layer verdict flips and context/answer/judge hashes; expand to five replicas only if the three-replica estimates remain unstable or a tighter interval is needed.

## 7. Reusable artifacts

The analysis code is provider-agnostic where practical and lives in [`eval_analysis`](../../../eval_analysis/README.md); compact, redacted evidence for the claims above is published in [`eval_analysis/evidence`](../../../eval_analysis/evidence/README.md), while the large paid-run artifacts remain local and are identified there by SHA-256.

- `amb_report.py`: inspect, compare, and serve AMB `.json` / `.json.gz` results through the official UI offline.
- `variance_probe.py` and `variance_trace.py`: isolated full-pipeline replicas, completion traces, stable hashes, and fail-closed attestations.
- `hindsight_repair.py` and `hindsight_repair_daemon.py`: frozen incident recovery path.
- `tests/`: offline regression tests for reports, comparison, isolation, traces, shutdown, and merge boundaries.

The local UI is started with:

```bash
uv run python eval_analysis/amb_report.py serve \
  outputs/longmemeval/hindsight-deepseek/rag/s.json \
  --id 6d550036 \
  --host 127.0.0.1 \
  --port 7979
```
