# 使用 DeepSeek V4 复现 Hindsight LongMemEval

[English](README.md) | 简体中文

这个分支专注于使用 **`hindsight-api@0.4.17`** 和 DeepSeek 模型栈复现 Hindsight 已发布的 LongMemEval `s` split 评测。记忆提取和答案判定实际使用的 Flash 型号是 **`deepseek-v4-flash-0731`**；最终答案由 DeepSeek V4 Pro 0813 生成。

## 1. 与官方结果的配对比较

我的运行评测了与官方结果相同的 500 个 query ID、问题、gold answer 和 metadata，得到 **448/500（89.6%）**；Hindsight 官方结果为 **473/500（94.6%）**。

| 结果 | 正确数 | 准确率 | 提取 / 回答 / 判定模型 |
| --- | ---: | ---: | --- |
| [Hindsight 官方结果](https://agentmemorybenchmark.ai/run/outputs%2Flongmemeval%2Fhindsight%2Frag%2Fs.json.gz?id=e47becba) | 473/500 | 94.6% | Gemini 2.5 Flash Lite / Gemini 3.1 Pro Preview / Gemini 2.5 Flash Lite |
| 我的 DeepSeek V4 结果 | 448/500 | 89.6% | Flash 0731 / Pro 0813 / Flash 0731 |
| 差异 | -25 | -5.0 个百分点 | 完整流水线差异，不是仅替换一个模型的 A/B 测试 |

| 配对判定 | 问题数 |
| --- | ---: |
| 两边都通过 | 434 |
| 官方通过、我的失败 | 39 |
| 官方失败、我的通过 | 14 |
| 两边都失败 | 13 |

差距主要集中在 multi-session（`-14`）和 temporal-reasoning（`-7`）问题。三个不完整的 Hindsight retain 单元后来在隔离的新 bank 中从头重跑，三题仍然全部正确，因此 retain-complete 修复结果仍为 448/500。

### 为什么我的准确率低于官方结果？

1. **外部 LLM 栈不同。** 官方运行使用 Gemini 完成提取、回答和判定；我的运行使用 Flash 0731、Pro 0813 和 Flash 0731。DeepSeek 模型栈能力较弱或行为不同是最主要的候选解释，尤其是提取和判定都使用了 Flash，但这不是隔离后的因果证明：三个 LLM 角色同时发生变化，而且 500 题中没有任何一题的保存 context 完全相同。在我的 52 道错题中，49 道的 raw chunk 仍包含官方标注的全部答案 session，因此大多数失败不能简单归因于遗漏 gold session。
2. **完整流水线存在实测非确定性。** 五道错题的 pilot 加上 55 次新的完整流水线重跑产生了 8 次恢复、1 次退化和 9 次 verdict flip。对干净的 458 题分析范围进行总体加权后，估计净分数漂移约为 **2.04 个百分点**，双向 verdict 总波动约为 **4.66 个百分点**。这些是工程尺度观测，不是置信区间，也不能证明非确定性导致了 5 个百分点差距中的固定比例。
3. **部分数据集答案或证据标签含糊。** 对完整历史的人工审查发现，`6d550036` 根据 “led” 的含义可支持 2 或 3 个项目；`51a45a95` 最可能意指 `Target`，但上下文从未直接说明优惠券在那里兑换。官方和我的运行都在这两题失败，所以它们会削弱绝对准确率的解释力，却没有贡献 25 个 paired verdict 差距。两个人工审查案例不足以推断整个数据集的问题比例。

现有证据支持“完整流水线性能差异”，但不能把 25 个 verdict 的差距拆分为模型能力、模型输出引起的提取/检索变化和 judge 波动。详情见[分析摘要](eval_analysis/README.md)、[完整比较](docs/hindsight_eval/longmemeval/20260-0822/summary.md)、[非确定性实验](docs/hindsight_eval/longmemeval/20260-0822/deepseek-non-deterimination.md)、[gold answer 人工审查](docs/hindsight_eval/longmemeval/gold-answer-case-studies.md)和[脱敏证据包](eval_analysis/evidence/README.md)。

## 2. 复现配置

本次运行保持公开 AMB 基线、LongMemEval 输入和 Hindsight v0.4.17 目标不变，仅把三个外部 Gemini 角色替换为 DeepSeek。

| 组件 | 固定值 |
| --- | --- |
| 工作负载 | LongMemEval cleaned `s` split；500 题；`rag`；非 Oracle |
| 数据集 | [`xiaowu0162/longmemeval-cleaned`](https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/tree/98d7416c24c778c2fee6e6f3006e7a073259d48f) revision `98d7416c24c778c2fee6e6f3006e7a073259d48f`；277,383,467 bytes；SHA-256 `d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442` |
| 已发布 AMB 基线 | [`decbb07f4f9899deac28a76293564cf263872652`](https://github.com/vectorize-io/agent-memory-benchmark/tree/decbb07f4f9899deac28a76293564cf263872652) |
| DeepSeek 配置 | [`9880574ac3a22caf0324ebc1c435e60211f1e256`](https://github.com/rocke2020/agent-memory-benchmark/commit/9880574ac3a22caf0324ebc1c435e60211f1e256)，以以上已发布 commit 为基线 |
| Hindsight | `hindsight-all==0.4.17`、`hindsight-embed==0.4.17`、运行时 `hindsight-api@0.4.17`；源码 [`2191654b1f9b454703916612fec57ce226c7746b`](https://github.com/vectorize-io/hindsight/tree/2191654b1f9b454703916612fec57ce226c7746b) |
| 已记录结果 | `outputs/longmemeval/hindsight-deepseek/rag/s.json`；SHA-256 `4e94268f30bda9dedf45853c37b1aa9b4385c6e8a4121f2eb3141e77c1d4fb23` |

### 使用的模型

| 角色 | 模型 |
| --- | --- |
| Hindsight 提取和 AMB 判定 | OpenAI-compatible `deepseek-v4-flash-0731`；请求 alias 为 `deepseek-v4-flash` |
| 最终答案生成 | OpenAI-compatible `deepseek-v4-pro-0813`；请求 alias 为 `deepseek-v4-pro` |
| Hindsight embedding | 本地 `BAAI/bge-small-en-v1.5`，384 dimensions |
| Hindsight reranking | 本地 `cross-encoder/ms-marco-MiniLM-L-6-v2` |

外部调用使用 DeepSeek 默认 thinking，effort 为 `high`。调用方发送了 `temperature=0`，但没有显式设置 thinking，因此流水线仍不具备确定性。两个本地模型的精确 Hugging Face cache revision 未被保存。

448/500 工件由多次 resume 组成，结果文件没有嵌入每次运行的 Git SHA。以上配置是公开复现所固定的配置，不代表每次历史运行都具有逐字节一致的源码 provenance。

## 3. 运行复现

完整评测会产生付费模型调用，并重新创建同名 benchmark bank。请遵循[完整复现指南](docs/hindsight_eval/longmemeval/20260-0822/94.6-reproduction.md)：使用不含重要数据的全新一次性 Hindsight 环境，检出已固定的 DeepSeek commit，执行 `uv sync --frozen`，下载并校验已固定数据集的 hash，在不提交的 `.env` 中配置上表所列模型角色，并在启动全部 500 题之前通过 dry、limited 和 Oracle gate。

```bash
uv run omb run \
  --dataset longmemeval \
  --split s \
  --memory hindsight \
  --mode rag \
  --name hindsight-deepseek
```

这些步骤复现的是已记录配置，不保证再次得到 448/500 或完全相同的 verdict vector。每次新结果都必须独立验证。

## 4. 如何阅读本复现的报告

本复现的文档是分层组织的，每一层回答不同的问题，建议按以下顺序阅读：

| 顺序 | 文档 | 回答的问题 |
| --- | --- | --- |
| 1 | [复现总结](docs/hindsight_eval/longmemeval/20260-0822/summary.md) | 448/500 的核心结果、与官方结果的配对比较、"Ctx tokens" 和 "indexing tokens" 的真实含义、5 个百分点差距的归因边界，以及可复用的分析工件 |
| 2 | [DeepSeek 非确定性实验](docs/hindsight_eval/longmemeval/20260-0822/deepseek-non-deterimination.md) | 2.04 个百分点净漂移和 4.66 个百分点双向波动估计是如何得到的，以及单次运行何时能可靠比较两个 memory 系统 |
| 3 | [分析总结与工具](eval_analysis/README.md) | 离线官方报告 UI、配对比较与修复工具、三个 retain 批次被跳过的根因，以及本地测试套件 |
| 4 | [gold answer 人工审查](docs/hindsight_eval/longmemeval/gold-answer-case-studies.md) | 对 `6d550036`、`51a45a95` 等含糊数据集 gold answer 的人工审查 |
| 5 | [模型与 token 用量总结](docs/hindsight_eval/longmemeval/20260-0822/model-token-summary.md) | 各模型角色的实测 token 分段及其证据边界 |
| 6 | [脱敏证据包](eval_analysis/evidence/README.md) | 带哈希校验的紧凑文件，无需大型付费运行工件即可在干净 checkout 上审计分数、配对 verdict 和 pilot 结论 |
| 7 | [完整复现指南](docs/hindsight_eval/longmemeval/20260-0822/94.6-reproduction.md) | 从头重跑整个评测的分步说明 |

如果要交互式地查看结果本身，可以通过离线的官方 AMB 报告 UI 在本地提供结果（仅监听 loopback）：

```bash
uv run python eval_analysis/amb_report.py serve \
  outputs/longmemeval/hindsight-deepseek/rag/s.json \
  --host 127.0.0.1 \
  --port 7979
```

所提供的页面布局、计算、过滤和题目下钻均为官方实现，仅入口 HTML 为离线使用做了适配。命令行阅读可使用 `eval_analysis/amb_report.py inspect` 和 `compare` 复现逐题摘要和与官方结果的配对比较；[脱敏证据包](eval_analysis/evidence/README.md)还列出了 `shasum` / `jq` 校验命令，仅凭已提交的脱敏文件即可复核配对 verdict 计数。
