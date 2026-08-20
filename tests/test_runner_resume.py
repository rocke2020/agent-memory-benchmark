import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from memory_bench.dataset.base import Dataset
from memory_bench.memory.base import MemoryProvider
from memory_bench.models import AnswerResult, Document, EvalSummary, Query, QueryResult
from memory_bench.modes.base import ResponseMode
from memory_bench.runner import EvalRunner


class _IsolatedDataset(Dataset):
    name = "resume-fixture"
    description = "Runner resume fixture"
    splits = ["s"]
    task_type = "mcq"
    isolation_unit = "question"

    def __init__(self):
        self._queries = [
            Query(
                id="q1",
                query="first question",
                gold_ids=[],
                gold_answers=["a"],
                user_id="unit-a",
            ),
            Query(
                id="q2",
                query="second question",
                gold_ids=[],
                gold_answers=["a"],
                user_id="unit-b",
            ),
        ]
        self._documents = [
            Document(id="d1", content="first context", user_id="unit-a"),
            Document(id="d2", content="second context", user_id="unit-b"),
        ]

    def load_queries(self, split, category=None, limit=None):
        return self._queries[:limit]

    def load_documents(
        self, split, category=None, limit=None, ids=None, user_ids=None
    ):
        documents = self._documents
        if ids is not None:
            documents = [document for document in documents if document.id in ids]
        if user_ids is not None:
            documents = [
                document for document in documents if document.user_id in user_ids
            ]
        return documents[:limit]


class _RecordingMemory(MemoryProvider):
    name = "recording"
    description = "Records ingested units"
    kind = "local"

    def __init__(self):
        self.ingested_unit_ids = []

    def ingest(self, documents):
        self.ingested_unit_ids.extend(document.user_id for document in documents)

    async def async_ingest(self, documents):
        self.ingest(documents)

    def retrieve(self, query, k=10, user_id=None, query_timestamp=None):
        return [Document(id="retrieved", content=f"context for {user_id}")], None


class _StaticMode(ResponseMode):
    name = "rag"
    description = "Returns a fixed multiple-choice answer"

    def __init__(self, fail_on_query=None):
        self.answered_queries = []
        self._fail_on_query = fail_on_query

    @property
    def llm_id(self):
        return "test:answer"

    def answer(self, query, memory, task_type="open", user_id=None):
        self.answered_queries.append(query)
        if query == self._fail_on_query:
            raise RuntimeError("simulated interruption")
        return AnswerResult(
            answer="a",
            reasoning="fixture",
            context=f"context for {user_id}",
            retrieve_time_ms=0.0,
        )

    def answer_from_context(self, query, context, task_type="open"):
        raise AssertionError("resume fixture should not use cached context mode")


class _StaticJudge:
    def __init__(self):
        self._llm = SimpleNamespace(model_id="test:judge")


class _ThreeQuestionDataset(_IsolatedDataset):
    def __init__(self):
        super().__init__()
        self._queries.append(
            Query(
                id="q3",
                query="third question",
                gold_ids=[],
                gold_answers=["a"],
                user_id="unit-c",
            )
        )
        self._documents.append(
            Document(id="d3", content="third context", user_id="unit-c")
        )


def _new_runner(output_dir: Path):
    runner = object.__new__(EvalRunner)
    runner.output_dir = output_dir
    runner._judge = _StaticJudge()
    return runner


def _completed_result(query_id="q1", query="first question"):
    return QueryResult(
        query_id=query_id,
        query=query,
        answer="cached answer",
        reasoning="cached reasoning",
        context="cached context",
        context_tokens=2,
        retrieve_time_ms=1.0,
        gold_answers=["a"],
        correct=True,
        judge_reason="cached judge",
        meta={},
    )


def _seed_partial_result(runner, dataset, memory, mode):
    completed_result = _completed_result()
    runner._save(
        EvalSummary(
            dataset=dataset.name,
            split="s",
            category=None,
            memory_provider=memory.name,
            run_name="resume-run",
            mode=mode.name,
            oracle=False,
            total_queries=1,
            correct=1,
            accuracy=1.0,
            ingestion_time_ms=1.0,
            ingested_docs=1,
            answer_llm=mode.llm_id,
            judge_llm="test:judge",
            results=[completed_result],
        )
    )


def test_skip_ingested_uses_current_query_unit_for_legacy_results(tmp_path: Path):
    """A missing Query.user_id copy in result meta must not re-run a completed unit."""
    dataset = _IsolatedDataset()
    memory = _RecordingMemory()
    mode = _StaticMode()
    runner = _new_runner(tmp_path)
    _seed_partial_result(runner, dataset, memory, mode)

    summary = runner.run(
        dataset=dataset,
        split="s",
        memory=memory,
        mode=mode,
        skip_ingested=True,
        run_name="resume-run",
    )

    assert memory.ingested_unit_ids == ["unit-b"]
    assert mode.answered_queries == ["second question"]
    assert [result.query_id for result in summary.results] == ["q1", "q2"]
    assert summary.results[0].answer == "cached answer"

    saved = json.loads(
        (tmp_path / dataset.name / "resume-run" / mode.name / "s.json").read_text()
    )
    assert [result["query_id"] for result in saved["results"]] == ["q1", "q2"]
    assert saved["results"][0]["answer"] == "cached answer"


def test_skip_ingested_can_resume_the_same_run_twice(tmp_path: Path):
    dataset = _ThreeQuestionDataset()
    first_resume_memory = _RecordingMemory()
    first_resume_mode = _StaticMode(fail_on_query="third question")
    runner = _new_runner(tmp_path)
    _seed_partial_result(
        runner, dataset, first_resume_memory, first_resume_mode
    )

    with pytest.raises(RuntimeError, match="simulated interruption"):
        runner.run(
            dataset=dataset,
            split="s",
            memory=first_resume_memory,
            mode=first_resume_mode,
            skip_ingested=True,
            run_name="resume-run",
        )

    partial_path = (
        tmp_path / dataset.name / "resume-run" / first_resume_mode.name / "s.json"
    )
    first_resume_output = json.loads(partial_path.read_text())
    assert [result["query_id"] for result in first_resume_output["results"]] == [
        "q1",
        "q2",
    ]

    second_resume_memory = _RecordingMemory()
    second_resume_mode = _StaticMode()
    summary = runner.run(
        dataset=dataset,
        split="s",
        memory=second_resume_memory,
        mode=second_resume_mode,
        skip_ingested=True,
        run_name="resume-run",
    )

    assert second_resume_memory.ingested_unit_ids == ["unit-c"]
    assert second_resume_mode.answered_queries == ["third question"]
    assert [result.query_id for result in summary.results] == ["q1", "q2", "q3"]
    assert summary.results[0].answer == "cached answer"
