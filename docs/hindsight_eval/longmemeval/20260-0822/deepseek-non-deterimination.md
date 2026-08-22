# DeepSeek nondeterminism study: design and runbook

**Terms:** The **candidate** is our frozen 448/500 DeepSeek result. The **pilot five** are five candidate errors already rerun three times. The **new 55** are 25 additional errors plus 30 originally correct controls that will each run once. A **recovery** is original fail → rerun pass; a **regression** is original pass → rerun fail. A **create-only bank** is a new Hindsight bank that must not overwrite or delete earlier benchmark state.

> **TL;DR:** Reuse the pilot five's 15 completed question-runs and start with the frozen new 55 after 18:00 Asia/Shanghai on 2026-08-22. The operational decision is whether one DeepSeek run is trustworthy for future in-house memory comparisons or whether those evaluations require replicas and uncertainty reporting; extend the deterministic sample only when Stage 1 cannot settle that decision and the user approves more cost.

## 1. Objective and proof boundary

This study decides whether future evaluations of our own agent memory can trust one DeepSeek score or must require repeated runs and uncertainty reporting. It estimates how unstable the DeepSeek Hindsight pipeline is at the observed configuration and whether that instability is small or large relative to 25 verdicts; it does not rerun Gemini or claim an exact decomposition of the 5.0-point gap.

The measured path remains:

```text
Hindsight retain/extraction → recall → DeepSeek answer → DeepSeek judge
```

The study reports two different quantities:

- **Net drift:** estimated recoveries minus estimated regressions, expressed in verdicts.
- **Gross instability:** estimated recoveries plus estimated regressions, expressed in verdicts.

Neither quantity is a causal percentage. Net drift can be negative, and gross instability can exceed 25 because flips in opposite directions cancel in the aggregate score.

## 2. Frozen evidence

The run must stop before paid ingestion if any frozen input differs from this table.

| Evidence | Required value |
|---|---|
| Candidate | `outputs/longmemeval/hindsight-deepseek/rag/s.json` |
| Candidate SHA-256 | `4e94268f30bda9dedf45853c37b1aa9b4385c6e8a4121f2eb3141e77c1d4fb23` |
| Official reference | `outputs/longmemeval/hindsight/rag/s.json.gz` |
| Dataset SHA-256 | `d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442` |
| Pilot evidence | `eval_analysis/evidence/pilot5-20260822-b.json` |
| Pilot evidence SHA-256 | `771ccdbf5b9cbde7f2a587bf60d4123f2e0909fe0ea01ebe4fdf948ec2514ba3` |
| Requested Hindsight/judge model | `deepseek-v4-flash` |
| Requested answer model | `deepseek-v4-pro` |
| Supplier versions | Flash `0731`; Pro `0813` |
| Hindsight API | `0.4.17` |

The five pilot verdict sequences are fixed sensitivity evidence:

| Query | Replica 1 | Replica 2 | Replica 3 | Recovery mean |
|---|---:|---:|---:|---:|
| `0ddfec37_abs` | pass | pass | pass | 1.000 |
| `0a995998` | fail | fail | fail | 0.000 |
| `15745da0` | pass | pass | pass | 1.000 |
| `b0479f84` | fail | fail | pass | 0.333 |
| `gpt4_f420262d` | fail | fail | fail | 0.000 |

## 3. Analysis frame

The clean analysis frame excludes known provenance and annotation confounders while preserving all 25 observed official-versus-local verdicts.

| Frame | Questions | Local correct | Official correct | Gap |
|---|---:|---:|---:|---:|
| Full benchmark | 500 | 448 | 473 | 25 |
| Clean analysis frame | 458 | 415 | 440 | 25 |

The excluded questions are:

- 38 resume-1 questions run through a different DeepSeek-compatible supply chain; their local and official cohorts both scored 30/38, so they contribute zero net verdicts to the gap.
- `4f54b7c9`, `gpt4_2312f94c`, and `gpt4_78cf46a3`, whose original retain histories required resume-4 repair; all three are paired passes and contribute zero gap.
- `6d550036`, whose gold answer is ambiguous; it is a paired failure and contributes zero gap.

The eligible population contains 43 original local errors and 415 original local correct answers:

| Question type | Eligible errors | Eligible correct | Error sample | Control sample |
|---|---:|---:|---:|---:|
| knowledge-update | 3 | 75 | 2 | 2 |
| multi-session | 21 | 90 | 15 | 15 |
| single-session-assistant | 2 | 54 | 1 | 1 |
| single-session-preference | 2 | 10 | 1 | 1 |
| single-session-user | 4 | 66 | 3 | 3 |
| temporal-reasoning | 11 | 120 | 8 | 8 |
| **Total** | **43** | **415** | **30** | **30** |

The sample quotas use largest-remainder proportional allocation over the 43 eligible errors. Controls use the same question-type quotas.

## 4. Frozen selection

The selection is deterministic and fixed before the paid run, preventing outcome-driven cherry-picking.

The study ID is `deepseek-nondeterminism-20260822-55a`. Within each question type and cohort, candidates are ordered by:

```text
SHA256(study_id + "\0" + cohort + "\0" + query_id), then query_id
```

The five pilot IDs are forced into the 30-error sample. The remaining 25 errors and all 30 controls take the first IDs under the hash order after applying Section 3 exclusions. A control whose `_abs`-stripped ID equals a selected error's ID is rejected.

Expected canonical selection-manifest SHA-256: `572a3a774c8e4734fb017f882c031f8299c6db08b1f623e76946c7b5eaa3a482`.

Expected sorted new-55 ID-set SHA-256: `ff003d654962f23729d70b64106e812babdd3eb5504ffa4369dd567b53589653`.

### 4.1 Pilot five: reuse, do not rerun

```text
0ddfec37_abs
0a995998
15745da0
b0479f84
gpt4_f420262d
```

### 4.2 New 25 errors

```text
5c40ec5b
9ee3ecd6
09ba9854_abs
a11281a2
3c1045c8
3a704032
bb7c3b45
gpt4_2ba83207
d851d5ba
46a3abf7
51c32626
7024f17c
gpt4_15e38248
88432d0a
gpt4_7fce9456
1568498a
ec81a493
726462e0
gpt4_4fc4f797
370a8ff4
71017277
eac54add
9a707b81
gpt4_f420262c
gpt4_7f6b06db
```

### 4.3 New 30 controls

```text
f685340e
2698e78f
a9f6b44c
d682f1a2
21d02d0d
87f22b4a
gpt4_a56e767c
dd2973ad
a96c20ee
60bf93ed
bc149d6b
c4a1ceb8
ba358f49_abs
6456829e_abs
f35224e0
a08a253f
e6041065
18dcd5a5
d6233ab6
95bcc1c8
e01b8e2f
a82c026e
cc6d1ec1
gpt4_b5700ca0
6613b389
gpt4_74aed68e
gpt4_d9af6064
gpt4_8279ba02
993da5e2
6e984301
```

## 5. Required implementation gate

The existing `eval_analysis/variance_probe.py` cannot run this study safely: it hardcodes the pilot five, requires every selected query to be a local error that the official run passed, and rejects a single replica. Paid execution must wait for a tested manifest-driven study command.

Implement `eval_analysis/deepseek_nondeterminism.py` with four commands:

```text
select     build the frozen manifest and reject any ID/hash/quota drift
preflight  validate files, runtime roles, profiles, document sets, and live models
run        execute exactly the new 55 once in create-only isolated banks
analyze    combine the new 55 with pilot evidence and write the estimates
```

The implementation should reuse the existing selected-dataset, completion tracing, retry disabling, document attestation, daemon shutdown, and exclusive-write helpers rather than create a second execution path.

Required offline tests:

1. The selector reproduces both expected hashes and all quotas.
2. Changing one ID, status, type, exclusion, or source hash fails closed.
3. The runner accepts one manifest-driven run while the pilot tool still requires at least two replicas.
4. The runner produces exactly 55 unique terminal results and never loads an unselected question's documents.
5. Every completion attempt has one terminal event and `sdk_max_retries == 0`.
6. A partial run cannot overwrite the candidate, pilot evidence, selection manifest, or completed output.
7. Analysis gives each of 60 questions equal weight and reproduces all three pilot-substitution sensitivity estimates.

## 6. Execution runbook

The paid run starts only after the offline implementation gate passes and the wall clock is after 18:00 Asia/Shanghai on 2026-08-22.

### Step 1: Build and verify the selection without network calls

```bash
uv run python eval_analysis/deepseek_nondeterminism.py select \
  --study-id deepseek-nondeterminism-20260822-55a \
  --candidate outputs/longmemeval/hindsight-deepseek/rag/s.json \
  --reference outputs/longmemeval/hindsight/rag/s.json.gz \
  --pilot-summary eval_analysis/evidence/pilot5-20260822-b.json \
  --resume-log 'run-artifacts/2028-0819->0822/longmemeval-hindsight-deepseek-resume-1.log'
```

Success requires both expected selection hashes and `new_errors=25 controls=30 new_runs=55`.

### Step 2: Run the fail-closed preflight

```bash
uv run python eval_analysis/deepseek_nondeterminism.py preflight \
  --study-id deepseek-nondeterminism-20260822-55a \
  --live
```

The preflight must load the real `.env` with secrets redacted, resolve all Hindsight/answer/judge roles, verify the candidate and dataset hashes, prove the output/profile namespace is absent, and make minimal live calls to both DeepSeek models. The API response and trace must match the requested aliases; before the paid run, a separate manual supplier-console gate must confirm Flash 0731, Pro 0813, and default thinking effort `high`, because those fields are not all exposed by the saved API response.

### Step 3: Execute and capture the new 55

```bash
uv run python eval_analysis/deepseek_nondeterminism.py run \
  --study-id deepseek-nondeterminism-20260822-55a \
  2>&1 | tee 'run-artifacts/2028-0819->0822/longmemeval-hindsight-deepseek-nondeterminism-55.log'
```

The command must use one new Hindsight profile containing 55 isolated question banks, retain synchronously, checkpoint after each terminal question, disable opaque OpenAI SDK retries, and stop the daemon in `finally`.

### Step 4: Analyze without further model calls

```bash
uv run python eval_analysis/deepseek_nondeterminism.py analyze \
  --study-id deepseek-nondeterminism-20260822-55a
```

The analyzer writes a create-only JSON report and a concise Markdown result beside it. It must not mutate the candidate 500-result artifact.

## 7. Failure and resume protocol

A partial or failed paid run preserves every successful question and repairs only missing or invalid units in a new namespace.

- Never delete a Hindsight bank or profile.
- Never overwrite the candidate, pilot summary, selection manifest, partial result, or final result.
- On HTTP 500, completion failure, document mismatch, model drift, or daemon shutdown failure, stop and retain the log plus partial artifacts.
- Derive completed units from valid terminal result records bound to the frozen manifest, not from process exit or non-empty output.
- Resume only missing/invalid query IDs with a new recovery suffix such as `-repair1`.
- Merge into a new create-only result after proving exactly 55 selected IDs, with repaired records replacing only their failed counterparts.

## 8. Analysis method

The estimator averages repeats within a question first, then averages questions within each type, so the pilot five never become 15 independent questions.

For question type `t`:

```text
r_t = mean recovery probability across sampled original errors of type t
g_t = mean regression indicator across sampled original correct controls of type t
net_t = E_t × r_t - C_t × g_t
gross_t = E_t × r_t + C_t × g_t
```

`E_t` and `C_t` are the eligible population counts from Section 3. For a pilot question, recovery probability is its pass count divided by three; for a new error or control, the observation is binary.

Report:

1. Raw new-55 recoveries, regressions, flips, and net change.
2. Question-type rates `r_t` and `g_t`.
3. Combined `net = Σ net_t` and `gross = Σ gross_t` over the 458-question frame.
4. `net / 25` and `gross / 25` only as signed and gross scale comparisons, not causal shares.
5. Three sensitivity estimates that substitute pilot Replica 1, 2, or 3 for the pilot means while keeping all new-55 outcomes fixed.

One new observation per non-pilot question cannot support a precise confidence interval. Report exact observed counts, the three sensitivity values, and the arithmetic inputs instead of a false precision claim.

## 9. Sample adequacy and trust decision

The initial 60 unique questions are sufficient to produce a first DeepSeek noise estimate and can disprove single-run trustworthiness, but they may be insufficient to certify that one run is stable.

- The 30 sampled errors estimate recovery; the 30 controls estimate regression.
- At the worst-case rate near 50%, a simple 30-observation binomial proportion has a 95% uncertainty scale of roughly ±18 percentage points before weighting.
- Per-type sample sizes range from 1 to 15, so individual type estimates are substantially less stable than the pooled direction.
- The pilot's repeated observations improve the within-question estimate for five errors only; they do not increase the number of represented questions.
- Deterministic stratification and population weighting reduce avoidable selection distortion but cannot replace more independent questions or replicas.

After each stage, calculate the three pilot-substitution estimates plus the equal-question combined estimate and a 95% uncertainty interval using a predeclared question-type-stratified method with a fixed random seed. The analyzer must not select a friendlier method after seeing outcomes.

Use the upper uncertainty bound on gross instability as the estimated **single-run noise floor**: a future memory-score difference smaller than that bound is not trustworthy from one DeepSeek run alone. For the current comparison, evaluate that bound against 25 verdicts:

1. **Clearly untrustworthy:** the lower bound is already large enough to invalidate the score difference being evaluated; stop because more samples are unnecessary for that decision.
2. **Decision-sufficient:** the upper bound is below the score difference being evaluated; stop, while retaining the bound as the minimum effect size that one run can support.
3. **Inconclusive:** the interval crosses the decision threshold; either expand under Section 10 or stop conservatively and require replicas for important comparisons.

Even zero regressions among 30 controls would not certify stability: the usual zero-event 95% upper-bound scale is about 10%, which is large when extrapolated over 415 eligible correct questions. Stage 1 therefore provides an approximation in all cases, but it proves trustworthiness only when the uncertainty bound is sufficiently separated from the effect size of interest.

## 10. Adaptive extension

Stage 1 is worth running as calibration for future agent-memory evaluations, but an extension is worth funding only when greater precision could change the evaluation policy.

The full 500-question run was approximately CNY 1,600, so a simple linear estimate puts Stage 1's 55 new questions near CNY 176. This is planning scale, not a quote: question lengths, extraction volume, retries, and off-peak pricing can change actual cost. Record the supplier dashboard before and after every paid stage.

After Stage 1, choose exactly one cost outcome:

1. **Clearly untrustworthy or decision-sufficient:** stop and publish the approximation plus its noise floor.
2. **Inconclusive, but cost control matters more than certifying one-run stability:** stop and label the result `cost-limited`; future important comparisons require replicas or uncertainty reporting.
3. **Inconclusive, and permitting future single-run evaluation would materially reduce ongoing cost:** offer the next deterministic batch and wait for explicit cost approval.

When extension is justified, enlarge the sample by continuing the same frozen hash ranking rather than choosing questions from observed answers.

| Stage | Newly paid questions | Cumulative unique questions | Decision |
|---|---:|---:|---|
| Stage 1 | 25 errors + 30 controls = 55 | 60, including pilot five | Run after the initial cost gate. |
| Stage 2 | remaining 13 eligible errors + next 30 controls = 43 | 103 | Run only under outcome 3 and after explicit cost approval. |
| Stage 3+ | next 30 controls per stage | 133, 163, ... | Continue only while the trust decision remains inconclusive and the user approves each batch. |

Stage 2 exhausts all 43 eligible errors, so later stages improve only the regression estimate over the much larger 415-question correct population. Never rerun a completed extension question merely to balance counts; repeated-question evidence is analyzed as a cluster, not as new independent questions.

Stop when the trust decision is clear, the eligible control population is exhausted, or the user declines the next cost gate. If budget stops the sequence first, report the achieved interval and label the result `cost-limited`, not `sample-sufficient`.

## 11. Acceptance gates

The study is publishable as an approximation only when every gate below passes.

- Selection manifest matches both frozen SHA-256 values and contains 5 pilot, 25 new error, and 30 control IDs with no overlap.
- The new run contains exactly 55 unique terminal results: 25 baseline failures and 30 baseline passes.
- All 2,654 expected document IDs are present across the 55 isolated banks; with batch size 8, all 355 expected retain batches complete.
- Completion traces contain at least 55 answer and 55 judge successes, no failed terminal events, and no opaque SDK retry allowance.
- Requested models, resolved models, supplier versions, thinking behavior, prompts, dataset, and Hindsight configuration match the frozen run.
- Every result has context, answer, judge verdict, usage, request ID, and stable hashes for context, answer, raw response, semantic raw response, source-document set, answer prompt, and judge prompt.
- The Hindsight daemon is stopped after success or failure.
- Candidate and pilot evidence hashes are unchanged after the run.
- Analysis exposes raw counts, equal-question combined estimates, three pilot sensitivity estimates, and the no-confidence-interval limitation.

## 12. Outputs and ownership

All new state is create-only and study-scoped, leaving the paid 500-question result unchanged.

Expected paths:

```text
eval_analysis/nondeterminism-results/deepseek-nondeterminism-20260822-55a/
  selection.json
  preflight.json
  run/s.json
  run/retain-attestation.json
  run/omb-completions.jsonl
  run/hindsight-completions.jsonl
  analysis.json
  analysis.md

run-artifacts/2028-0819->0822/
  longmemeval-hindsight-deepseek-nondeterminism-55.log
```

The selection manifest owns membership, the run result owns the 55 new observations, the pilot summary owns the 15 prior observations, and `analysis.json` owns the combined derived estimates. No derived file becomes a replacement for `outputs/longmemeval/hindsight-deepseek/rag/s.json`.

Related main report: [LongMemEval Hindsight DeepSeek reproduction summary](summary.md).
