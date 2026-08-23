# LongMemEval Hindsight DeepSeek reproduction summary

> **TL;DR:** Our matched 500-question run scored **448/500 (89.6%)**, versus the official Hindsight result's **473/500 (94.6%)**; a cost-bounded DeepSeek calibration then observed 9 verdict flips in 55 new questions, an estimated 2.04 pp net score-drift scale, and 4.66 pp of gross verdict instability on the clean frame. The measured pipeline had **five model roles but four distinct configured model names**, but AMB discarded Hindsight's retain-usage responses: this is a complete 500-result accuracy evaluation, not a billing-complete full evaluation, and the exact indexing-token total cannot be recovered from the existing artifacts. Operationally, a one-run difference around 2 pp cannot reliably rank two memory systems, while the 5-point official gap still cannot be causally split between nondeterminism and the DeepSeek-versus-Gemini model-stack change.

## 1. Headline result

The reproduction is a complete 500-result accuracy evaluation over the same questions, gold answers, and metadata, with a score 5.0 percentage points below the official result. It is not a billing-complete token/cost evaluation because the exact retain-extraction usage was not preserved.

| Result | Correct | Accuracy | Extraction / answer / judge LLMs |
|---|---:|---:|---|
| [Official Hindsight](https://agentmemorybenchmark.ai/run/outputs%2Flongmemeval%2Fhindsight%2Frag%2Fs.json.gz?id=e47becba) | 473/500 | 94.6% | Gemini 2.5 Flash Lite / Gemini 3.1 Pro Preview / Gemini 2.5 Flash Lite |
| Our local DeepSeek run | 448/500 | 89.6% | DeepSeek V4 Flash 0731 / DeepSeek V4 Pro 0813 / DeepSeek V4 Flash 0731 |
| Difference | -25 | -5.0 pp | Not a model-only A/B test |

This headline table is LLM-only; Sections 5 and 6 close the inventory over the embedding and reranker roles as well. The public result artifact records the official answer and judge models, while the [official catalog at the frozen baseline](https://github.com/vectorize-io/agent-memory-benchmark/blob/decbb07f4f9899deac28a76293564cf263872652/catalog.json#L81-L87) specifies Gemini 2.5 Flash Lite as embedded Hindsight's extraction model; the artifact does not independently attest that internal runtime field.

Three Hindsight retain batches returned HTTP 500; resume-4 later reran those three complete question units successfully and left the overall score at 448/500.

## 2. Ctx tokens

The report's **Ctx tokens** measures the size of AMB's saved, formatted recall view. It is a retrieval-output-size metric, not the answer model's actual prompt usage and not the end-to-end retain → recall → answer → judge token cost. Retain/indexing usage should therefore be reported beside it, but must not be added directly to it as though both values came from one billing meter.

For each question, AMB obtains the value as follows:

1. Hindsight recall returns ranked facts plus their source chunks. The adapter removes duplicate results, formats each fact with its type and temporal/chunk metadata, and inlines a source chunk only on its first occurrence.
2. RAG formats those results as `## Memory 1\n...`, `## Memory 2\n...`, and so on, separated by blank lines. This string is saved as `results[].context`.
3. The runner encodes that exact string with the local `tiktoken` `cl100k_base` encoding and saves the encoded length as `results[].context_tokens`. This counter is independent of the DeepSeek, Gemini, BGE, and MiniLM tokenizers.
4. The report takes the arithmetic mean over all non-null per-question values; the UI rounds that mean to the nearest integer.

In formula form, for question `q`:

```text
formatted_context_q = join("\n\n", "## Memory {i}\n" + formatted_recall_item_i)
context_tokens_q    = len(cl100k_base.encode(formatted_context_q))
Ctx tokens          = round(sum(context_tokens_q) / number_of_questions)
```

| Result | Context-token sum | Exact mean | UI value |
|---|---:|---:|---:|
| Official | 21,812,237 | 43,624.474 | 43,624 |
| Ours | 24,812,616 | 49,625.232 | 49,625 |

There is a LongMemEval-specific caveat: when Hindsight supplies `raw_response`, the answer prompt serializes that complete recall response as JSON instead of using the formatted string counted above. Consequently, **Ctx tokens does not equal the context actually sent to the answer LLM in this run**. Separate local analysis of the saved artifact estimated a mean of 109,234.830 `cl100k_base` tokens for the serialized recall payload and 109,806.864 for the complete answer user prompt, versus the displayed Ctx value of 49,625. These remain local estimates, not supplier usage.

## 3. Indexing tokens

The exact retain-extraction token total for the canonical 500-question run is unavailable. Discarding this usage was a serious instrumentation mistake and a cost-accounting loss in the preserved artifacts, but it does not invalidate the **448/500** accuracy result: the missing evidence concerns token usage, not the saved answers, judge verdicts, or repaired final result set.

Here **indexing tokens** means the supplier-metered input and output tokens used by Hindsight's extraction LLM during retain. Local BGE embedding is also indexing work, but it has no supplier/API token usage and its local tokenizer throughput was not captured.

### What was preserved

AMB waited for synchronous retain to finish, and Hindsight returned extraction `input_tokens`, `output_tokens`, and `total_tokens`. However, AMB discarded the returned response instead of saving its usage; the canonical result contains 500 question results but zero retain/provider-usage fields.

| Evidence | Input tokens | Output tokens | Total tokens | Proof boundary |
|---|---:|---:|---:|---|
| Canonical 500-result artifact | Unavailable | Unavailable | Unavailable | No retain response or usage field was saved. |
| Canonical initial/resume daemon windows | 341,888,434 | 254,748,775 | 596,637,209 | Exact subtotal of the logged slow extraction calls only; not the complete total. |
| Later isolated four-question nondeterminism repair | 3,255,054 | 2,042,499 | 5,297,553 | Exact, hash-attested supplier usage for that separate repair only; it cannot replace or be extrapolated to the canonical run. |

The five canonical invocation windows contain **96,266** logged slow `retain_extract_facts` calls, while the same windows report at least **118,871** base extraction chunks. Hindsight logged usage only for calls slower than 10 seconds, so at least **22,605** base calls have no token record; automatic splitting or retries can make the true gap larger. The 596,637,209-token value also includes logged failed or retried work within those windows and cannot be reduced to an exact, deduplicated total for the final 500 accepted question units.

The later isolated repair proves that complete response-level collection is possible: all 923 extraction attempts had matching successful responses, supplier usage, and SDK retries disabled. That evidence applies only to its four questions.

The supported reporting boundary is therefore:

| Evaluation claim | Status |
|---|---|
| Accuracy and verdict evaluation | Complete: 500 results, 448 correct. |
| Saved formatted-context size | Complete under the Ctx-tokens definition in Section 2. |
| Exact retain-extraction tokens | Unavailable. |
| Full external-LLM token/cost ledger | Incomplete; the aggregate supplier records cannot be split reliably by retain, answer, judge, and retry. |

### Required future ledger

Indexing and query-time work must be accounted for as separate lifecycle stages:

| Stage | Token-bearing work | What this run preserved |
|---|---|---|
| Retain/indexing | DeepSeek Flash reads each retained session plus the extraction instructions and emits structured facts. Hindsight's synchronous retain response exposes extraction `input_tokens`, `output_tokens`, and `total_tokens`. | **AMB discarded the returned retain response, including its usage.** Therefore the exact retain-extraction input, output, and total tokens are unavailable from the result artifact. |
| Retain/indexing | Local BGE embeds the extracted facts. | No supplier/API token applies; local tokenizer throughput was not captured. |
| Recall | Local BGE embeds the query and local MiniLM reranks candidates. | No supplier/API token applies; local tokenizer throughput was not captured. |
| Answer and judge | DeepSeek Pro consumes the actual answer prompt and emits the answer; DeepSeek Flash consumes the judge prompt and emits the verdict. | Their per-call usage was not saved in the 500-result artifact. |

For a future billing-complete run, the external-LLM ledger should report retain extraction, answer, and judge input/output tokens separately, including every retry attempt. Here `answer_total_tokens` explicitly includes both the actual answer-prompt input and the generated answer output:

```text
retain_extraction_total_tokens = retain_extraction_input_tokens + retain_extraction_output_tokens
answer_total_tokens            = answer_input_tokens + answer_output_tokens
judge_total_tokens             = judge_input_tokens + judge_output_tokens

external_LLM_total_tokens =
    retain_extraction_total_tokens + answer_total_tokens + judge_total_tokens
```

This equation defines the desired future ledger; **the current AMB artifact cannot evaluate it**. The Hindsight adapter ignores each synchronous retain response instead of persisting its extraction usage, and the AMB answer/judge adapters likewise do not persist their response usage. Do **not** add `Ctx tokens` again: it is a local view-size proxy whose text is already represented inside `answer_input_tokens`, so doing so would mix tokenizers and double-count part of the answer stage.

For a reused index, keep the one-time retain total as the primary measured value. If retrospective amortization is useful, report `retain_extraction_total_tokens / observed_recall_invocation_count` with the observation window and index or bank boundary. Count every recall attempt against that index, including zero-result recalls; if no recall occurred, report the amortized value as not applicable. Future recall volume is usually too uncertain for one defensible `expected_recall_count`, so planning should show multiple recall-volume scenarios rather than present one forecast as measured usage.

The recorded all-original-supplier projection of 743,356,436 tokens combines retain extraction, answer, judge, and their supplier-visible attempts, but cannot be split retroactively by role from the saved artifacts; see the [model and token usage summary](model-token-summary.md) for the measured segments, local-model accounting, and proof boundary.

## 4. Paired comparison

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

## 5. Attribution boundary

The 5-point gap must be analyzed against two unresolved contributor classes: full-pipeline LLM nondeterminism and the three-role Gemini-to-DeepSeek LLM substitution. The complete retrieval-and-evaluation path has five model roles; the two local retrieval roles have the same nominal Hindsight v0.4.17 defaults in both columns, but only our run captured runtime evidence for them.

| Model role | Official Hindsight reference | Our DeepSeek run | Evidence boundary |
|---|---|---|---|
| Hindsight memory extraction | `gemini-2.5-flash-lite` | `deepseek-v4-flash` | Official catalog configuration versus our configured alias and daemon extraction log; the official result artifact records neither extraction runtime nor its resolved model version. |
| Hindsight embedding | v0.4.17 source default: local `BAAI/bge-small-en-v1.5` | local `BAAI/bge-small-en-v1.5`, 384 dimensions | The official result does not attest this default. Our daemon startup log attests provider, model name, and dimension, but not the exact Hugging Face weight revision. |
| Hindsight cross-encoder reranking | v0.4.17 source default: local `cross-encoder/ms-marco-MiniLM-L-6-v2` | local `cross-encoder/ms-marco-MiniLM-L-6-v2` | The official result does not attest this default. Our daemon startup log attests provider and model name, but not the exact Hugging Face weight revision. |
| Final answer generation | `gemini-3.1-pro-preview` | `deepseek-v4-pro` | Both result artifacts record the configured answer-model ID; the DeepSeek supplier's resolved suffix was inspected separately. |
| Answer judging | `gemini-2.5-flash-lite` | `deepseek-v4-flash` | Both result artifacts record the configured judge-model ID; the DeepSeek supplier's resolved suffix was inspected separately. |

- All 500 query IDs, questions, gold answers, and metadata match exactly.
- Zero of 500 saved contexts are byte-identical between the two runs.
- The intended model change covers the complete three-role LLM stack, not only the answer model: DeepSeek V4 Flash/Pro replaces Gemini 2.5 Flash Lite/3.1 Pro Preview in the corresponding roles.
- The five-error pilot proves that this full pipeline is nondeterministic, but its error-selected sample cannot estimate the whole-run variance.

There is no sixth executed model role hidden behind the Hindsight API configuration. This evaluation used AMB `rag`, which calls one Hindsight recall per question and then the answer model; it did not call Hindsight `reflect` or the `agentic-rag` planner. The benchmark also created banks with observations disabled, so Hindsight's consolidation LLM path did not execute. The captured daemon log's only Hindsight LLM call scope is `retain_extract_facts`. BM25, graph/temporal retrieval, Reciprocal Rank Fusion, date parsing, and the local `cl100k_base` context-token counter are algorithms or tokenization utilities, not additional model roles. The [AMB response-mode guide](../../general/response-modes.md) explains the `rag`, `agentic-rag`, and `agent` call boundaries.

The defensible conclusion is therefore a **combined pipeline-performance difference**. Model-family capability and behavior are likely contributors, and nondeterminism is demonstrably present, but the current artifacts cannot apportion the 25 verdicts between them; doing so requires matched repeated runs or the stage-isolation experiment in Section 8.

## 6. Complete model inventory and runtime versions

The evaluation used five logical roles and four distinct configured model names. DeepSeek exposes supplier-resolved versions for the three external LLM roles, while the two local SentenceTransformers roles are attested to the repository/model-name level only because their exact cached Hugging Face snapshot revisions were not preserved.

| Execution stage and role | Configured or requested identity | Runtime resolution | Attestation boundary |
|---|---|---|---|
| Retain: Hindsight memory extraction | `openai` / `deepseek-v4-flash` | `deepseek-v4-flash-0731` | The `.env` selector and daemon log attest the requested alias and `retain_extract_facts` calls; the resolved suffix comes from manual inspection of supplier records. |
| Retain and recall: Hindsight embedding | local / `BAAI/bge-small-en-v1.5` | 384-dimensional local model; exact weight revision unavailable | The `.env` pin, repair preflight, and original daemon startup log attest provider, model name, and dimension. This role has no supplier model-version response. |
| Recall: Hindsight cross-encoder reranking | local / `cross-encoder/ms-marco-MiniLM-L-6-v2` | local model; exact weight revision unavailable | The `.env` pin, repair preflight, and original daemon startup log attest provider and model name. This role has no supplier model-version response. |
| Answer: AMB final answer generation | `openai` / `deepseek-v4-pro` | `deepseek-v4-pro-0813` | The 500-result JSON records `openai:deepseek-v4-pro`; the resolved suffix comes from manual inspection of supplier records. |
| Judge: AMB answer judging | `openai` / `deepseek-v4-flash` | `deepseek-v4-flash-0731` | The 500-result JSON records `openai:deepseek-v4-flash`; the resolved suffix comes from manual inspection of supplier records. |

The three DeepSeek LLM roles used default thinking at effort `high`, which is the middle level in the supplier's three-level `low` / `high` / `max` presentation. The effort levels and resolved version suffixes above come from manual inspection of this run's DeepSeek supplier records; they are not fields in the AMB result JSON. Local embedding and reranking do not have a DeepSeek thinking mode.

Although the benchmark caller sent `temperature=0` for the external LLM calls, it did not explicitly send `thinking` or `reasoning_effort`; [DeepSeek's thinking-mode documentation](https://api-docs.deepseek.com/guides/thinking_mode) states that thinking defaults to enabled, ordinary requests default to effort `high`, and this sampling setting has no effect in thinking mode. It was therefore not an active determinism control for the evaluated calls.

## 7. Five-error nondeterminism pilot

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

## 8. DeepSeek nondeterminism result and recommendation

The completed cost-bounded follow-up combines the five pilot errors' three replicas with **55 new DeepSeek full-pipeline runs: 25 additional original errors plus 30 category-matched originally correct controls**. The new 55 produced 8 recoveries and 1 regression, or 9 raw flips (16.4%) and a signed gain of 7 verdicts.

After averaging replicas within each pilot question and weighting question-type rates back to the 458-question clean frame, the estimate is **+9.325 net verdicts (2.04 pp)** and **21.325 gross unstable verdicts (4.66 pp)**. These are respectively `0.373` and `0.853` times the 25-verdict official gap as scale comparisons, not causal shares of that gap; the pilot-replica sensitivity range remains material at 8.658–10.658 net and 20.658–22.658 gross verdicts.

The operational conclusion is clear enough to stop: as an engineering shorthand, Round 1 and Round 2 of a full DeepSeek evaluation may differ by roughly 2 percentage points in final accuracy. This does not mean every pair will differ by exactly 2 pp, and it is not a standard deviation or confidence bound; it means that a one-run gap around 2 pp should remain unresolved. A single run is still useful for directional screening, but any comparison that could turn on such a small gap requires replicas or explicit uncertainty. No Stage 2 spend is planned for this trust-policy decision; the full proof boundary, Repair-4 provenance, weighting method, sensitivity analysis, and future optional extension are in the [DeepSeek nondeterminism result and runbook](deepseek-non-deterimination.md).

## 9. Reusable artifacts

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
