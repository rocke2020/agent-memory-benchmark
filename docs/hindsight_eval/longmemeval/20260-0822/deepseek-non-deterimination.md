# DeepSeek nondeterminism study: result, design, and runbook

**Terms:** The **candidate** is our frozen 448/500 DeepSeek result. The **pilot five** are five candidate errors already rerun three times. The **new 55** are 25 additional errors plus 30 originally correct controls that each contribute one terminal observation. **Repair-4** is the failure-specific rerun of the four new-55 questions that did not produce journals in the first paid attempt. A **composite result** combines immutable results from more than one create-only profile while preserving a hash-bound source attestation for every question. A **recovery** is original fail → rerun pass; a **regression** is original pass → rerun fail. A **create-only bank** is a new Hindsight bank that must not overwrite or delete earlier benchmark state.

> **TL;DR:** Stage 1 observed 8 recoveries and 1 regression in the new 55 questions; after population weighting and combining the pilot five, the estimated gross instability is 21.325 verdicts across the 458-question clean frame, while signed net drift is +9.325 verdicts. This is enough to reject a single DeepSeek full-pipeline score as a precise basis for future in-house memory comparisons, so no cost extension is needed for that policy decision; important comparisons require replicas or explicit uncertainty.

## 1. Objective and proof boundary

This study finds that future evaluations of our own agent memory cannot treat one DeepSeek score as precise and should use repeated runs or explicit uncertainty for important comparisons. It estimates how unstable the DeepSeek Hindsight pipeline is at the observed configuration and whether that instability is small or large relative to 25 verdicts; it does not rerun Gemini or claim an exact decomposition of the 5.0-point gap.

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
| Official gzip-byte SHA-256 | `2025b1def4794861ba768730d2090816c6a51425b46d29545762db554c3818ca` |
| Dataset SHA-256 | `d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442` |
| Pilot evidence | `eval_analysis/evidence/pilot5-20260822-b.json` |
| Pilot evidence SHA-256 | `771ccdbf5b9cbde7f2a587bf60d4123f2e0909fe0ea01ebe4fdf948ec2514ba3` |
| Resume-1 log SHA-256 | `052450343a16bf61ef3b2cb1ebded489a7e09b4e02b66bf26c73ca76b86762d6` |
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

The manifest contains exactly `study_id`, `analysis_frame`, the three ordered query-ID lists, and `exclusions`; its hash is SHA-256 over UTF-8 canonical JSON with sorted keys and compact separators, excluding no data and adding no timestamp or self-hash. Expected canonical selection-manifest SHA-256: `572a3a774c8e4734fb017f882c031f8299c6db08b1f623e76946c7b5eaa3a482`.

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

The manifest-driven implementation is `eval_analysis/deepseek_nondeterminism.py`; it reuses the verified pilot primitives while replacing the mutable AMB checkpoint with immutable per-question journal entries.

Its four internal commands are:

```text
select     build the frozen manifest and reject any ID/hash/quota drift
preflight  validate files, runtime roles, profiles, document sets, and live models
run        execute exactly the new 55 once in create-only isolated banks
analyze    combine the new 55 with pilot evidence and write the estimates
```

The runner additionally writes append-only retain receipts and question-scoped answer/judge completion evidence. Preflight records the current study-code and lockfile hashes, and `run` refuses any drift or a preflight older than 15 minutes.

Required offline tests:

1. The selector reproduces both expected hashes and all quotas.
2. Changing one ID, status, type, exclusion, or source hash fails closed.
3. Every successful question creates one immutable journal entry; a changed or duplicate entry fails closed.
4. Retrieval document and chunk IDs remain inside that question's expected document set.
5. Every retain batch and completion attempt has one terminal receipt, and OpenAI SDK retries remain disabled.
6. A partial run cannot overwrite the candidate, pilot evidence, selection manifest, log, journal entry, or completed output.
7. Analysis averages replicas within each pilot question, then weights the per-type rates to the eligible populations and reproduces all three pilot-substitution sensitivity estimates.

## 6. Execution runbook

The create-only launcher performs selection, live preflight, the paid 55, and offline analysis in one fail-fast pipeline with `pipefail`; the confirmation flag records the already completed manual Flash 0731, Pro 0813, and default-thinking-high check.

### Start the complete Stage 1

```bash
./eval_analysis/run_deepseek_nondeterminism_55.sh --confirm-supplier-versions
```

The launcher refuses existing selection/run state, profile state, or log; verifies both selection hashes and `new_errors=25 controls=30 new_runs=55`; loads the real `.env` with secrets redacted; resolves answer, judge, extraction, embedding, and reranker roles; makes minimal live Pro/Flash calls; and runs a one-document patched-daemon synchronous-retain canary before the paid profile. The canary daemon stops and its state is preserved, then the run uses one new profile with 55 isolated banks; each completed question becomes an immutable journal file, every trace remains append-only, and the paid daemon stops in `finally` before the final result is created.

## 7. Failure and resume protocol

A partial or failed paid run preserves every successful question as an immutable journal entry and never creates the final `run/s.json`. The first new-55 attempt reached 51 journals before Hindsight's 300-second daemon idle timer cancelled four still-running retain requests; repair therefore operates on four complete question units, not on selected inner batches.

- Never delete a Hindsight bank or profile.
- Never overwrite the candidate, pilot summary, selection manifest, log, journal entry, attestation, or final result.
- On HTTP 500, completion failure, document mismatch, model drift, or daemon shutdown failure, stop and retain the log plus partial artifacts.
- Derive completed units from valid terminal result records bound to the frozen manifest, not from process exit or non-empty output.
- Do not rerun the one-line launcher after failure. Inspect the immutable journal and traces first, then use a failure-specific command that selects only missing or invalid IDs in a new profile.
- The initial launcher deliberately does not guess a generic recovery action before the failure class is known; a repaired result may be merged only after proving exactly 55 selected IDs, with repaired records replacing only their failed counterparts.

### 7.1 Repair-4 execution flow

Repair-4 keeps the original profile, database, 51 journals, three traces, preflight, and failure log read-only. It uses one new paid profile with four isolated banks plus a separate fresh canary profile, and disables daemon auto-exit because a synchronous retain request may legitimately run for more than five minutes.

The repair launcher performs these steps in order:

1. Re-derive the frozen selection and prove that the only absent journals are `5c40ec5b`, `1568498a`, `f685340e`, and `18dcd5a5` at original ordinals 1, 16, 26, and 43.
2. Validate the other 51 journal objects, retrieval identities, 51 answer/judge completion pairs, and 329 successful retain-batch identities; hash those journals, original traces, preflight, selection, main failure log, and original daemon log into a create-only repair plan. Record the original database path without starting, querying, or hashing that database. Every retrieved document and chunk identity must belong to the question's expected document set; Hindsight may omit candidate chunk bodies beyond its chunk-token budget, so absence from the returned `chunks` map is not treated as cross-bank contamination.
3. Load the real runtime configuration, set `HINDSIGHT_EMBED_DAEMON_IDLE_TIMEOUT=0`, probe DeepSeek Pro and Flash, and run a one-document patched-Hindsight canary in a fresh profile.
4. Run exactly four queries with 189 documents and 26 synchronous retain batches in a second fresh profile. Stop and verify the daemon before publishing repair attestation or result files.
5. Validate all four document sets, retrieval IDs, retain receipts, answer/judge traces, extraction trace, model identities, request IDs, usage, source hashes, and code hashes; then publish the repair attestation followed by its four-result `s.json`. If the process stops after complete journals and traces but before that attestation, an offline seal deterministically reconstructs it from those immutable artifacts and the stopped-profile proof without another model call.
6. Revalidate every hash from the repair plan and repair attestation. Build a version-2 composite attestation with separate original and repair components; the original failed Hindsight completion trace is hash-bound failure provenance, while only the original 329 complete-question retain receipts and 51 scoped answer/judge pairs are accepted as clean evidence.
7. Run an offline-only, restart-safe finalize: create or byte-verify the composite attestation, create or byte-verify the four missing canonical journal paths, and publish or byte-verify the deterministic `run/s.json` last as the commit marker. Existing but different bytes fail closed; the paid `run` command itself is never replayed.
8. Run the Section 8 analysis against the attested composite and create `analysis.json` plus `analysis.md` without further model calls.

The original retain trace contains 343 successful batch identities, but 14 belong to abandoned partial banks for the four missing questions. The composite accepts only the 329 complete-question batch identities from the original run plus all 26 clean repair identities, yielding the required 355 without reusing partial-bank state.

Run the repair and analysis pipeline:

```bash
./eval_analysis/run_deepseek_nondeterminism_repair_4.sh --confirm-supplier-versions
```

If the paid repair-4 phase fails, its log, plan, canary, profile, journals, and traces remain immutable; neither the original launcher nor the paid repair command is rerun under the same identity. If only sealing, finalize, or analysis is interrupted, resume the no-model phases directly; each verifies identical existing bytes:

```bash
uv run --locked python eval_analysis/deepseek_nondeterminism_repair4.py finalize
uv run --locked python eval_analysis/deepseek_nondeterminism_repair4.py analyze
```

Do not rerun the shell launcher because its log is create-only and it intentionally begins at live preflight. A later paid recovery must select only the still-missing or invalid repair questions under a new suffix.

## 8. Analysis method

The primary estimand averages repeats within a pilot question first, gives sampled questions equal weight inside their question type, and then weights each type's recovery and regression rates to its eligible error and correct populations; it does not treat all 60 sampled questions as one unweighted population.

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

One new observation per non-pilot question cannot support a precise confidence interval. The implementation therefore reports exact observed counts, the population-weighted point estimate, and the three pilot-substitution values without claiming a confidence interval.

## 9. Observed result and decision

Stage 1 shows material full-pipeline verdict instability: the new 55 changed from 30 baseline passes to 37 rerun passes through 9 verdict flips—eight recoveries and one regression. Population weighting does not turn these values into a causal share of the official-versus-local gap, but it does make the operational decision clear: a single DeepSeek run is directional evidence, not a precise score for close agent-memory comparisons.

| Observation | Result |
|---|---:|
| New-error recoveries | 8/25 |
| Correct-control regressions | 1/30 |
| Raw verdict flips | 9/55 (16.4%) |
| Raw signed change | +7 verdicts |
| Population-weighted net drift | +9.325/458 verdicts (+2.04 pp) |
| Population-weighted gross instability | 21.325/458 verdicts (4.66 pp) |
| Net scale versus the 25-verdict gap | 0.373 |
| Gross scale versus the 25-verdict gap | 0.853 |

The three pilot-replica substitutions keep net drift between 8.658 and 10.658 verdicts and gross instability between 20.658 and 22.658 verdicts. The direction is therefore not an artifact of choosing one of the three pilot replicas, although the one-observation-per-new-question design still does not support a confidence interval.

Repair-4 restored the four missing observations without adding extra sample weight: all 26 retain batches succeeded and its two sampled errors recovered while its two correct controls remained correct.

The result answers the cost decision but not causal attribution. It measures the full configured path—DeepSeek-backed Hindsight extraction and recall, DeepSeek answer generation, and DeepSeek judging—so `0.373` and `0.853` are signed and gross scale comparisons with the 25-verdict gap, not percentages of that gap caused by nondeterminism.

The authoritative derived artifacts are local create-only files at `eval_analysis/nondeterminism-results/deepseek-nondeterminism-20260822-55a/analysis.json` and `analysis.md`; their hashes are bound by the composite attestation and they are intentionally not repository links.

## 10. Sample adequacy and trust decision

The 60 unique questions and 70 rerun observations are sufficient to reject single-run trustworthiness because material flipping was observed in both directions. They are not sufficient to estimate a narrow confidence interval or certify an exact variance for every question type, but greater precision would not change the current policy decision.

- The 30 sampled errors estimate recovery; the 30 controls estimate regression.
- At the worst-case rate near 50%, a simple 30-observation binomial proportion has a 95% uncertainty scale of roughly ±18 percentage points before weighting.
- Per-type sample sizes range from 1 to 15, so individual type estimates are substantially less stable than the pooled direction.
- The pilot's repeated observations improve the within-question estimate for five errors only; they do not increase the number of represented questions.
- Deterministic stratification and population weighting reduce avoidable selection distortion but cannot replace more independent questions or replicas.

Use raw flips, the population-weighted net/gross estimates, and the three pilot substitutions together. The observed material flipping rejects single-run trustworthiness; the small control sample still limits the precision of the regression-rate estimate, so the study does not claim an exact whole-benchmark variance.

## 11. Adaptive extension

Stage 1 selects the predeclared **clearly untrustworthy** outcome, so Stage 2 is not planned: more paid samples would refine the number but would not change the requirement for replicas or uncertainty reporting. The extension design remains available only if a later project needs a narrower quantitative variance estimate for a different decision.

The full 500-question run was approximately CNY 1,600, so a simple linear estimate puts Stage 1's 55 new questions near CNY 176. This is planning scale, not a quote: question lengths, extraction volume, retries, and off-peak pricing can change actual cost. Record the supplier dashboard before and after every paid stage.

The predeclared cost outcomes were:

1. **Clearly untrustworthy — selected:** stop and publish the approximation; more samples are unnecessary for that policy decision.
2. **Inconclusive, but cost control matters more than certifying one-run stability:** stop and label the result `cost-limited`; future important comparisons require replicas or uncertainty reporting.
3. **Inconclusive, and permitting future single-run evaluation would materially reduce ongoing cost:** offer the next deterministic batch and wait for explicit cost approval.

When extension is justified, enlarge the sample by continuing the same frozen hash ranking rather than choosing questions from observed answers.

| Stage | Newly paid questions | Cumulative unique questions | Decision |
|---|---:|---:|---|
| Stage 1 | 25 errors + 30 controls = 55 | 60, including pilot five | Completed; stop for the current trust-policy decision. |
| Stage 2 | remaining 13 eligible errors + next 30 controls = 43 | 103 | Not planned; requires a new precision objective and explicit cost approval. |
| Stage 3+ | next 30 controls per stage | 133, 163, ... | Continue only while the trust decision remains inconclusive and the user approves each batch. |

Stage 2 exhausts all 43 eligible errors, so later stages improve only the regression estimate over the much larger 415-question correct population. Never rerun a completed extension question merely to balance counts; repeated-question evidence is analyzed as a cluster, not as new independent questions.

Stop when the trust decision is clear, the eligible control population is exhausted, or the user declines the next cost gate. If budget stops the sequence first, report the achieved interval and label the result `cost-limited`, not `sample-sufficient`.

## 12. Acceptance gates

The study is publishable as an approximation only when every gate below passes.

- Selection manifest matches both frozen SHA-256 values and contains 5 pilot, 25 new error, and 30 control IDs with no overlap.
- The composite contains exactly 55 unique terminal results in frozen order: 25 baseline failures and 30 baseline passes, sourced from 51 original journals and four repair journals without overlap.
- All 2,654 expected document IDs are covered across the accepted isolated banks; all retrieval IDs stay inside the matching bank; and the accepted receipts contain exactly 329 complete-question original batches plus 26 clean repair batches.
- Accepted completion evidence contains 51 original and four repair answer/judge pairs, with no failed terminal events or opaque SDK retry allowance in those scoped calls.
- Requested models, resolved models, supplier versions, thinking behavior, prompts, dataset, and Hindsight configuration match the frozen run.
- The original and repair preflight patched-daemon canaries are each hash-bound and each has one confirmed document, one successful synchronous retain receipt, extraction/verification completion evidence, and a verified daemon stop.
- The sidecar binds every result to answer/judge usage, request IDs, and prompt hashes, plus stable context, answer, raw-response, and source-document hashes.
- The Hindsight daemon is stopped after success or failure.
- Candidate and pilot evidence hashes are unchanged after the run.
- Analysis exposes raw counts, the declared population-weighted estimate, three pilot sensitivity estimates, and the no-confidence-interval limitation.

## 13. Outputs and ownership

All new state is create-only and study-scoped, leaving the paid 500-question result unchanged.

Expected paths:

```text
eval_analysis/nondeterminism-results/deepseek-nondeterminism-20260822-55a/
  selection.json
  preflight.json
  preflight-canary/retain-batches.jsonl
  preflight-canary/hindsight-completions.jsonl
  journal/001-<query-id>.json ... journal/055-<query-id>.json
  run/s.json
  run/retain-attestation.json
  run/retain-batches.jsonl
  run/omb-completions.jsonl
  run/hindsight-completions.jsonl
  analysis.json
  analysis.md

  repair-4/plan.json
  repair-4/preflight.json
  repair-4/preflight-canary/retain-batches.jsonl
  repair-4/preflight-canary/hindsight-completions.jsonl
  repair-4/journal/001-5c40ec5b.json ... journal/004-18dcd5a5.json
  repair-4/run/s.json
  repair-4/run/retain-attestation.json
  repair-4/run/retain-batches.jsonl
  repair-4/run/omb-completions.jsonl
  repair-4/run/hindsight-completions.jsonl

run-artifacts/2028-0819->0822/
  longmemeval-hindsight-deepseek-nondeterminism-55.log
  longmemeval-hindsight-deepseek-nondeterminism-repair-4.log
```

The selection manifest owns membership, the composite attestation owns the 51-plus-4 provenance boundary, the final run result owns the 55 new observations, the pilot summary owns the 15 prior observations, and `analysis.json` owns the combined derived estimates. No derived file becomes a replacement for `outputs/longmemeval/hindsight-deepseek/rag/s.json`.

Related main report: [LongMemEval Hindsight DeepSeek reproduction summary](summary.md).
