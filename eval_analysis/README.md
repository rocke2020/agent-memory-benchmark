# LongMemEval Hindsight reproduction analysis

> **TL;DR:** The reproduced run is a valid 500-question result at `448/500 = 89.6%`, and the three previously skipped retain units have now been repaired end to end without changing the score. The official run is `473/500 = 94.6%`; the paired artifacts prove a net 25-verdict gap, but they do not support attributing all five percentage points to the DeepSeek answer model alone.

## Terms

- **Ctx tokens**: `cl100k_base` token count of the formatted context saved in each `results[].context`; it is not provider billing usage.
- **Strict retain-complete**: every expected dataset document is confirmed by Hindsight's `list_documents` API, not merely submitted by the runner.
- **Verdict flip**: the same question is judged correct in one artifact and incorrect in the other.
- **Repair namespace**: a new Hindsight profile and bank prefix used to prevent a repair from deleting or reusing the original profile.

## 1. Offline official report UI

The local server uses the official deployed JavaScript and CSS bundles byte for byte, while the entry HTML removes analytics and remote fonts so it can run offline. It listens on loopback by default and exposes only the selected result plus vendored UI assets.

Run the original reproduction:

```bash
uv run python eval_analysis/amb_report.py serve \
  outputs/longmemeval/hindsight-deepseek/rag/s.json \
  --id 6d550036 \
  --host 127.0.0.1 \
  --port 7979
```

Run the strict three-question repaired copy:

```bash
uv run python eval_analysis/amb_report.py serve \
  eval_analysis/repair-results/resume-4-a/merged/s.json \
  --id 6d550036 \
  --host 127.0.0.1 \
  --port 7979
```

The application layout, calculations, filters, and question drill-down are the official implementation. The offline `index.html` is intentionally not byte-identical because remote analytics/fonts were removed and a `prefers-color-scheme` light/dark override was added. Bundle provenance and hashes are recorded in `vendor/amb-ui/SOURCE.json`.

## 2. What Ctx tokens means

The official report's Ctx value is reproducible exactly from the result artifact. It is the arithmetic mean of the 500 per-question `context_tokens` values, rounded by the UI.

| Artifact | Sum | Exact mean | UI display |
|---|---:|---:|---:|
| Official Hindsight | 21,812,237 | 43,624.474 | 43,624 |
| Original DeepSeek reproduction | 24,812,616 | 49,625.232 | 49,625 |
| Three-question repaired copy | 24,817,681 | 49,635.362 | 49,635 |

Inspect any AMB result:

```bash
uv run python eval_analysis/amb_report.py inspect path/to/result.json --json
```

Ctx tokens is not the DeepSeek supplier's prompt-token counter and does not include system instructions, query text, answer output, judge calls, Hindsight extraction calls, or retries. For this LongMemEval adapter, the actual answer prompt serializes `raw_response` when present; the local artifact's mean serialized retrieval payload is `109,234.830` tokens and the mean full answer user prompt is `109,806.864` tokens. Those are still local tokenizer estimates, not supplier usage.

The aggregate supplier figure `743,356,436` combines OMB and Hindsight API traffic. The current run artifacts do not persist per-response usage, so it cannot be split retroactively into answer, judge, extraction, input, cached input, and output tokens.

## 3. Paired official comparison

The same 500 query IDs, questions, gold answers, and metadata were evaluated, but the saved contexts and all three LLM roles differ. The 5-point score gap is concentrated in multi-session and temporal reasoning.

Reference: [official Hindsight LongMemEval result](https://agentmemorybenchmark.ai/run/outputs%2Flongmemeval%2Fhindsight%2Frag%2Fs.json.gz?id=e47becba).

| Paired verdict | Questions |
|---|---:|
| Both pass | 434 |
| Official pass, local fail | 39 |
| Official fail, local pass | 14 |
| Both fail | 13 |

The discordant counts are `39` versus `14`, producing a net `-25` local verdicts. An exact McNemar test gives `p = 0.000802`; this describes a systematic difference between these two artifacts, not its cause.

| Question type | Official | Local | Correct-count delta |
|---|---:|---:|---:|
| knowledge-update | 76/78 | 75/78 | -1 |
| multi-session | 121/133 | 107/133 | -14 |
| single-session-assistant | 56/56 | 54/56 | -2 |
| single-session-preference | 23/30 | 24/30 | +1 |
| single-session-user | 68/70 | 66/70 | -2 |
| temporal-reasoning | 129/133 | 122/133 | -7 |

Reproduce the paired calculation after downloading the official `.json.gz`:

```bash
uv run python eval_analysis/amb_report.py compare \
  outputs/longmemeval/hindsight-deepseek/rag/s.json \
  path/to/official/s.json.gz \
  --sample-errors 30 \
  --json
```

Per the evaluation assumption, the 38 questions routed through Tencent's DeepSeek-compatible supply chain are pooled with the official DeepSeek supplier; no supplier-quality adjustment is applied.

## 4. What can and cannot explain 94.6% to 89.6%

The artifacts do not isolate “DeepSeek model quality” as the sole cause. Extraction, answer generation, and judging all changed together, while the official artifact removed `raw_response`, preventing an exact raw-retrieval comparison.

Evidence boundaries:

- All 500 inputs match by query ID, query, gold answer, and metadata.
- No saved formatted context is byte-identical between official and local runs, so this is not an answer-model-only A/B test.
- The official and local judge reasons have no exact matches, and the judge models differ.
- Among the 52 local errors, 49 contain every official `answer_session_id` in their saved raw chunks; most failures are therefore not simple omission of the gold session.
- The three repaired questions retained their `True` verdicts but changed context and answer because 21 previously missing distractor sessions were added. This repair is not a clean nondeterminism test because the inputs changed.

Temperature zero does not prove determinism across a multi-model pipeline. The completed pilot below captured `reasoning_content` in every extraction, answer, and judge response even though the caller did not send an explicit `thinking` request; all evaluation calls were sent with temperature zero, yet retrieval, answers, and one verdict varied. This proves temperature zero was insufficient for reproducibility, not that the parameter had no effect.

## 5. Bounded nondeterminism experiment

The completed five-error pilot observed full-pipeline variation, but matched-correct controls are still required before estimating score variance. Rerunning only errors measures selected-error recovery, not how much nondeterminism contributed to the full-run score.

Five-question pilot:

1. `0ddfec37_abs` — abstention / knowledge update
2. `0a995998` — multi-session counting
3. `15745da0` — near-gold strictness / single-session user
4. `b0479f84` — preference
5. `gpt4_f420262d` — temporal reasoning

The `pilot5-20260822-b` experiment ran three fresh create-only full-pipeline replicas, each with all 227 expected document IDs and strict successful-response/model/usage/shutdown gates:

| Query | Original | Replica 1 | Replica 2 | Replica 3 |
|---|---:|---:|---:|---:|
| `0ddfec37_abs` | false | true | true | true |
| `0a995998` | false | false | false | false |
| `15745da0` | false | true | true | true |
| `b0479f84` | false | false | false | true |
| `gpt4_f420262d` | false | false | false | false |
| **Correct** | **0/5** | **2/5** | **2/5** | **3/5** |

Every selected question had three distinct answer, context, exact raw-response, and identity-normalized raw-response hashes. The only mixed benchmark verdict was `b0479f84`; a separate DeepSeek Pro re-judge marked its three replica answers false, so the Flash `false, false, true` sequence includes a judge-boundary effect rather than a confirmed correct recovery.

The completed pilot's trace wrapper recorded successful completion responses but could not observe transport retries internal to OMB's OpenAI SDK client, whose configured default was two. The saved context/answer/verdict variation remains direct evidence, but the trace is not a billing-complete attempt ledger; the current wrapper now disables opaque SDK retries, writes an attempt event before dispatch, writes a sanitized success/failure terminal, and rejects any unmatched or failed attempt.

The next experiment should run each case through four isolation layers, starting with three replicas per layer:

1. Judge only: fixed saved answer and fixed judge prompt.
2. Answer plus judge: fixed context and answer prompt.
3. Recall plus answer plus judge: fixed complete bank.
4. Full pipeline: fresh create-only bank, retain, recall, answer, and judge.

Thirty-error expansion:

```text
0ddfec37_abs a2f3aa27
0a995998 37f165cf 7405e8b1 88432d0a 88432d0a_abs a96c20ee_abs
bf659f65 d851d5ba gpt4_15e38248 gpt4_2ba83207 gpt4_731e37d7 gpt4_ab202e7f
1568498a ceb54acb
09d032c9 1a1907b4 75832dbd 95228167 b0479f84
15745da0 51a45a95 ec81a493
eac54add gpt4_4929293b gpt4_4fc4f797 gpt4_9a159967 gpt4_f420262d gpt4_fe651585
```

For the 30-question phase, add 30 locally correct controls matched by question type, run three full-pipeline replicas, and expand to five replicas only when the estimate remains unstable or needs a tighter interval. Record dataset/result/prompt/model hashes, thinking-request and reasoning-content evidence, provider usage, context hash, answer hash, judge result, and stage timing for every replica.

## 6. Root cause of the three skipped retain batches

The three HTTP 500s had two confirmed upstream causes, not a generic transient network failure. They affected 21 distractor documents but did not explain the net 25-verdict score gap because all three affected questions were already correct locally and officially.

| Query | Missing outer batch | Documents | Confirmed cause |
|---|---:|---:|---|
| `4f54b7c9` | 2 | 8 | Python/PostgreSQL lowercase mismatch for Turkish `İ` |
| `gpt4_2312f94c` | 6 | 5 | Same Unicode entity-resolution bug |
| `gpt4_78cf46a3` | 1 | 8 | Literal tiktoken `endoftext` marker rejected during counting |

The exact upstream fixes are [Unicode entity resolution commit `438ce98b4`](https://github.com/vectorize-io/hindsight/commit/438ce98b4) and [special-token input commit `4bc7013e4`](https://github.com/vectorize-io/hindsight/commit/4bc7013e4). The repair daemon keeps Hindsight at `0.4.17` and applies only these two backports.

## 7. Strict three-question repair result

The repair is now retain-complete for all three question units and leaves the canonical result untouched. Each entire question was rerun because inserting only the missing batch would alter temporal/entity construction order without recomputing recall, answer, and judge.

| Query | Confirmed documents | New verdict |
|---|---:|---:|
| `4f54b7c9` | 45/45 | true |
| `gpt4_2312f94c` | 45/45 | true |
| `gpt4_78cf46a3` | 49/49 | true |

Artifacts:

- Full repair log: `run-artifacts/2028-0819->0822/longmemeval-hindsight-deepseek-resume-4.log`
- Repaired 500-result copy: `eval_analysis/repair-results/resume-4-a/merged/s.json`
- Original SHA-256 before and after: `4e94268f30bda9dedf45853c37b1aa9b4385c6e8a4121f2eb3141e77c1d4fb23`
- Repaired copy SHA-256: `438bd8f81861487391e0eaf72ceb8eb2c7c501ef402f8b5e910a637dad9a90cd`
- Replacement boundary: exactly three changed query objects and 497 object-equal results
- Repaired score: `448/500 = 89.6%`

The repair uses a new profile, create-only banks, fail-closed retain warnings, exact `list_documents` set comparisons, per-query hash-bound retain attestations, and an exclusive merge output. It never calls bank deletion and never overwrites the canonical `s.json`.

## 8. Code map

The reusable report path is provider-agnostic; the repair path is deliberately frozen to this LongMemEval/Hindsight incident.

- `amb_report.py`: validate, summarize, compare, and locally serve AMB `.json` or `.json.gz` files.
- `hindsight_repair.py`: frozen three-question create-only repair and exclusive merge.
- `hindsight_repair_daemon.py`: Hindsight 0.4.17 launcher with the two exact upstream backports and startup self-test.
- `variance_probe.py`: create-only full-pipeline replicas, completeness gates, and cross-replica summaries.
- `variance_trace.py`: completion metadata plus exact and identity-normalized retrieval hashes.
- `deepseek_nondeterminism.py`: frozen 55-question selection, live role preflight, immutable paid-run journal, retain/completion attestations, and population-weighted analysis.
- `run_deepseek_nondeterminism_55.sh`: one-line create-only launcher for selection → live preflight → paid run → offline analysis.
- `evidence/`: committed redacted replay capsule with source hashes and clean-checkout verification commands.
- `tests/`: offline report, comparison, isolation, attestation, and merge regression tests.
- `vendor/amb-ui/`: official report bundles plus the offline entrypoint and system-theme override.

Run all local tests:

```bash
uv run python -m unittest discover -s eval_analysis/tests -v
```
