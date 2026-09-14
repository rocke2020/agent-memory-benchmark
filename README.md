# LongMemEval Hindsight Reproduction with DeepSeek V4

English | [简体中文](README_CN.md)

This branch focuses on reproducing Hindsight's published LongMemEval `s`-split evaluation with a DeepSeek model stack. The exact Flash model type used for memory extraction and judging was **`deepseek-v4-flash-0731`**; DeepSeek V4 Pro 0813 generated the final answers.

## 1. Paired official comparison

My run evaluated the same 500 query IDs, questions, gold answers, and metadata as the official result. It scored **448/500 (89.6%)**, versus the official Hindsight result's **473/500 (94.6%)**.

| Result | Correct | Accuracy | Extraction / answer / judge |
| --- | ---: | ---: | --- |
| [Official Hindsight](https://agentmemorybenchmark.ai/run/outputs%2Flongmemeval%2Fhindsight%2Frag%2Fs.json.gz?id=e47becba) | 473/500 | 94.6% | Gemini 2.5 Flash Lite / Gemini 3.1 Pro Preview / Gemini 2.5 Flash Lite |
| My DeepSeek V4 run | 448/500 | 89.6% | Flash 0731 / Pro 0813 / Flash 0731 |
| Difference | -25 | -5.0 percentage points | Combined pipeline difference, not a model-only A/B test |

| Paired verdict | Questions |
| --- | ---: |
| Both pass | 434 |
| Official pass, mine fails | 39 |
| Official fails, mine passes | 14 |
| Both fail | 13 |

The deficit is concentrated in multi-session (`-14`) and temporal-reasoning (`-7`) questions. Three incomplete retain units were later rerun from scratch in isolated banks; all three remained correct, so the retain-complete repaired result also scores 448/500.

### Why is my accuracy lower?

1. **Different external LLM stack.** The official run used Gemini for extraction, answers, and judging; mine used Flash 0731, Pro 0813, and Flash 0731. A weaker or behaviorally different DeepSeek stack is the strongest candidate explanation, especially because extraction and judging both used Flash, but this is not isolated causal proof: all three LLM roles changed together and none of the 500 saved contexts are byte-identical. Among my 52 failures, 49 still contain every official answer-bearing session in their raw chunks, so most are not simple gold-session omissions.
2. **Measured full-pipeline nondeterminism.** A five-error pilot plus 55 new full-pipeline reruns produced 8 recoveries, 1 regression, and 9 verdict flips. Population weighting over the clean 458-question frame estimated about **2.04 percentage points of net score drift** and **4.66 percentage points of gross bidirectional verdict instability**. These are engineering-scale observations, not a confidence interval or proof that nondeterminism caused a fixed share of the 5-point gap.
3. **Ambiguous dataset answers and evidence labels.** Manual full-history audits found that `6d550036` supports either 2 or 3 projects depending on the meaning of “led,” while `51a45a95` most plausibly intends `Target` without directly stating that the coupon was redeemed there. Both runs failed both questions, so these cases weaken absolute-accuracy interpretation but contribute zero to the paired 25-verdict gap. Two audited cases do not establish the dataset-wide defect rate.

The evidence supports a combined pipeline-performance difference; it cannot apportion the 25-verdict gap among model capability, model-induced extraction/retrieval changes, and judge variation. See the [analysis summary](eval_analysis/README.md), [full comparison](docs/hindsight_eval/longmemeval/20260-0822/summary.md), [nondeterminism study](docs/hindsight_eval/longmemeval/20260-0822/deepseek-non-deterimination.md), [gold-answer audits](docs/hindsight_eval/longmemeval/gold-answer-case-studies.md), and [redacted evidence capsule](eval_analysis/evidence/README.md).

## 2. Reproduction configuration

The run keeps the public AMB baseline, LongMemEval inputs, and Hindsight v0.4.17 target fixed while replacing the three external Gemini roles with DeepSeek.

| Component | Pin |
| --- | --- |
| Workload | LongMemEval cleaned `s` split; 500 questions; `rag`; non-Oracle |
| Dataset | [`xiaowu0162/longmemeval-cleaned`](https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/tree/98d7416c24c778c2fee6e6f3006e7a073259d48f) revision `98d7416c24c778c2fee6e6f3006e7a073259d48f`; 277,383,467 bytes; SHA-256 `d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442` |
| Published AMB base | [`decbb07f4f9899deac28a76293564cf263872652`](https://github.com/vectorize-io/agent-memory-benchmark/tree/decbb07f4f9899deac28a76293564cf263872652) |
| DeepSeek configuration | [`9880574ac3a22caf0324ebc1c435e60211f1e256`](https://github.com/rocke2020/agent-memory-benchmark/commit/9880574ac3a22caf0324ebc1c435e60211f1e256), based on the published commit above |
| Hindsight | `hindsight-all==0.4.17`, `hindsight-embed==0.4.17`, runtime `hindsight-api@0.4.17`; source [`2191654b1f9b454703916612fec57ce226c7746b`](https://github.com/vectorize-io/hindsight/tree/2191654b1f9b454703916612fec57ce226c7746b) |
| Recorded result | `outputs/longmemeval/hindsight-deepseek/rag/s.json`; SHA-256 `4e94268f30bda9dedf45853c37b1aa9b4385c6e8a4121f2eb3141e77c1d4fb23` |

### Models used

| Role | Model |
| --- | --- |
| Hindsight extraction and AMB judging | OpenAI-compatible `deepseek-v4-flash-0731`; requested alias `deepseek-v4-flash` |
| Final-answer generation | OpenAI-compatible `deepseek-v4-pro-0813`; requested alias `deepseek-v4-pro` |
| Hindsight embedding | Local `BAAI/bge-small-en-v1.5`, 384 dimensions |
| Hindsight reranking | Local `cross-encoder/ms-marco-MiniLM-L-6-v2` |

The external calls used default DeepSeek thinking at effort `high`. The caller sent `temperature=0` without an explicit thinking setting, which did not make the pipeline deterministic. Exact cached Hugging Face revisions for the two local models were not preserved.

The 448/500 artifact was assembled across resumptions and does not embed each invocation's Git SHA. The configuration above is the documented public rerun pin, not a claim of byte-identical source provenance for every historical invocation.

## 3. Run the reproduction

The full evaluation makes paid model calls and recreates same-named benchmark banks. Follow the [complete reproduction guide](docs/hindsight_eval/longmemeval/20260-0822/94.6-reproduction.md): use a fresh disposable Hindsight environment, check out the pinned DeepSeek commit, install with `uv sync --frozen`, download and hash-check the pinned dataset, configure the model roles above in an uncommitted `.env`, and pass the dry, limited, and Oracle gates before starting all 500 questions.

```bash
uv run omb run \
  --dataset longmemeval \
  --split s \
  --memory hindsight \
  --mode rag \
  --name hindsight-deepseek
```

These steps reproduce the documented configuration, not a guaranteed 448/500 score or identical verdict vector. Every new result must be validated independently.
