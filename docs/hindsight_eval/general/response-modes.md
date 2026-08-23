# AMB response modes with Hindsight

## Terms

These terms separate the benchmark runner, Hindsight operations, and model-owned answering.

| Term | Meaning |
|---|---|
| **AMB** | Agent Memory Benchmark, the evaluation runner in this repository. |
| **Response mode** | The per-question strategy AMB uses after the runner has prepared and ingested the memory provider. |
| **Retain** | Hindsight's ingestion operation. It turns dataset documents into stored memory before questions are answered. |
| **Recall** | Hindsight's retrieval operation. It searches stored memory and returns evidence for a query. |
| **Reflect** | Hindsight's native answer operation. It returns answer text rather than only retrieved evidence. |
| **AMB answer LLM** | The model selected for benchmark answer generation. It is separate from Hindsight's internal extraction model and AMB's judge model. |
| **Tool loop** | A model-controlled sequence in which the model may call a declared tool, inspect its result, and decide whether to call it again. |

> **TL;DR:** `rag`, the default, performs one Hindsight recall for each question and sends the result to the AMB answer LLM. `agentic-rag` is intended to let that LLM issue zero, one, or multiple ordinary recall calls with model-written queries, aggregate their evidence, and then make a separate AMB final-answer call; it never calls Hindsight reflect. `agent` delegates the question to Hindsight reflect through `direct_answer`. Retain is the runner's shared ingestion stage for all three modes, not an operation selected by a response mode.

## Mode matrix

The modes differ only in how they answer each question after common ingestion. They do not select different retain behavior.

| Mode | Default | Hindsight operation per question | Who chooses the retrieval query? | Who produces the evaluated answer? | Uses Hindsight reflect? |
|---|---:|---|---|---|---:|
| `rag` | Yes | One `recall` | AMB uses `meta.retrieval_query` when present, otherwise the original question. LongMemEval supplies no alternate retrieval query, so it uses the original question. | The AMB answer LLM receives the recalled context. | No |
| `agentic-rag` | No | Zero, one, or multiple ordinary `recall` tool calls | The AMB answer LLM writes each search query while seeing the original question and prior tool results. | The same AMB answer LLM is called again over the aggregated recall context. | No |
| `agent` | No | One `direct_answer` call, mapped by the Hindsight adapter to `reflect` | Hindsight owns the native reflect process. | Hindsight returns the answer; AMB does not make a separate answer-LLM call. | Yes |

The CLI default and complete three-mode registry are in [`cli.py`](../../../src/memory_bench/cli.py#L30) and [`modes/__init__.py`](../../../src/memory_bench/modes/__init__.py#L7).

## Retain is shared ingestion

The runner ingests dataset documents before it invokes any response mode. Selecting `rag`, `agentic-rag`, or `agent` therefore changes the question-answering path, not whether the source conversations are retained.

For LongMemEval, each question is an isolation unit with its own Hindsight bank. The runner starts that unit's `async_ingest`, waits for that unit's ingestion to finish, and only then answers its question. The embedded Hindsight adapter translates this shared ingest call into ordered `aretain_batch(..., retain_async=False)` operations. The runner may prefetch other banks, but a question is not answered before its own bank is ready.

```text
LongMemEval conversation sessions
  -> AMB Documents
  -> runner async_ingest
  -> Hindsight aretain_batch and memory extraction
  -> selected response mode
  -> AMB judge
```

`--skip-ingestion` and resume behavior can deliberately reuse existing state, but that is a runner option rather than a response-mode property. See the [runner's unit ingestion and answer barrier](../../../src/memory_bench/runner.py#L216) and the [Hindsight ordered retain implementation](../../../src/memory_bench/memory/hindsight.py#L548).

## End-to-end response flows

`rag` retrieves once, `agentic-rag` lets a model drive repeated retrieval, and `agent` hands answering to Hindsight. All three then return an `AnswerResult` to the same runner-level judging path.

### `rag`

RAG makes exactly one mode-level retrieval call per question, then asks the AMB answer LLM to synthesize the answer.

```text
original question
  -> memory.async_retrieve(original question)
  -> Hindsight arecall
  -> recalled Documents plus raw Hindsight response
  -> LongMemEval answer prompt
  -> AMB answer LLM
  -> generated answer
  -> AMB judge
```

For LongMemEval, the dataset-specific prompt receives the original question, question date, and raw Hindsight recall response. The implementation is in [`rag.py`](../../../src/memory_bench/modes/rag.py#L44), with Hindsight recall mapped in [`hindsight.py`](../../../src/memory_bench/memory/hindsight.py#L655).

### `agentic-rag`

Agentic RAG is intended to use recall as an LLM tool. Each tool invocation is still an ordinary Hindsight recall, and no step calls reflect.

```text
original question
  -> AMB answer LLM tool loop
       -> optional recall(query 1) -> Hindsight arecall -> evidence 1
       -> optional recall(query 2) -> Hindsight arecall -> evidence 2
       -> optional further recall calls
       -> tool-loop text response is discarded
  -> concatenate evidence from every recall call
  -> separate AMB answer-LLM generation call
  -> generated answer
  -> AMB judge
```

The recall results are appended in call order with separators. The mode does not deduplicate evidence across calls, and it discards each raw Hindsight response. It also discards the tool loop's own final text, so the evaluated answer comes only from the separate final generation call. See [`agentic_rag.py`](../../../src/memory_bench/modes/agentic_rag.py#L38) and the [Gemini tool loop](../../../src/memory_bench/llm/gemini.py#L79).

### `agent`

Agent mode bypasses AMB's recall-to-answer pipeline and calls the memory provider's native answer interface. For Hindsight, that interface maps directly to reflect.

```text
original question
  -> memory.async_direct_answer
  -> Hindsight reflect
  -> Hindsight answer returned as both answer and context
  -> AMB judge
```

AMB records no separate answer-model reasoning for this mode. The mode dispatch is in [`agent.py`](../../../src/memory_bench/modes/agent.py#L16), and the Hindsight mapping is in [`hindsight.py`](../../../src/memory_bench/memory/hindsight.py#L470).

## What "multiple recall calls" means

It means dynamic query planning by the answer model, not a deterministic AMB decomposition algorithm. The only fixed input to the tool loop is the original question plus instructions that recall may be called more than once.

Given an original question such as "Where did I travel after the Paris conference, and who joined me?", the model could choose queries such as:

1. `Paris conference travel`
2. `trip immediately after Paris conference`
3. `who joined the post-conference trip`

Those calls are examples, not a required decomposition. Depending on the model's decisions and earlier results, it may:

- reuse the original question unchanged;
- paraphrase it for retrieval;
- split it into subquestions;
- issue a follow-up query based on earlier recalled evidence;
- stop after one call; or
- return text without calling recall, leaving the aggregated context empty.

The generic tool-loop contract permits zero or more calls and has a default budget of 10. The Gemini implementation executes every function call emitted in one model turn before checking that budget again, so 10 is a loop budget rather than a strict per-call ceiling when one turn contains several calls.

## Current checkout limitations

The intended flows above are source-defined, but `agentic-rag` and CLI-selected `agent` are not runnable as currently wired. These are source-level and constructor-probe findings; no benchmark or external model call was run for this document.

1. `AgenticRAGMode` constructs `RAGMode(llm=self._llm, k=k)`, but `RAGMode.__init__` accepts only `llm`. A read-only constructor probe returned:

   ```text
   agentic-rag: TypeError: RAGMode.__init__() got an unexpected keyword argument 'k'
   ```

2. The CLI mode factory checks `cls.__init__.__code__` before construction. `AgentMode` inherits `object.__init__`, which has no `__code__`; the same probe returned:

   ```text
   agent-factory: AttributeError: 'wrapper_descriptor' object has no attribute '__code__'
   ```

3. After the constructor mismatch is fixed, `agentic-rag` still needs an answer adapter that implements `tool_loop`. In this checkout, [`GeminiLLM`](../../../src/memory_bench/llm/gemini.py#L79) implements it, while the OpenAI and Groq adapters inherit the base `NotImplementedError` from [`llm/base.py`](../../../src/memory_bench/llm/base.py#L42).

4. Agentic RAG's separate final call does not forward runner metadata or the LongMemEval prompt builder. It therefore uses the generic RAG prompt over formatted documents rather than LongMemEval's raw-response-aware prompt. This differs from normal `rag` even when both use the same answer model.

These limitations document the current checkout only. They do not change the distinction among retain, recall, and reflect, and this document does not propose or implement fixes.

For the concrete five-role model stack used by the completed DeepSeek LongMemEval `rag` evaluation, see the [LongMemEval model inventory](../longmemeval/20260-0822/summary.md#6-complete-model-inventory-and-runtime-versions).

## Authoritative source map

The behavior above is owned by a small set of files, so future changes should be checked across the complete runner-to-provider path rather than in one registry alone.

| Responsibility | Source |
|---|---|
| CLI default and mode construction | [`src/memory_bench/cli.py`](../../../src/memory_bench/cli.py#L21) and [`src/memory_bench/modes/__init__.py`](../../../src/memory_bench/modes/__init__.py#L7) |
| Shared ingestion, per-question dispatch, and judging | [`src/memory_bench/runner.py`](../../../src/memory_bench/runner.py#L51) |
| One-recall RAG flow | [`src/memory_bench/modes/rag.py`](../../../src/memory_bench/modes/rag.py#L29) |
| Model-directed recall tool flow | [`src/memory_bench/modes/agentic_rag.py`](../../../src/memory_bench/modes/agentic_rag.py#L22) |
| Provider-native answer flow | [`src/memory_bench/modes/agent.py`](../../../src/memory_bench/modes/agent.py#L9) |
| Hindsight retain, recall, and reflect mappings | [`src/memory_bench/memory/hindsight.py`](../../../src/memory_bench/memory/hindsight.py#L221) |
| Tool-loop contract and Gemini implementation | [`src/memory_bench/llm/base.py`](../../../src/memory_bench/llm/base.py#L42) and [`src/memory_bench/llm/gemini.py`](../../../src/memory_bench/llm/gemini.py#L79) |
| LongMemEval answer prompt and isolation unit | [`src/memory_bench/dataset/longmemeval.py`](../../../src/memory_bench/dataset/longmemeval.py#L55) |
