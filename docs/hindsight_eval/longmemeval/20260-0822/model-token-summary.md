# LongMemEval DeepSeek model and token usage

> **TL;DR:** The completed evaluation used **five logical model roles but four distinct configured model names**: DeepSeek Flash for Hindsight extraction and AMB judging, local BGE for embeddings, local MiniLM for reranking, and DeepSeek Pro for final answers. Supplier billing records aggregate the three external LLM roles, while the two local retrieval models are not API-token-metered; the available artifacts cannot reconstruct a per-role token split.

## Terms

The distinction between a model role, a configured alias, and a supplier-resolved version prevents one name from being mistaken for one execution stage.

| Term | Meaning |
|---|---|
| **Model role** | One producer-to-consumer responsibility in the evaluated path. The same model name can serve more than one role. |
| **Configured or requested model** | The model alias or local repository ID selected by `.env` and passed to the relevant adapter. |
| **Supplier-resolved version** | The dated DeepSeek version shown in supplier records after an external alias was requested. Local SentenceTransformers models do not return this field. |
| **Provider selector** | A value such as `openai` that selects an API wire protocol. It does not identify the model vendor; here it routes to a DeepSeek-compatible endpoint. |
| **AMB / `OMB_*`** | AMB is Agent Memory Benchmark. `OMB_*` is the legacy environment-variable prefix still used for its answer and judge configuration. |
| **API tokens** | Supplier-metered tokens for external LLM calls. Local embedding/reranking work and AMB's local `cl100k_base` context count are outside this measure. |

## 1. Complete model inventory

All executed model-producing and model-scoring paths close over the five roles below. DeepSeek Flash is reused for extraction and judging, so five roles resolve to four distinct configured names.

| Role and producer → consumer path | Configured or requested identity | Runtime resolution and evidence | API-token accounting |
|---|---|---|---|
| Hindsight extraction: retained sessions → extracted memory facts | `openai` / `deepseek-v4-flash` | Supplier records: `deepseek-v4-flash-0731`; the daemon log attests the requested alias and `retain_extract_facts` calls. | Included in aggregate external-LLM totals; per-role split unavailable. |
| Hindsight embedding: extracted memories and recall query → semantic vectors | local / `BAAI/bge-small-en-v1.5` | Daemon startup attests the local model and 384 dimensions; exact cached Hugging Face revision unavailable. | Not API-token-metered; local invocation/token counts were not recorded. |
| Hindsight reranking: fused recall candidates and query → ranked candidates | local / `cross-encoder/ms-marco-MiniLM-L-6-v2` | Daemon startup attests the local model; exact cached Hugging Face revision unavailable. | Not API-token-metered; local invocation/token counts were not recorded. |
| AMB final answer: question plus raw recall → structured answer | `openai` / `deepseek-v4-pro` | Result JSON: `openai:deepseek-v4-pro`; supplier records: `deepseek-v4-pro-0813`. | Included in aggregate external-LLM totals; per-role split unavailable. |
| AMB judge: question, gold answer, and generated answer → verdict | `openai` / `deepseek-v4-flash` | Result JSON: `openai:deepseek-v4-flash`; supplier records: `deepseek-v4-flash-0731`. | Included in aggregate external-LLM totals; per-role split unavailable. |

This inventory is complete for the documented `mode=rag` path, not for every capability Hindsight can expose. AMB used retain and one recall per question, then final answer and judge calls; it did not invoke Hindsight `reflect` or the `agentic-rag` planner. Banks were created with observations disabled, so no consolidation-model execution belongs to this run. BM25, graph/temporal retrieval, Reciprocal Rank Fusion, date parsing, and `cl100k_base` tokenization are not additional model roles.

The 500-result JSON directly records only the answer and judge identities. Hindsight extraction, embedding, and reranker identities are attested by the redacted configuration closure and captured daemon/preflight logs; exact local model weight revisions were not preserved. See the [full comparison summary](summary.md#6-complete-model-inventory-and-runtime-versions) and [reproduction guide](94.6-reproduction.md#6-configure-the-deepseek-roles-and-local-retrieval-models).

## 2. Supplier, cost, request, and token records

The measured official-DeepSeek segments cover 462 questions; the 38-question Tencent segment lacks exact cost, request, and token records. The full-500 official-supplier row is therefore an approximation, not a measured total for the mixed-supplier run.

| Segment | Questions | Supply chain | Cost | API requests | Supplier tokens | Evidence status |
|---|---:|---|---:|---:|---:|---|
| Phase 0, `0 → 112` | 112 | Official DeepSeek | CNY 362 | 26,927 | 168,358,427 | Recorded supplier totals. |
| Resume 1, `112 → 150` | 38 | Tencent DeepSeek-compatible | Unavailable | Unavailable | Unavailable | Do not estimate these as measured usage. |
| Resumes 2–3, `150 → 500` | 350 | Official DeepSeek | CNY 1,117 | 82,704 | 518,502,920 | Recorded supplier totals. |
| **Measured official-DeepSeek subtotal** | **462** | **Official DeepSeek** | **CNY 1,479** | **109,631** | **686,861,347** | Sum of Phase 0 and Resumes 2–3. |
| **All 500 via official DeepSeek** | **500** | **Official DeepSeek projection** | **approximately CNY 1,600** | **approximately 118,648** | **approximately 743,356,436** | Counterfactual projection used for budgeting, not actual mixed-supplier usage. |

The run occurred from 2026-08-19 through 2026-08-22 and used off-peak periods to reduce cost. Of the 500 questions, 462 used the official DeepSeek supply chain and 38 used Tencent's DeepSeek-compatible supply chain. The score analysis pools the two cohorts as an explicit evaluation assumption; no matched supplier A/B independently proves quality equivalence.

## 3. Accounting boundary

The recorded request, token, and cost figures combine AMB answer/judge traffic with Hindsight extraction traffic. They do not expose a reliable per-role ledger, and no API-token value should be invented for the local retrieval models.

- The external totals combine `OMB_*` answer/judge usage and `HINDSIGHT_API_*` extraction usage. The saved result does not preserve enough per-response usage to split input, cached input, output, retry, extraction, answer, and judge tokens retroactively.
- Local BGE embeddings and MiniLM reranking ran on the host. Their API requests and API tokens are **not applicable**, while their local text/token throughput is **unavailable** because it was not captured as a billing ledger.
- The DeepSeek supplier-resolved names were `deepseek-v4-flash-0731` and `deepseek-v4-pro-0813`. The configured aliases remain `deepseek-v4-flash` and `deepseek-v4-pro`; aliases and resolved versions must not be conflated.
- AMB's saved **Ctx tokens** value is a local `cl100k_base` count of formatted retrieval context: 24,812,616 total, 49,625.232 mean, displayed as 49,625. It is not supplier usage and excludes the other model-role traffic above.

## deterministic
Embedding and reranking are conditionally deterministic under fixed inputs and runtime. The complete LongMemEval pipeline is nondeterministic because their inputs can change through LLM extraction, and answer/judge generation is also nondeterministic.
