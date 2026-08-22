# LongMemEval reproduction evidence capsule

> **TL;DR:** These compact, redacted files make the report's score, paired-verdict, context-token, pilot, and re-judge claims auditable from a clean checkout without committing the large paid-run result, daemon databases, or raw completion logs.

## Contents

The capsule preserves the smallest evidence needed for each conclusion, together with the SHA-256 of every authoritative local source.

- `official-comparison.json`: 500 paired verdict rows, input hashes, context-token counts, category totals, and source artifact hashes.
- `pilot5-20260822-b.json`: five-question replica verdicts, stable retrieval/answer hashes, document attestations, trace summaries, daemon-log hashes, and the original pilot source-code hashes.
- `deepseek-pro-recheck.json`: the 14 exact answers, gold answers, Pro verdicts, and Pro reasons from the successful auxiliary re-judge.
- `resume-4.json`: source/merged hashes, three document-count attestations, score, and log hash.
- `MANIFEST.sha256`: integrity hashes for the four JSON files.

The full local source result remains at `outputs/longmemeval/hindsight-deepseek/rag/s.json` and must have SHA-256 `4e94268f30bda9dedf45853c37b1aa9b4385c6e8a4121f2eb3141e77c1d4fb23`. The official artifact is downloadable from [Agent Memory Benchmark](https://agentmemorybenchmark.ai/run/outputs%2Flongmemeval%2Fhindsight%2Frag%2Fs.json.gz?id=e47becba) and must have SHA-256 `d0bcc75fb4060f0a395239a353d2066d3f7d252bbe16d674c951d9855deaf9cf`.

## Verification

The capsule is valid only when every manifest hash and structural assertion succeeds.

```bash
cd eval_analysis/evidence
shasum -a 256 -c MANIFEST.sha256

jq -e '
  .paired.matched_queries == 500 and
  .paired.input_mismatches == {"gold_answers":0,"meta":0,"query":0} and
  .paired.verdict_transitions == {
    "both_fail":13,
    "both_pass":434,
    "reference_fail_candidate_pass":14,
    "reference_pass_candidate_fail":39
  } and
  (.paired.rows | length) == 500 and
  ([.paired.rows[] | select(.candidate_input_sha256 != .reference_input_sha256)] | length) == 0
' official-comparison.json

jq -e '
  .replica_correct == [2,2,3] and
  ([.questions[] | select(.mixed_replica_verdicts)] | map(.query_id)) == ["b0479f84"] and
  (.replicas | length) == 3
' pilot5-20260822-b.json

jq -e '.status == "DEEPSEEK_PRO_RECHECK_OK" and .case_count == 14' \
  deepseek-pro-recheck.json

jq -e '
  .source_correct == 448 and
  .merged_correct == 448 and
  [.questions[].confirmed_documents] == [45,45,49]
' resume-4.json
```

## Proof boundary

The capsule supports aggregate and hash-based audit, not reconstruction of the omitted full conversations or model outputs. The completed pilot used successful-response tracing and could not exclude retries internal to the OpenAI SDK; `pilot5-20260822-b.json` records that limitation, while the committed tracer now disables opaque SDK retries and records attempt/terminal pairs for future experiments.
