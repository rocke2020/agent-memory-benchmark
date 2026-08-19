# Reproducing Hindsight's Published 94.6% LongMemEval Result with DeepSeek

## Terms used in this guide

> **Naming note:** `AMB` is the project name, while the declared executable depends on the source revision. The DeepSeek branch README uses `amb`, and the maintainer reports that `uv run amb dataset-stats --dataset longmemeval` works in the prepared branch environment. Its `pyproject.toml` still declares only `omb`, however, so a fresh environment is guaranteed to expose `omb` rather than `amb`. The commands below use the source-declared `omb`; in the prepared environment, `amb` invokes the same CLI application.

| Term | Meaning in this guide |
|---|---|
| **Hindsight** | The memory system being evaluated. It turns retained conversations into memory representations and recalls relevant information for a question. |
| **LongMemEval S split** | The exact 500-question cleaned LongMemEval input used by the published result and this reproduction protocol. |
| **AMB** | **Agent Memory Benchmark**, the upstream project and evaluation harness that published Hindsight's 473/500 result. The repository is named `agent-memory-benchmark`. |
| **OMB / `omb`** | **Open Memory Benchmark**, the pinned snapshot's package description and original package/CLI name. The DeepSeek branch is based on commit `decbb07f4f9899deac28a76293564cf263872652` and still declares only `omb` in `pyproject.toml`, so it is the source-guaranteed command used in this guide. `omb` is a benchmark command, not a Hindsight service. |
| **`amb`** | The modern primary CLI name, introduced after the published snapshot by commit [`a49e450`](https://github.com/vectorize-io/agent-memory-benchmark/commit/a49e45005926cfc40f0a7ed8da26fa017c57a9e4). Current upstream `main` declares both `amb` and the legacy `omb` alias. The pinned source does not declare `amb` despite using it in README examples, but a reused environment may retain an `amb` launcher from a newer editable install. |
| **`OMB_*` environment variables** | Legacy configuration names retained after the CLI rename, including `OMB_ANSWER_LLM` and `OMB_JUDGE_LLM`. Their prefix does not determine which CLI command is available. |
| **Declared entry point / available launcher** | A declared entry point comes from the checked-out `pyproject.toml`; an available launcher is an executable already present in the active virtual environment. The latter can be stale, so command success alone does not identify what the checked-out source declares. |
| **RAG mode** | **Retrieval-augmented generation** mode: AMB retrieves information from Hindsight, supplies the recall result to an answer model, and then judges the generated answer. |
| **Adapter** | AMB integration code that translates between its common interfaces and one external component. This guide distinguishes the LongMemEval dataset adapter, Hindsight memory adapter, and OpenAI-compatible large language model (LLM) adapter. |
| **Retain / recall** | Hindsight's write and retrieval operations, respectively: retain stores source conversations as memory; recall retrieves information relevant to a query. |
| **Daemon / embedded profile / bank** | The daemon is the local Hindsight API process started by `hindsight-embed`; the embedded profile names that process and its configuration; a bank is an isolated Hindsight memory store. This branch fingerprints the extraction configuration into the profile name and creates one bank per question. |
| **Extraction, answer, and judge models** | Three separate LLM roles. This run sends all three through an OpenAI-compatible protocol to DeepSeek: Flash extracts memories, Pro generates final answers, and Flash judges those answers against the references. |
| **Provider selector** | A selector such as `openai` identifies the API wire protocol used by an adapter, not necessarily the model vendor. Here, `openai` means Chat Completions against a DeepSeek-compatible endpoint. |
| **Dry run** | A read-only preflight that validates the CLI, dataset, and non-secret configuration without retaining memories or calling an external model. There is no `--dry-run` CLI flag in this branch. |
| **Limited run** | A paid end-to-end smoke run over five questions using the normal LongMemEval haystacks. It tests extraction, retrieval, answer generation, judging, and result persistence before the full run. |
| **Gold session / gold answer** | In this guide, a gold session means a complete haystack conversation that the current AMB adapter selects because at least one raw turn has `has_answer=true`; this is not identical to the raw file's `answer_session_ids` field. AMB removes `has_answer` before ingestion. In the inference path, the separate gold answer is excluded from ingestion, retrieval, and answer generation; it is supplied to the judge afterward and persisted in the result. One adapter query may have zero, one, or several gold sessions. |
| **Oracle run** | A five-question calibration run with `--oracle`. AMB ingests the distinct union of the selected queries' gold sessions, so the document count is not generally equal to the query count. It still uses Hindsight extraction and recall and never passes the gold answer to the answer model. |
| **Embedding model / cross-encoder** | The two local retrieval models: the embedding model supports semantic search, while the cross-encoder reranks retrieved candidates before Hindsight returns them. |
| **`uv` / `uvx`** | `uv` installs and runs the pinned Python environment; `uvx` launches a Python package's command in an isolated tool environment. Here, `hindsight-embed` uses `uvx` to start a separate Hindsight API daemon. |
| **Published result artifact** | The compressed AMB result file containing all 500 answers, contexts, judgments, and aggregate metrics for the reported 94.6% run. |
| **Historical protocol, DeepSeek-provider, reference-score, and verdict-vector outcomes** | The historical protocol uses the published Gemini roles. This guide instead runs a DeepSeek-provider variant. A reference-score match means the variant also reaches 473/500 and the six published category totals; a verdict-vector match also matches every question's correct/incorrect judgment. [Section 12](#12-reporting-language) defines the reporting criteria. |
| **Run-attested** | Supported by evidence captured from the actual benchmark process, such as its startup log or replay manifest. A source default is not run-attested proof of what the unpublished original process used. |

> **TL;DR:** The score being compared is AMB's published Hindsight result: 473 correct answers out of 500, or 94.6%. This guide reruns the same public LongMemEval data and Hindsight retrieval path from a branch based on commit `decbb07f4f9899deac28a76293564cf263872652`, but deliberately replaces all three historical Gemini LLM roles with DeepSeek. It is therefore a **DeepSeek-provider reproduction variant**, not a strict reproduction of the published protocol; reaching 94.6% is a reference-score match. Execute the four gates below in order: dry run, limited run, oracle run, then the full 500-question evaluation.

## Run workflow

The [AMB README usage ladder](../../README.md#usage) shows provider discovery, a `--query-limit` run, an `--oracle` run, dataset statistics, and result viewing. The commands below adapt that ladder to LongMemEval and make the four gates explicit. Run them sequentially in the disposable environment from Sections 3–6; never run the paid stages concurrently because they address the same Hindsight benchmark banks.

The workflow assumes another agent has completed the `.env` fix and that these non-secret selectors now exist exactly as shown:

```dotenv
HINDSIGHT_API_LLM_PROVIDER=openai
HINDSIGHT_API_LLM_MODEL=deepseek-v4-flash
OMB_ANSWER_LLM=openai
OMB_ANSWER_MODEL=deepseek-v4-pro
OMB_JUDGE_LLM=openai
OMB_JUDGE_MODEL=deepseek-v4-flash
```

The DeepSeek and OpenAI-compatible key/base-URL variables must also be non-empty, but must never be printed or committed. Hindsight-specific key/base-URL variables are optional when the adapter can resolve the matching conventional pair; if a Hindsight-specific base URL is set, its Hindsight-specific key is mandatory.

### 1. Dry run

The dry run must prove that the selected CLI can load the pinned LongMemEval input and that every DeepSeek role is configured, without retaining memories or making a model call.

First validate `.env` without printing secret values:

```bash
uv run python - <<'PY'
from dotenv import dotenv_values
from pathlib import Path

config = dotenv_values(".env")
expected = {
    "HINDSIGHT_API_LLM_PROVIDER": "openai",
    "HINDSIGHT_API_LLM_MODEL": "deepseek-v4-flash",
    "OMB_ANSWER_LLM": "openai",
    "OMB_ANSWER_MODEL": "deepseek-v4-pro",
    "OMB_JUDGE_LLM": "openai",
    "OMB_JUDGE_MODEL": "deepseek-v4-flash",
}
required_credentials = {
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_BASE_URL",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
}
mismatches = [name for name, value in expected.items() if config.get(name) != value]
missing_credentials = [
    name for name in sorted(required_credentials) if not config.get(name)
]
hindsight_pair_invalid = bool(
    config.get("HINDSIGHT_API_LLM_BASE_URL")
    and not config.get("HINDSIGHT_API_LLM_API_KEY")
)
dataset_path = config.get("LONGMEMEVAL_DATA_PATH")
dataset_missing = not dataset_path or not Path(dataset_path).is_file()
if mismatches or missing_credentials or hindsight_pair_invalid or dataset_missing:
    raise SystemExit(
        "selector mismatches="
        f"{mismatches}; missing credential variables={missing_credentials}; "
        f"invalid Hindsight key/base pair={hindsight_pair_invalid}; "
        f"dataset path missing={dataset_missing}"
    )
print("DeepSeek selectors, credential presence, and dataset path: OK")
PY
```

Then run the read-only CLI checks:

```bash
uv run omb --help
uv run omb providers
uv run omb splits --dataset longmemeval
uv run omb dataset-stats --dataset longmemeval
```

Pass criteria: every command exits zero; `providers` lists `longmemeval` and `hindsight`; `splits` lists `s`; dataset statistics report 500 questions and the six category counts in Section 10. Do not proceed merely because the output is non-empty.

### 2. Limited run

The limited run is the first external-call gate. It must complete five normal RAG questions and write a structurally valid, non-oracle result before the more expensive runs begin.

```bash
export SMOKE_QUERY_LIMIT=5
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export NO_PROXY=127.0.0.1,localhost
export no_proxy="$NO_PROXY"
test ! -e outputs/longmemeval/hindsight-deepseek-limited/rag/s.json
mkdir -p run-artifacts
bash -o pipefail -c '
  uv run omb run \
    --dataset longmemeval \
    --split s \
    --memory hindsight \
    --mode rag \
    --name hindsight-deepseek-limited \
    --query-limit "$SMOKE_QUERY_LIMIT" \
    2>&1 | tee run-artifacts/longmemeval-hindsight-deepseek-limited.log
'
```

Validate more than process exit status:

```bash
jq -e --argjson limit "$SMOKE_QUERY_LIMIT" '
  .dataset == "longmemeval" and
  .split == "s" and
  .run_name == "hindsight-deepseek-limited" and
  .oracle == false and
  .answer_llm == "openai:deepseek-v4-pro" and
  .judge_llm == "openai:deepseek-v4-flash" and
  .total_queries == $limit and
  (.results | length) == $limit and
  ([.results[].query_id] | unique | length) == $limit and
  ([.results[] | select(.answer == null or .answer == "")] | length) == 0 and
  ([.results[] | select(.context == null or .context == "")] | length) == 0
' outputs/longmemeval/hindsight-deepseek-limited/rag/s.json
```

Stop on any selector, ingestion, retrieval, answer, judge, schema, or count failure. The unique run name isolates the result file, but the Hindsight adapter still derives the same LongMemEval bank IDs and recreates the selected banks; this is why the environment must contain no valuable Hindsight data.

### 3. Oracle run

The oracle run reduces session-level haystack noise, but it does not assume one document per question and does not bypass Hindsight. For the selected queries, AMB takes the distinct union of their `gold_ids`, ingests those complete sessions, and then runs the normal extraction, recall, answer, and judge path.

The pinned S split's 13 repeated session IDs are all non-answer fillers, so no gold ID selects more than one document occurrence. For this dataset, the Oracle document count is therefore:

```text
number of distinct generated gold document IDs (`question_id_session_id`) across the selected queries
```

For the canonical S split and `SMOKE_QUERY_LIMIT=5`, the first five queries happen to have exactly one gold session each:

| Query ID | Normal haystack document occurrences | Oracle gold sessions | Gold document ID |
|---|---:|---:|---|
| `e47becba` | 53 | 1 | `e47becba_answer_280352e9` |
| `118b2229` | 45 | 1 | `118b2229_answer_40a90d51` |
| `51a45a95` | 50 | 1 | `51a45a95_answer_d61669c7` |
| `58bf7951` | 57 | 1 | `58bf7951_answer_355c48bb` |
| `1e043500` | 50 | 1 | `1e043500_answer_3e012175` |
| **Total** | **255** | **5** | **5 distinct documents** |

The runner reports 255 limited-run documents because it counts session occurrences before the Hindsight adapter deduplicates document IDs. Two repeated non-answer filler IDs in these five haystacks reduce the limited run to 253 distinct IDs submitted for retention; all five Oracle document IDs are distinct.

Five Oracle documents is a property of this fixed five-query selection, not a general Oracle rule. Across all 500 canonical S-split questions, the current AMB adapter derives the following gold-session multiplicity from raw `has_answer` annotations:

| Gold sessions for one question | Questions |
|---:|---:|
| 0 | 21 |
| 1 | 196 |
| 2 | 228 |
| 3 | 30 |
| 4 | 16 |
| 5 | 6 |
| 6 | 3 |

In total, 283 questions have more than one gold session, and one question can have as many as six. Multiple gold sessions mean the dataset marks several complete conversations as answer-bearing; they may contain distributed, updated, duplicated, or conflicting evidence. The annotation count alone does not prove that every marked session is logically indispensable.

This multiplicity is specific to the adapter behavior being reproduced. The pinned raw JSON lists 948 references in `answer_session_ids`, while the current adapter derives 854 gold references from `has_answer`; 62 questions differ because 94 raw declared references do not carry a `has_answer` marker. No `has_answer`-derived reference is absent from the raw declared list. Use the adapter-derived `gold_ids` for this fixed five-query calibration and its validation; substituting `answer_session_ids` would define a different Oracle input.

> **Full-Oracle limitation:** A score from a 500-question Oracle invocation is not currently safe to interpret as a complete 500-question result. Twenty-one queries have no adapter-derived `gold_ids`. The runner rejects Oracle mode only when the combined gold-ID set is empty; in a mixed selection, it loads documents for the other queries. LongMemEval then evaluates question-isolated units by iterating document-backed units, so a query with no Oracle document can be omitted instead of producing an explicit result or failure. This is a source-level conclusion, not a run-attested outcome: a full Oracle invocation was not executed because it would write to and recreate Hindsight banks. Before reporting a full Oracle score, explicitly resolve the gold-source semantics or define zero-gold-query behavior, then require exactly 500 unique results. Do not silently treat an omitted-query result or a reduced denominator as canonical. The guide's canonical full evaluation does not use `--oracle` and is unaffected.

Before the paid Oracle run, verify this exact selection against the configured dataset. The check rejects a changed query order, a selected query with no gold session, a missing gold document, or a gold-document count other than the expected five:

```bash
export EXPECTED_ORACLE_QUERY_IDS=e47becba,118b2229,51a45a95,58bf7951,1e043500
export EXPECTED_ORACLE_DOCS=5
uv run python - <<'PY'
import os

from dotenv import load_dotenv

from memory_bench.dataset.longmemeval import LongMemEvalDataset

load_dotenv(".env", override=True)
query_limit = int(os.environ["SMOKE_QUERY_LIMIT"])
expected_query_ids = os.environ["EXPECTED_ORACLE_QUERY_IDS"].split(",")
expected_documents = int(os.environ["EXPECTED_ORACLE_DOCS"])
dataset = LongMemEvalDataset()
queries = dataset.load_queries("s", limit=query_limit)
actual_query_ids = [query.id for query in queries]
empty_query_ids = [query.id for query in queries if not query.gold_ids]
gold_ids = {gold_id for query in queries for gold_id in query.gold_ids}
documents = dataset.load_documents("s", ids=gold_ids)
loaded_ids = {document.id for document in documents}

if len(queries) != query_limit:
    raise SystemExit(f"expected {query_limit} queries, loaded {len(queries)}")
if len(expected_query_ids) != query_limit:
    raise SystemExit(
        f"expected query ID count {len(expected_query_ids)} does not match limit {query_limit}"
    )
if actual_query_ids != expected_query_ids:
    raise SystemExit(
        f"query order changed: expected={expected_query_ids}, actual={actual_query_ids}"
    )
if empty_query_ids:
    raise SystemExit(f"selected queries without gold sessions: {empty_query_ids}")
if len(gold_ids) != expected_documents:
    raise SystemExit(
        f"expected {expected_documents} distinct gold sessions, found {len(gold_ids)}"
    )
if len(documents) != expected_documents:
    raise SystemExit(
        f"expected {expected_documents} gold documents, loaded {len(documents)}"
    )
if loaded_ids != gold_ids:
    raise SystemExit(
        f"gold document mismatch: missing={sorted(gold_ids - loaded_ids)}, "
        f"unexpected={sorted(loaded_ids - gold_ids)}"
    )

for query in queries:
    print(f"{query.id}: {len(query.gold_ids)} gold session(s)")
print(f"{len(queries)} queries -> {len(gold_ids)} distinct gold sessions")
PY
```

Store the Oracle result separately from both the limited run and full evaluation:

```bash
: "${SMOKE_QUERY_LIMIT:?run the limited gate first in the same shell}"
test ! -e outputs/longmemeval/hindsight-deepseek-oracle/rag/s.json
bash -o pipefail -c '
  uv run omb run \
    --dataset longmemeval \
    --split s \
    --memory hindsight \
    --mode rag \
    --name hindsight-deepseek-oracle \
    --query-limit "$SMOKE_QUERY_LIMIT" \
    --oracle \
    2>&1 | tee run-artifacts/longmemeval-hindsight-deepseek-oracle.log
'
```

```bash
jq -e \
  --argjson limit "$SMOKE_QUERY_LIMIT" \
  --argjson expected_docs "$EXPECTED_ORACLE_DOCS" '
  .dataset == "longmemeval" and
  .split == "s" and
  .run_name == "hindsight-deepseek-oracle" and
  .oracle == true and
  .answer_llm == "openai:deepseek-v4-pro" and
  .judge_llm == "openai:deepseek-v4-flash" and
  .total_queries == $limit and
  .ingested_docs == $expected_docs and
  (.results | length) == $limit and
  ([.results[].query_id] | unique | length) == $limit and
  ([.results[] | select(.answer == null or .answer == "")] | length) == 0 and
  ([.results[] | select(.context == null or .context == "")] | length) == 0
' outputs/longmemeval/hindsight-deepseek-oracle/rag/s.json
```

The `--oracle` flag does not pass gold text directly to the answer model. A gold document contains the complete conversation session; AMB removes the internal `has_answer` marker before ingestion. In the inference path, the separate gold answer is excluded from ingestion, retrieval, and answer generation, then supplied to the judge and saved in the result JSON.

The recorded five-query artifacts from 2026-08-18 demonstrate why Oracle accuracy is diagnostic rather than a guaranteed upper bound:

| Run | Runner `ingested_docs` session occurrences | Correct | Accuracy | Failed query IDs |
|---|---:|---:|---:|---|
| Limited | 255 | 4/5 | 80% | `51a45a95` |
| Oracle | 5 | 3/5 | 60% | `118b2229`, `51a45a95` |

For `118b2229`, the gold session contains the user's correct statement that the commute takes 45 minutes each way and an assistant reply that incorrectly paraphrases it as one hour. Hindsight recalled both claims; the Oracle answer returned both 45 and 60 minutes, and the judge marked it incorrect. This explains that artifact's extra Oracle failure, but it does not establish that Oracle mode generally lowers accuracy. Treat Oracle as a reduced-noise end-to-end calibration, not as proof that generation works independently of memory or as a score that must exceed the limited run.

### 4. Full evaluation

The full evaluation is eligible for comparison with 94.6% only after all three earlier gates pass. It must evaluate all 500 questions with no limiting, oracle, skip-ingestion, or cached-context narrowing. Crash-resume via `--skip-ingested` is permitted — the published 94.6% artifact itself records a resumed invocation (Section 2), so a resumed rerun is not disqualified.

```bash
# Fresh start only; skip this guard when resuming — the partial output must stay in place.
test ! -e outputs/longmemeval/hindsight-deepseek/rag/s.json
bash -o pipefail -c '
  /usr/bin/time -p uv run omb run \
    --dataset longmemeval \
    --split s \
    --memory hindsight \
    --mode rag \
    --name hindsight-deepseek \
    2>&1 | tee run-artifacts/longmemeval-hindsight-deepseek.log
'
```

Do not add any of these options to the full command — each one narrows or rewrites what gets evaluated:

```text
--query-limit --query-id --doc-limit --category --oracle --skip-ingestion
--skip-retrieval --skip-answer --only-failed
```

`--skip-ingested` is the one exception: if the run breaks mid-way, rerun the identical command with `--skip-ingested` appended. The runner saves results incrementally after every question and, on resume, loads the same output file, reuses every completed unit's stored result, and continues with the remaining units only. The resumed output remains eligible for the Section 9 and 10 comparison; only the `ingested_docs` expectation changes (Section 9).

The output is `outputs/longmemeval/hindsight-deepseek/rag/s.json`. Continue to Section 9 for structural validation and Section 10 for the comparison with the published reference; a zero exit code alone is not acceptance evidence. After validation, `uv run omb view` may be used to browse the saved results, but the viewer is not a validation gate.

## 1. Define the result being reproduced

The comparison target is one specific published artifact: the **94.6% Hindsight result (473 correct out of 500)** produced by AMB. The executable procedure in this guide is a DeepSeek-provider variant of that benchmark, not the older LongMemEval runner inside the Hindsight repository and not the exact historical Gemini protocol.

Use these authoritative artifacts:

- [Hindsight README benchmark claim](https://github.com/vectorize-io/hindsight#memory-performance--accuracy)
- [Agent Memory Benchmark repository](https://github.com/vectorize-io/agent-memory-benchmark)
- [First public AMB snapshot containing the result](https://github.com/vectorize-io/agent-memory-benchmark/tree/decbb07f4f9899deac28a76293564cf263872652)
- [Published Hindsight result artifact](https://github.com/vectorize-io/agent-memory-benchmark/blob/decbb07f4f9899deac28a76293564cf263872652/outputs/longmemeval/hindsight/rag/s.json.gz)
- [LongMemEval cleaned dataset](https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned)

Do **not** use `scripts/benchmarks/run-longmemeval.sh` from the Hindsight repository to reproduce 94.6%. That is a different harness with different defaults and is not the provenance of the AMB result.

## 2. What is open, and what is missing

The historical method is open enough to audit and rerun, but it is not a complete immutable replay capsule. This guide additionally changes the three external LLM roles, so report its result as a DeepSeek-provider variant even if every numerical acceptance condition below is met.

| Item | Public? | Reproduction consequence |
|---|---:|---|
| LongMemEval dataset adapter and document construction | Yes | Exact dataset-to-AMB transformation can be inspected and pinned. |
| Hindsight memory adapter | Yes | Retain/recall translation, bank isolation, extraction model, recall budget, and token limits are visible. |
| OpenAI-compatible LLM adapter | Yes | The DeepSeek branch's structured Chat Completions answer/judge calls, retry behavior, and explicit `temperature=0.0` can be inspected and pinned. |
| Answer and category-specific judge prompts | Yes | The LongMemEval dataset adapter owns this prompt logic, which can be pinned to a commit. |
| AMB dependency lock | Yes, with a daemon caveat | `uv.lock` installs `hindsight-all==0.4.17`, `hindsight-embed==0.4.17`, and `hindsight-api==0.4.15`, but normal installed embedded mode launches a separate `hindsight-api@0.4.17` process through `uvx`. The AMB lock therefore does not alone prove the daemon code or all of its resolved transitive dependencies. |
| Embedding and reranker defaults | Yes | Hindsight API v0.4.17 defaults to local `BAAI/bge-small-en-v1.5` embeddings (384 dimensions) and local `cross-encoder/ms-marco-MiniLM-L-6-v2` reranking. The result artifact does not record whether the original process overrode them. |
| Per-question answers, contexts, verdicts, and aggregate result | Yes | The 473/500 reference can be independently audited. |
| Source commit recorded inside the result JSON | No | The commit below is the first public source/result snapshot, not run-attested provenance. |
| Dataset revision/hash recorded inside the result JSON | No | Pin and record it explicitly for the rerun. |
| Immutable DeepSeek model revisions | No | The configured `deepseek-v4-pro` and `deepseek-v4-flash` service aliases may change or disappear. |
| Sampling seed | No | The DeepSeek branch sets answer and judge temperature to `0.0`, but remote model behavior is still not guaranteed to be bit-for-bit deterministic. |
| Original OS, hardware, `uv` version, run timestamp, and full command | No | Record these for the rerun; they cannot be reconstructed exactly. |

### Adapter ownership and ingestion accounting

**Ingestion ordering and concurrency.** Each LongMemEval question is one Hindsight bank, and a bank's retain batches are submitted strictly sequentially in dataset time order — knowledge-update and temporal-reasoning questions depend on facts landing in order inside their bank, so no intra-bank concurrency is allowed. Concurrency happens only across banks: the runner starts the ingestion of the next 4 units while the current unit's queries are answered, keeping up to 5 bank chains in flight, which matches the Hindsight daemon's internal cap of 5 concurrent retain operations. This is what keeps a full run inside a ~16–17 hour ingestion budget instead of the ~75 hours a fully serial submission would need.

The three adapters have separate responsibilities. Keeping those ownership boundaries explicit explains why the dataset contains 23,867 document occurrences while the Hindsight memory adapter submits 23,854 distinct retain items.

An adapter is integration code, not the LongMemEval dataset, Hindsight, or DeepSeek itself. The three adapter roles are:

1. **LongMemEval dataset adapter** — [`src/memory_bench/dataset/longmemeval.py`](../../src/memory_bench/dataset/longmemeval.py). It reads `longmemeval_s_cleaned.json`, converts every session occurrence into an AMB `Document`, converts every question into an AMB `Query`, assigns document IDs as `{question_id}_{session_id}`, and defines the answer and category-specific judge prompts. The S split therefore produces 23,867 `Document` occurrences but only 23,854 distinct document IDs: 13 questions each repeat one non-answer filler session ID with identical conversation content and a different timestamp.
2. **Hindsight memory adapter** — [`src/memory_bench/memory/hindsight.py`](../../src/memory_bench/memory/hindsight.py). It translates AMB `Document` ingestion into Hindsight retain operations and AMB queries into Hindsight recall operations. The pinned local adapter deduplicates each bank's retain items by `document_id`, keeps the first occurrence, and consequently submits 23,854 retain items for a clean full run. It returns both AMB retrieved `Document` objects and Hindsight's raw recall response. It also controls benchmark-critical integration settings: one bank per LongMemEval question, the embedded Hindsight profile, the extraction-model configuration, retain batching, recall budget/token limits, and chunk/entity inclusion.
3. **OpenAI-compatible LLM adapter** — [`src/memory_bench/llm/openai.py`](../../src/memory_bench/llm/openai.py). It calls the DeepSeek-compatible Chat Completions endpoint for AMB answer generation and judging, requests structured JSON, sets temperature to `0.0`, and implements model-call retries. Hindsight's internal memory extraction is configured separately through the Hindsight memory adapter.

The `ingested_docs` discrepancy is almost certainly resume accounting rather than a smaller evaluation dataset. The verified dataset partition is exact:

| Dataset range | Questions | AMB document occurrences counted by runner | Distinct IDs submitted by local Hindsight |
|---|---:|---:|---:|
| First invocation candidate | 1–263 | 12,564 | 12,557 |
| Resumed invocation candidate | 264–500 | 11,303 | 11,297 |
| Complete S split | 1–500 | 23,867 | 23,854 |

The pinned runner initializes `ingested_docs_count` to zero for every invocation. With `--skip-ingested`, it loads and merges previously completed results but restores neither their ingestion time nor their document count; it increments the counter only for newly processed units. Because the first 263 questions contain exactly 12,564 sessions and the remaining 237 contain exactly 11,303, the published artifact strongly indicates a resume after question 263. The original command and log are absent, so this remains a source-and-data-supported inference rather than run-attested history.

Therefore, `ingested_docs: 11303` means “raw AMB document occurrences counted in the likely resumed invocation,” not “documents in the complete benchmark” or “retain items submitted to Hindsight.” The local Hindsight adapter would submit 11,297 distinct IDs for that range. A clean one-shot run must report `ingested_docs: 23867`, while submitting 23,854 retain items after the pinned adapter's deduplication; do not use 11,303 as the clean-run acceptance condition.

## 3. Freeze the reference base and DeepSeek branch

Commit `decbb07f4f9899deac28a76293564cf263872652` is the first public AMB commit containing both the 94.6% result and its implementation. The DeepSeek-provider branch pins commit `9880574ac3a22caf0324ebc1c435e60211f1e256`, whose merge base with the reference is exactly that published commit. This branch adds configurable Hindsight extraction protocols and routes answer/judge calls through the OpenAI-compatible adapter; it also explicitly sets evaluation temperature to `0.0`. Those are intentional differences from the historical Gemini run, not undocumented drift.

```bash
git clone https://github.com/vectorize-io/agent-memory-benchmark.git
cd agent-memory-benchmark
AMB_REFERENCE_COMMIT=decbb07f4f9899deac28a76293564cf263872652
AMB_DEEPSEEK_COMMIT=9880574ac3a22caf0324ebc1c435e60211f1e256
git checkout --detach "$AMB_DEEPSEEK_COMMIT"
test "$(git rev-parse HEAD)" = "$AMB_DEEPSEEK_COMMIT"
test "$(git merge-base HEAD "$AMB_REFERENCE_COMMIT")" = "$AMB_REFERENCE_COMMIT"
```

Verify the committed lock and reference result before installing anything:

```bash
printf '%s  %s\n' 7fb73767aefe748c5fe2500de81a963ab2ff80368c1b444edd14a37c979500c3 uv.lock | shasum -a 256 -c -
printf '%s  %s\n' 2025b1def4794861ba768730d2090816c6a51425b46d29545762db554c3818ca outputs/longmemeval/hindsight/rag/s.json.gz | shasum -a 256 -c -
```

Expected output for both checks is `OK`. Stop if either hash differs.

## 4. Use a disposable Hindsight environment

Run the benchmark in a fresh OS account, VM, or otherwise disposable environment with no valuable Hindsight data. The Hindsight memory adapter uses an embedded profile named `omb-longmemeval-s-<fingerprint>`, creates one bank per question, and attempts to delete each same-named benchmark bank before recreating it. The fingerprint covers daemon version, extraction provider, model, endpoint, and credential without exposing the credential.

Requirements:

- Python 3.11 or newer.
- `uv` installed; record its exact version.
- A fresh Python virtual environment created after checking out the pinned commit. Do not reuse `.venv` from another AMB revision: editable package renames can leave both `amb` and `omb` launchers available.
- A DeepSeek API credential and compatible base URL with access to `deepseek-v4-pro` and `deepseek-v4-flash`.
- Enough API quota for Hindsight extraction, 500 answer calls, and 500 judge calls.
- Enough time for a full run. The reference records about 8 hours 21 minutes of ingestion, but its `11,303` document counter exactly matches the likely resumed portion, so this is probably not the total ingestion time for all 23,867 documents. Budget more than the published duration for a clean run. With this branch's concurrent ingestion (see "Ingestion ordering and concurrency" below), a 3-question probe measured ~4.1 s/document at only 3 parallel question chains; at the full run's 5 chains that projects to roughly 16–17 hours of ingestion, with answering overlapped by ingestion prefetch.

Install AMB from the committed lock:

```bash
uv sync --frozen
uv run python - <<'PY'
from importlib.metadata import version
for package in ("hindsight-all", "hindsight-api", "hindsight-embed"):
    print(package, version(package))
PY
```

Required output:

```text
hindsight-all 0.4.17
hindsight-api 0.4.15
hindsight-embed 0.4.17
```

Confirm that the fresh environment contains the source-declared CLI:

```bash
test -x .venv/bin/omb
```

This check must exit zero. If `amb` is also present in the prepared branch environment, it may run successfully because both launchers import `memory_bench.cli:app`; record which launcher was used, but do not mistake launcher availability for a source declaration.

These installed versions do not mean the daemon runs the installed `hindsight-api==0.4.15`. In normal installed mode, [`DaemonEmbedManager._find_api_command()`](https://github.com/vectorize-io/hindsight/blob/2191654b1f9b454703916612fec57ce226c7746b/hindsight-embed/hindsight_embed/daemon_embed_manager.py#L91-L102) reads `HINDSIGHT_EMBED_API_VERSION`, defaults it to the `hindsight-embed` package version, and launches `uvx hindsight-api@<version>`. For this snapshot, that default target is `hindsight-api@0.4.17`; upstream tag `v0.4.17` resolves to source commit `2191654b1f9b454703916612fec57ce226c7746b`.

An already responsive fingerprinted profile daemon can be reused. In the disposable account or VM, require a genuinely new Hindsight home before the run:

```bash
test ! -e "$HOME/.hindsight"
```

If this check fails, do not delete or reuse valuable Hindsight data. Switch to a fresh disposable account or VM. The `.env` pins in Section 6 fix the new daemon's version and retrieval models; preserve its startup log as runtime evidence.

## 5. Pin and verify the LongMemEval input

The pinned LongMemEval dataset adapter downloads the Hugging Face `main` revision by default, which is not strict enough. Download the exact known dataset revision and point the LongMemEval dataset adapter to the verified local file.

```bash
AMB_DATA_DIR="$(pwd)/../amb-reproduction-data"
AMB_DATA_FILE="$AMB_DATA_DIR/longmemeval_s_cleaned.json"
mkdir -p "$AMB_DATA_DIR"
curl --fail --location --retry 3 --max-time 1800 \
  'https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/98d7416c24c778c2fee6e6f3006e7a073259d48f/longmemeval_s_cleaned.json' \
  --output "$AMB_DATA_FILE"
printf '%s  %s\n' d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442 "$AMB_DATA_FILE" | shasum -a 256 -c -
```

The source file must be 277,383,467 bytes and the hash check must report `OK`. A different hash is a different benchmark input.

Confirm the complete document count and the exact boundary implied by the published resume metadata:

```bash
jq -e '
  length == 500 and
  ([.[0:263][].haystack_sessions | length] | add) == 12564 and
  ([.[263:500][].haystack_sessions | length] | add) == 11303 and
  ([.[].haystack_sessions | length] | add) == 23867
' "$AMB_DATA_FILE"
```

This check must return exit code zero. It verifies dataset structure, not that the original publisher used the same immutable file; the published result JSON contains no dataset hash.

Confirm the exact duplicate behavior that the pinned local Hindsight adapter will apply:

```bash
python - "$AMB_DATA_FILE" <<'PY'
import json
import sys
from collections import defaultdict

with open(sys.argv[1], encoding="utf-8") as handle:
    rows = json.load(handle)

duplicate_groups = []
for row in rows:
    by_id = defaultdict(list)
    for index, (session_id, date, session) in enumerate(zip(
        row["haystack_session_ids"], row["haystack_dates"], row["haystack_sessions"]
    )):
        by_id[session_id].append((index, date, session))
    for session_id, occurrences in by_id.items():
        if len(occurrences) > 1:
            duplicate_groups.append((row, session_id, occurrences))

assert len(duplicate_groups) == 13
assert all(len(occurrences) == 2 for _, _, occurrences in duplicate_groups)
assert all(occurrences[0][2] == occurrences[1][2] for _, _, occurrences in duplicate_groups)
assert all(session_id not in row["answer_session_ids"] for row, session_id, _ in duplicate_groups)
assert sum(len(row["haystack_session_ids"]) for row in rows) == 23867
assert sum(len(set(row["haystack_session_ids"])) for row in rows) == 23854
print("13 exact-content non-answer duplicate ID groups; 23,867 occurrences -> 23,854 local Hindsight retain items")
PY
```

This is an AMB adapter behavior to preserve, not an instruction to rewrite the official LongMemEval JSON. Do not deduplicate the source file, add occurrence suffixes to document IDs, or move this normalization into the dataset adapter for the controlled DeepSeek run; any of those changes defines an additional corrected or occurrence-preserving variant.

## 6. Configure the DeepSeek roles and local retrieval models

The measured variant includes three DeepSeek LLM roles plus Hindsight's embedding and cross-encoder models. Pin all five roles: changing either retrieval model changes what the answer model receives even when every DeepSeek setting stays the same.

### The three LLM roles

The benchmark uses two DeepSeek model names across three separate responsibilities. The provider value `openai` selects an OpenAI-compatible protocol; it does not claim that OpenAI supplies the model.

| Role | Protocol and pinned model | What the model receives and produces |
|---|---|---|
| **Hindsight memory extraction** | `openai` / `deepseek-v4-flash` | Hindsight processes retained chat sessions and extracts the memory representations that its recall path will later search. This role is configured with `HINDSIGHT_API_LLM_*`, independently of AMB's answer/judge selectors. |
| **Final answer generation** | `openai` / `deepseek-v4-pro` | After Hindsight recall, AMB supplies the question, question date, and raw recall response. The model synthesizes that evidence into structured `reasoning` and `answer` fields without seeing the gold answer. |
| **Answer judging** | `openai` / `deepseek-v4-flash` | AMB supplies the question, gold answer, generated answer, and category-specific grading rules. The model returns a short reason and a `correct` boolean. |

### Hindsight retrieval models

A clean installed run targets Hindsight API v0.4.17 and, without overrides, resolves both retrieval components to local SentenceTransformers models.

| Role | Provider and pinned model | What it does |
|---|---|---|
| **Embedding** | Local SentenceTransformers, `BAAI/bge-small-en-v1.5` | Produces 384-dimensional vectors for stored memories and recall queries, providing the semantic branch of Hindsight retrieval. |
| **Cross-encoder reranking** | Local SentenceTransformers, `cross-encoder/ms-marco-MiniLM-L-6-v2` | Scores query-candidate pairs after semantic, BM25, graph, and temporal results are fused with Reciprocal Rank Fusion (RRF). |

The [Hindsight API v0.4.17 defaults at commit `2191654b`](https://github.com/vectorize-io/hindsight/blob/2191654b1f9b454703916612fec57ce226c7746b/hindsight-api/hindsight_api/config.py#L333-L348) select the local providers, these exact model names, 384 embedding dimensions, and at most 300 cross-encoder candidates. The [embedding factory](https://github.com/vectorize-io/hindsight/blob/2191654b1f9b454703916612fec57ce226c7746b/hindsight-api/hindsight_api/engine/embeddings.py#L800-L822) and [cross-encoder factory](https://github.com/vectorize-io/hindsight/blob/2191654b1f9b454703916612fec57ce226c7746b/hindsight-api/hindsight_api/engine/cross_encoder.py#L964-L992) then instantiate the local SentenceTransformers implementations.

`budget="high"` does not enable or select the cross-encoder. Hindsight reranks whenever recall produces candidates; high budget instead maps to `thinking_budget=1000`. The v0.4.17 recall path fuses retrieval branches with RRF, sends at most 300 candidates through the cross-encoder, applies its combined scoring, and only then applies the `thinking_budget * 2` result cap. See the [budget mapping](https://github.com/vectorize-io/hindsight/blob/2191654b1f9b454703916612fec57ce226c7746b/hindsight-api/hindsight_api/engine/memory_engine.py#L2135-L2138) and [RRF-to-reranking path](https://github.com/vectorize-io/hindsight/blob/2191654b1f9b454703916612fec57ce226c7746b/hindsight-api/hindsight_api/engine/memory_engine.py#L2583-L2668).

These names are the source-default effective models for a new no-override daemon, not historical runtime proof. The published 94.6% artifact records neither model, and an inherited environment, profile configuration, development checkout, `HINDSIGHT_EMBED_API_VERSION`, or already-running profile daemon can alter the effective runtime. Explicitly pinning the following values turns them from assumptions into rerun configuration.

### Why the roles remain separate

Final answer generation has the broadest synthesis burden, while extraction is high-volume and judging is a constrained classification task. This branch assigns the Pro model to final answers and the Flash model to extraction and judging. That split is an explicit experiment configuration, not an inferred property of the published Gemini run.

The reported score remains end to end: it measures Hindsight extraction and retrieval together with the DeepSeek answer model and DeepSeek judge. It is not a pure retrieval score, and it is not directly attributable to any one of the three LLM roles.

The pinned `.gitignore` does not exclude `.env`. Before creating the repository-root file, protect it with `rg -qxF '.env' .git/info/exclude || printf '%s\n' '.env' >> .git/info/exclude`, then keep it uncommitted:

```dotenv
DEEPSEEK_API_KEY=replace_with_your_key
DEEPSEEK_BASE_URL=replace_with_your_compatible_endpoint
OPENAI_API_KEY=${DEEPSEEK_API_KEY}
OPENAI_BASE_URL=${DEEPSEEK_BASE_URL}
LONGMEMEVAL_DATA_PATH=/absolute/path/to/amb-reproduction-data/longmemeval_s_cleaned.json
HINDSIGHT_EMBED_API_VERSION=0.4.17
HINDSIGHT_API_LLM_PROVIDER=openai
HINDSIGHT_API_LLM_MODEL=deepseek-v4-flash
HINDSIGHT_API_LLM_API_KEY=${DEEPSEEK_API_KEY}
HINDSIGHT_API_LLM_BASE_URL=${DEEPSEEK_BASE_URL}
HINDSIGHT_API_EMBEDDINGS_PROVIDER=local
HINDSIGHT_API_EMBEDDINGS_LOCAL_MODEL=BAAI/bge-small-en-v1.5
HINDSIGHT_API_RERANKER_PROVIDER=local
HINDSIGHT_API_RERANKER_LOCAL_MODEL=cross-encoder/ms-marco-MiniLM-L-6-v2
HINDSIGHT_API_RERANKER_MAX_CANDIDATES=300
OMB_ANSWER_LLM=openai
OMB_ANSWER_MODEL=deepseek-v4-pro
OMB_JUDGE_LLM=openai
OMB_JUDGE_MODEL=deepseek-v4-flash
```

The Hindsight memory adapter resolves its internal extraction role from `HINDSIGHT_API_LLM_*`; the answer and judge roles resolve independently from `OMB_ANSWER_*` and `OMB_JUDGE_*`. The AMB CLI loads the repository-root `.env` with override enabled, and Hindsight API v0.4.17 also searches for a `.env` from its working directory with override enabled. Inspect the repository-root file rather than assuming shell variables take precedence. Never print or commit API keys or authenticated endpoint URLs.

If either DeepSeek model alias is unavailable or the endpoint does not support the structured Chat Completions response used by this branch, stop and report the provider variant as failed. Do not silently replace a model or protocol.

## 7. Confirm the exact evaluation path

The pinned LongMemEval dataset adapter, DeepSeek-configured Hindsight memory adapter, and OpenAI-compatible LLM adapter form a chain that isolates every question in its own Hindsight bank, performs high-budget recall, generates a structured answer, and then applies the LongMemEval category-specific LLM judge. The complete ownership-preserving flow is:

```text
longmemeval_s_cleaned.json
  → LongMemEval dataset adapter
  → AMB Documents + Queries + answer/judge prompt builders
  → Hindsight memory adapter
  → Hindsight retain → memory extraction → Hindsight recall
  → AMB retrieved Documents + raw Hindsight recall response
  → LongMemEval dataset adapter builds answer prompt from the raw response JSON
  → OpenAI-compatible LLM adapter → deepseek-v4-pro
  → generated answer
  → LongMemEval dataset adapter selects category-specific judge prompt
  → OpenAI-compatible LLM adapter → deepseek-v4-flash
  → correct / incorrect
```

The three adapters' pinned settings are part of the measured system, not optional tuning knobs.

The pinned path is:

1. The **LongMemEval dataset adapter** loads all 500 questions from the `s` split.
2. The **LongMemEval dataset adapter** converts all 23,867 haystack session occurrences into JSON conversation `Document` objects and each question into one `Query`; 13 duplicate pairs share a generated document ID.
3. The **Hindsight memory adapter** uses `question_id` as the isolation/user ID and creates bank `longmemeval-s-u<question_id>`.
4. The **Hindsight memory adapter** keeps the first item for each document ID, reducing the clean full run to 23,854 retain items, and retains them in batches of 20 with observations disabled.
5. Hindsight extracts memories through the OpenAI-compatible Chat Completions protocol using `deepseek-v4-flash`, as configured by the **Hindsight memory adapter**, and the v0.4.17 daemon encodes stored memories with local `BAAI/bge-small-en-v1.5` 384-dimensional embeddings.
6. The **Hindsight memory adapter** recalls with `budget="high"`, `max_tokens=32768`, `include_chunks=true`, `max_chunk_tokens=32768`, and `include_entities=false`. Hindsight embeds the query, runs semantic, BM25, graph, and temporal retrieval, fuses candidates with RRF, and reranks up to 300 candidates with local `cross-encoder/ms-marco-MiniLM-L-6-v2`.
7. The **Hindsight memory adapter** returns AMB retrieved `Document` objects plus the raw Hindsight recall response.
8. The AMB RAG mode formats the retrieved `Document` objects as context and passes the raw response in prompt metadata. The **LongMemEval dataset adapter** builds the answer prompt and, when that raw response is present, serializes it as JSON instead of using the formatted context.
9. The **OpenAI-compatible LLM adapter** generates structured `reasoning` and `answer` fields with `deepseek-v4-pro` at temperature `0.0`.
10. The **LongMemEval dataset adapter** selects the category-specific judge prompt, and the **OpenAI-compatible LLM adapter** calls `deepseek-v4-flash` at temperature `0.0` to judge the answer.
11. The AMB runner records `correct=true` or `false`; aggregate accuracy is `correct / 500`.

The four-stage workflow at the head of this guide is the canonical execution order. It adapts the branch README's `amb` examples to the source-declared `omb` entry point and adds result-level acceptance checks after each paid gate.

## 8. Preserve failures and understand the optional resume shape

A failed external call is a failed gate, not permission to continue with partial evidence. Preserve its stage-specific log and result file, record the failing role and error, and stop. The Hindsight memory adapter catches and skips some retain errors, so process exit code alone is not proof that all intended memories were retained.

### Optional: emulate the likely published resume sequence

This two-command sequence can emulate the published artifact shape, including the final `ingested_docs: 11303`. It is useful for auditing the historical metadata, but it is an inferred execution sequence—not the canonical DeepSeek evaluation—because the original command and interruption log were not published.

Run this instead of the canonical one-shot command. Start from a separate clean output and disposable Hindsight environment; do not reuse an output file produced by another experiment.

```bash
test ! -e outputs/longmemeval/hindsight-deepseek-resume/rag/s.json

# First 263 questions contain 12,564 session documents.
uv run omb run \
  --dataset longmemeval \
  --split s \
  --memory hindsight \
  --mode rag \
  --name hindsight-deepseek-resume \
  --query-limit 263

# Reuse those 263 results and evaluate the remaining 237 questions / 11,303 documents.
uv run omb run \
  --dataset longmemeval \
  --split s \
  --memory hindsight \
  --mode rag \
  --name hindsight-deepseek-resume \
  --skip-ingested
```

**Unverified procedure outcome:** based on the pinned runner, the second result is expected to contain all 500 query results while reporting only the 11,303 documents counted during that invocation. This two-stage sequence has not been executed as part of preparing this guide because it incurs the full external-model cost. Do not report it as the verified original command unless independently run and recorded.

## 9. Validate the result structurally

A valid result must contain exactly 500 unique questions, internally consistent counts, non-empty answers and contexts, and the recorded answer/judge model IDs. This check can fail even when the CLI exits successfully.

The AMB result schema records the answer and judge model IDs but not the Hindsight daemon version, embedding model, cross-encoder model, or resolved Hugging Face weight revisions. Validate those from the new daemon's startup log and preserve them separately in the replay manifest; the result JSON alone cannot prove them.

```bash
AMB_OUTPUT=outputs/longmemeval/hindsight-deepseek/rag/s.json
jq -e '
  .dataset == "longmemeval" and
  .split == "s" and
  .run_name == "hindsight-deepseek" and
  .memory_provider == "hindsight" and
  .mode == "rag" and
  .oracle == false and
  .answer_llm == "openai:deepseek-v4-pro" and
  .judge_llm == "openai:deepseek-v4-flash" and
  .total_queries == 500 and
  .ingested_docs == 23867 and
  (.results | length) == 500 and
  ([.results[].query_id] | unique | length) == 500 and
  ([.results[] | select(.answer == null or .answer == "")] | length) == 0 and
  ([.results[] | select(.context == null or .context == "")] | length) == 0 and
  .correct == ([.results[] | select(.correct == true)] | length) and
  .accuracy == (.correct / .total_queries)
' "$AMB_OUTPUT"
```

The `23,867` check applies to a one-shot run. For any resumed run — whether the optional Section 8 emulation or an unplanned crash-resume — replace it with `.ingested_docs < 23867`, because the counter is reset per invocation and counts only the units processed by the final invocation; the exact value depends on the resume boundary (11,303 for the published 263-question emulation). The real completeness gate is the 500-result, non-empty answer/context checks above, not this counter. Record the resume boundary in the replay manifest so the expected value can be recomputed. This runner counter is incremented before the Hindsight adapter's ID deduplication: it represents raw AMB `Document` occurrences handed to the adapter, not the 23,854 or 11,297 distinct retain items the adapter subsequently submits. It also does not prove that every retain batch completed successfully.

Print the independently recomputed total and category results:

```bash
jq '{total_queries, correct, accuracy, answer_llm, judge_llm}' "$AMB_OUTPUT"
jq -r '
  .results
  | group_by(.meta.question_type)[]
  | [.[0].meta.question_type, length,
     (map(select(.correct == true)) | length)]
  | @tsv
' "$AMB_OUTPUT" | sort
```

## 10. Compare against the published 94.6% result

A reference-score match requires 473/500 overall and the same six category totals. It does not convert this DeepSeek-provider variant into a historical-protocol reproduction because all three external LLM roles differ from the published Gemini configuration.

The published result's 500 answers cover the full S split, but its `ingested_docs: 11303` and approximately 8-hour-21-minute ingestion time describe only the strongly inferred resumed invocation. That invocation contains 11,297 distinct document IDs after the pinned local Hindsight deduplication. Compare accuracy and per-question verdicts against the publication; compare a clean run's raw runner count against 23,867 and its adapter-level retain-item count against 23,854.

The published reference is:

| Question type | Total | Correct | Accuracy |
|---|---:|---:|---:|
| knowledge-update | 78 | 76 | 97.44% |
| multi-session | 133 | 121 | 90.98% |
| single-session-assistant | 56 | 56 | 100.00% |
| single-session-preference | 30 | 23 | 76.67% |
| single-session-user | 70 | 68 | 97.14% |
| temporal-reasoning | 133 | 129 | 96.99% |
| **Overall** | **500** | **473** | **94.60%** |

Exact aggregate acceptance check:

```bash
jq -e '.total_queries == 500 and .correct == 473 and .accuracy == 0.946' \
  "$AMB_OUTPUT"
```

To compare per-question verdicts without loading the large contexts twice:

```bash
gzip -dc outputs/longmemeval/hindsight/rag/s.json.gz \
  | jq '[.results[] | {query_id, correct}] | sort_by(.query_id)' \
  > run-artifacts/reference-verdicts.json
jq '[.results[] | {query_id, correct}] | sort_by(.query_id)' "$AMB_OUTPUT" \
  > run-artifacts/rerun-verdicts.json
diff -u run-artifacts/reference-verdicts.json run-artifacts/rerun-verdicts.json
```

An empty diff means the 500 binary verdicts match. It still does not imply byte-identical retrieved contexts, answers, or judge reasoning.

## 11. Preserve a replay manifest

The rerun should produce the provenance metadata missing from the published JSON. Without it, future comparisons cannot distinguish code drift, data drift, model drift, and runtime drift.

Record at least:

```bash
{
  git rev-parse HEAD
  git merge-base HEAD "$AMB_REFERENCE_COMMIT"
  shasum -a 256 uv.lock
  shasum -a 256 "$AMB_DATA_FILE"
  shasum -a 256 "$AMB_OUTPUT"
  uv --version
  uv run python --version
  uv run python - <<'PY'
from importlib.metadata import version
for package in ("hindsight-all", "hindsight-api", "hindsight-embed", "openai"):
    print(package, version(package))
PY
  printf '%s\n' \
    'HINDSIGHT_EMBED_API_VERSION=0.4.17' \
    'HINDSIGHT_API_LLM_PROVIDER=openai' \
    'HINDSIGHT_API_LLM_MODEL=deepseek-v4-flash' \
    'HINDSIGHT_API_EMBEDDINGS_PROVIDER=local' \
    'HINDSIGHT_API_EMBEDDINGS_LOCAL_MODEL=BAAI/bge-small-en-v1.5' \
    'HINDSIGHT_API_RERANKER_PROVIDER=local' \
    'HINDSIGHT_API_RERANKER_LOCAL_MODEL=cross-encoder/ms-marco-MiniLM-L-6-v2' \
    'HINDSIGHT_API_RERANKER_MAX_CANDIDATES=300' \
    'OMB_ANSWER_LLM=openai' \
    'OMB_ANSWER_MODEL=deepseek-v4-pro' \
    'OMB_JUDGE_LLM=openai' \
    'OMB_JUDGE_MODEL=deepseek-v4-flash'
} > run-artifacts/replay-manifest.txt
```

Also preserve the new `omb-longmemeval-s-<fingerprint>` daemon startup log and record the lines that identify the Hindsight API version, extraction protocol/model, embedding model, reranker model, device, and resolved Hugging Face snapshot revisions when available. Model repository names alone do not freeze their weight revisions. Record the UTC start/end time, operating system, CPU architecture, whether any command was resumed, every transient API error, and total API cost if available. Do not include keys, authenticated URLs, or the profile fingerprint, and scan logs for secrets before sharing them.

## 12. Reporting language

Use precise language that separates the historical result from this provider substitution. The 94.6% reference is auditable, but a DeepSeek score cannot establish reproduction of the historical Gemini protocol.

Use one of these conclusions:

- **DeepSeek-provider run completed:** the DeepSeek branch, AMB lock, Hindsight API v0.4.17 daemon, dataset hash, five configured model roles, prompts, recall settings, `23,867 -> 23,854` local Hindsight ID-deduplication behavior, and 500-query validation are all attested; the score may differ from 94.6%.
- **Reference score matched:** the validated DeepSeek-provider run scored exactly 473/500 with the six published category totals. This is not historical-protocol reproduction.
- **Reference verdict vector matched:** the reference score matched and all 500 `correct` values match the published result. The underlying models and protocol still differ.
- **Published resume shape emulated:** the inferred 263-question first invocation plus `--skip-ingested` produces 500 merged results and a final `ingested_docs: 11303`; this does not prove the unpublished original command.
- **DeepSeek-provider run failed validation:** a configured model is unavailable, the daemon or either retrieval model is not runtime-attested, an input hash differs, errors were skipped, cached contexts or answers were substituted for fresh retrieval/generation, or any required DeepSeek setting differs. (A `--skip-ingested` crash-resume is not itself a failure condition; the published reference was produced the same way.)

Do not say “Hindsight 94.6% was strictly reproduced” for this DeepSeek variant, even if it reaches exactly 94.6%. Say “the DeepSeek-provider run matched the published reference score,” and support that statement with the structural and category checks above.
