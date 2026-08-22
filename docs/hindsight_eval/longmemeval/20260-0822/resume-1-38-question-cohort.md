# LongMemEval resume-1 38-question cohort analysis

> **TL;DR:** The resume-1 cohort scored **30/38 (78.9%)** in both our Tencent-supplied DeepSeek run and the official Hindsight run, while the official-supplier DeepSeek cohort trailed official Hindsight by 5.4 percentage points; this is weak, approximate evidence that Tencent's DeepSeek-compatible supply chain was not worse and may have been better in this run, but the small, non-random, non-overlapping cohorts do not prove supplier equivalence or a causal quality advantage.

## Terms

These terms separate execution provenance from benchmark performance.

- **Resume-1 cohort**: the 38 consecutive questions completed by `longmemeval-hindsight-deepseek-resume-1.log`; this run used Tencent's DeepSeek-compatible supply chain.
- **Official paired cohort**: the same 38 query IDs selected from the official 500-question Hindsight artifact.
- **Complement**: the other 462 questions in the corresponding 500-question result.
- **Accidental equality**: two runs have the same aggregate correct count but succeed on different questions.
- **Reference gap**: local DeepSeek accuracy minus official Hindsight accuracy on the same query-ID slice; it calibrates for observed slice difficulty but is not a direct supplier A/B measurement.

## 1. Conclusion

The 38-question slice provides weak, approximate evidence that Tencent's supply chain was not worse and may have been better in this run; it does not establish equivalence because the two suppliers handled different question cohorts.

| Result slice | Correct | Accuracy | Reference gap |
|---|---:|---:|---:|
| Tencent-supplied DeepSeek, resume-1 cohort | 30/38 | 78.9% | 0.0 pp |
| Official Hindsight, same 38 IDs | 30/38 | 78.9% | Reference |
| Official-supplier DeepSeek, other questions | 418/462 | 90.5% | -5.4 pp |
| Official Hindsight, same other 462 IDs | 443/462 | 95.9% | Reference |

The Tencent cohort matches official Hindsight in aggregate, whereas the official-supplier cohort is 25 verdicts behind its same-ID official reference. That contrast is favorable to Tencent, but the resume boundary, question composition, and lack of supplier overlap mean it remains an approximate observational signal rather than a controlled supplier comparison.

## 2. Why the matching 78.9% is accidental

The same aggregate accuracy hides materially different question-level outcomes.

| Paired verdict on the 38 IDs | Questions |
|---|---:|
| Both pass | 24 |
| Official pass, local fail | 6 |
| Official fail, local pass | 6 |
| Both fail | 2 |

Only 26 of 38 verdicts agree. Each run has eight failures, but only two failures are shared, so `30/38` versus `30/38` is a numerical coincidence rather than evidence that the pipelines or suppliers performed identically.

## 3. Difficulty and question composition

The slice contains only two question types, both of which are among the harder categories in the full comparison.

| Question type | Questions | Local correct | Official correct |
|---|---:|---:|---:|
| multi-session | 20 | 16/20 | 17/20 |
| single-session-preference | 18 | 14/18 | 13/18 |
| **Total** | **38** | **30/38** | **30/38** |

It contains no single-session-assistant or single-session-user questions, which scored much higher in the full official comparison. The result therefore supports an observational statement, "this 38-question slice was harder for both evaluated pipelines," while the non-random contiguous selection and its category mix prevent a stronger claim that the supplier caused the difference or that every question is intrinsically harder.

## 4. Supplier-quality interpretation

The calibrated result is consistent with Tencent being close to or better than the official DeepSeek supplier in this run, but its uncertainty is too large for a strong supplier-ranking claim.

| Slice | Official-only passes | Local-only passes | Exact paired p-value |
|---|---:|---:|---:|
| Tencent resume-1, 38 questions | 6 | 6 | 1.0 |
| Official-supplier complement, 462 questions | 33 | 8 | 0.000112 |

Within the Tencent slice, the balanced `6` versus `6` discordance means there is no observed directional accuracy difference from official Hindsight. Within the official-supplier complement, `33` versus `8` produces the entire 25-verdict aggregate deficit. Comparing those discordant directions between slices gives a two-sided Fisher exact `p = 0.0598`, or approximately `0.060`; this is only a trend-level weak signal and cannot support a strong conclusion because it remains above the conventional 0.05 threshold.

The statistical values are descriptive rather than causal because supplier assignment follows a resume boundary instead of random allocation, the cohorts do not overlap, and the official Hindsight reference uses the Gemini model stack rather than either DeepSeek supplier. The supported conclusion is therefore limited: Tencent was not observably worse on its 38 assigned questions and may have been better after same-ID difficulty calibration; the data do not prove supplier equivalence, interchangeability, or a general Tencent advantage.

## 5. Cohort provenance

The log and result order identify the cohort without relying on a billing estimate or provider label in the final JSON.

- The resume-1 log first reports 112 already-ingested units.
- It then records exactly 38 unique `start` events and 38 matching successful `done` events.
- Those query IDs match result positions 113 through 150 in one-indexed human numbering, or indices 112 through 149 in the JSON array.
- The first ID is `5a7937c8`; the last is `95228167`.

The exact ordered query IDs are:

```text
5a7937c8, gpt4_ab202e7f, gpt4_e05b82a6, gpt4_731e37d7, edced276,
10d9b85a, e3038f8c, 2b8f3739, 1a8a66a6, c2ac3c61, bf659f65,
gpt4_372c3eed, gpt4_2f91af09, 81507db6, 88432d0a_abs, 80ec1f4f_abs,
eeda8a6d_abs, 60bf93ed_abs, edced276_abs, gpt4_372c3eed_abs, 8a2466db,
06878be2, 75832dbd, 0edc2aef, 35a27287, 32260d93, 195a1a1b,
afdc33df, caf03d32, 54026fce, 06f04340, 6b7dfb22, 1a1907b4,
09d032c9, 38146c39, d24813b1, 57f827a0, 95228167
```

## 6. Impact on the main evaluation

This cohort does not change either reported full-run score because all 500 questions remain included exactly once in each aggregate.

The main comparison should therefore continue to use **448/500 (89.6%)** for our DeepSeek pipeline and **473/500 (94.6%)** for the official Gemini pipeline. The cohort analysis supplies a weak provider-quality signal for operational planning, while still rejecting two stronger interpretations: the 38 questions are not a representative supplier A/B sample, and their equal aggregate accuracy does not prove supplier equivalence.

Related main report: [LongMemEval Hindsight DeepSeek reproduction summary](summary.md).
