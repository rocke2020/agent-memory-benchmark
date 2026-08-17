# AMB — Agent Memory Benchmark

We built AMB because we wanted to be honest about how Hindsight performs — and because no existing benchmark gave us the full picture. AMB is fully open: datasets, prompts, scoring logic, and results.

Live leaderboard: **[agentmemorybenchmark.ai](https://agentmemorybenchmark.ai)**

## The problem with existing benchmarks

LoComo and LongMemEval are solid datasets, but they were designed for an era of 32k context windows. State-of-the-art models now have million-token context windows — on most instances, a naive "dump everything into context" approach scores competitively, not because it's a good memory architecture, but because retrieval has become the easy part. The benchmarks can no longer tell them apart.

Both datasets were also built around chatbot use cases. Agents today don't just answer questions about conversation history — they research, plan, execute multi-step tasks, and build knowledge across many interactions. AMB adds datasets that focus on agentic tasks: memory across tool calls, knowledge built from document research, preferences applied to multi-step decisions.

## What AMB measures

A memory system that scores 90% accuracy but costs $10 per user per day is not better than one that scores 82% and costs $0.10. AMB starts from accuracy because it's the hardest to fake, and tracks speed and token cost alongside it.

The only credible benchmark result is one you can reproduce yourself. AMB publishes everything: the evaluation harness, judge prompts, answer generation prompts, and the exact models used. Small changes to any of these can swing accuracy scores by double digits — we publish all of them.

## How it works

AMB separates memory extraction, final-answer generation, and answer judging so each role can use the model and API protocol appropriate to its job.

1. **Ingest** — documents from a dataset are loaded into a memory provider
2. **Retrieve** — for each query the memory provider retrieves relevant context
3. **Generate** — the model selected by `OMB_ANSWER_LLM` and `OMB_ANSWER_MODEL` produces an answer from the retrieved context
4. **Judge** — the model selected by `OMB_JUDGE_LLM` and `OMB_JUDGE_MODEL` scores the answer against gold answers

Retrieval time is tracked separately from generation; ingestion time is also recorded.

### Hindsight extraction protocols

The embedded Hindsight provider delegates memory extraction to the Hindsight daemon. AMB selects the daemon adapter with `HINDSIGHT_API_LLM_PROVIDER`; this Hindsight provider selector identifies an API protocol adapter, not necessarily the model vendor.

| `HINDSIGHT_API_LLM_PROVIDER` | API protocol | Example vendor |
| --- | --- | --- |
| `openai` | OpenAI Chat Completions | OpenAI, DeepSeek, or another compatible endpoint |
| `openai-responses` | OpenAI Responses | OpenAI |
| `anthropic` | Anthropic Messages | Anthropic |
| `gemini` | Gemini GenerateContent | Google |

Set `HINDSIGHT_API_LLM_MODEL` and, when overriding credentials, `HINDSIGHT_API_LLM_API_KEY`. Setting `HINDSIGHT_API_LLM_BASE_URL` also requires the Hindsight-specific key so a conventional credential cannot be sent to an unrelated endpoint. When neither Hindsight-specific value is set, AMB resolves a key and base URL from the same conventional provider namespace. Native Gemini does not use a custom base URL in this integration.

When no selector is set, AMB preserves the original `gemini` and `gemini-2.5-flash-lite` defaults. The pinned `hindsight-api@0.4.17` daemon supports `openai`, `anthropic`, and `gemini`. The `openai-responses` adapter requires `hindsight-api@0.9.0` or newer, selected with `HINDSIGHT_EMBED_API_VERSION`. Changing that daemon version is not a strict reproduction of the original pinned Hindsight run, and compatibility between the pinned 0.4.17 embedded client and a 0.9.x daemon remains unverified without an end-to-end run.

The four selectors are covered by offline configuration tests. To limit external calls and cost, this branch's live provider test covers only the active DeepSeek Chat Completions path.

AMB derives the embedded Hindsight profile from the selected daemon version and extraction configuration. This prevents a healthy daemon started for one provider from being silently reused after the provider, model, endpoint, or credential changes; the credential itself is never placed in the profile name. Each profile owns a separate pg0 memory store, so changing any fingerprinted setting requires re-ingestion and cannot reuse the prior profile with `--skip-ingestion`.

This branch currently uses DeepSeek through Chat Completions:

```dotenv
HINDSIGHT_EMBED_API_VERSION=0.4.17
HINDSIGHT_API_LLM_PROVIDER=openai
HINDSIGHT_API_LLM_MODEL=deepseek-v4-flash
HINDSIGHT_API_LLM_API_KEY=${DEEPSEEK_API_KEY}
HINDSIGHT_API_LLM_BASE_URL=${DEEPSEEK_BASE_URL}
OPENAI_API_KEY=${DEEPSEEK_API_KEY}
OPENAI_BASE_URL=${DEEPSEEK_BASE_URL}
```

## Setup

Configure credentials for the answer, judge, and memory-extraction models selected for the run. For this branch's DeepSeek configuration:

```bash
# Store the real value only in the ignored local .env file.
DEEPSEEK_API_KEY=...
DEEPSEEK_BASE_URL=https://api.deepseek.com
OPENAI_API_KEY=${DEEPSEEK_API_KEY}
OPENAI_BASE_URL=${DEEPSEEK_BASE_URL}
```

## Usage

```bash
# List available datasets, memory providers, and modes
uv run amb providers

# List domains for a dataset
uv run amb domains --dataset personamem

# Run a benchmark
uv run amb run --dataset personamem --domain 32k --memory bm25

# Limit scale for a quick test
uv run amb run --dataset personamem --domain 32k --memory bm25 --query-limit 20

# Oracle mode: ingest only gold documents (tests generation quality in isolation)
uv run amb run --dataset personamem --domain 32k --memory bm25 --oracle

# Dataset statistics
uv run amb dataset-stats --dataset personamem

# Browse results in the browser
uv run amb view
```

## Results

Results are saved to `outputs/{dataset}/{memory}/{mode}/{domain}.json` and can be explored with `uv run amb view`.

## Requirements

- Python ≥ 3.11
- Credentials for the selected extraction, answer, and judge providers
- For MemBench: set `MEMBENCH_DATA_PATH` to your local data directory
