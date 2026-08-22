#!/usr/bin/env python3
"""Run the frozen 55-question DeepSeek nondeterminism study safely."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import subprocess
import tempfile
import uuid
from collections import defaultdict
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from typing import Any

from amb_report import load_amb_result
from hindsight_repair import (
    DEFAULT_SOURCE,
    EXPECTED_HINDSIGHT_EMBED_VERSION,
    RepairHindsightMemoryProvider,
    _identifier_set_sha256,
    _load_environment,
    _redacted_runtime_config,
    _repair_profile,
    _repair_profile_paths,
    _sha256,
    _slug,
    _validate_expected_runtime_configuration,
)
from variance_probe import (
    EXPECTED_RESOLVED_MODEL_BY_ROLE,
    SelectedLongMemEvalDataset,
    _load_jsonl,
    _json_sha256,
    _longmemeval_prompt_hashes,
    _normalized_answer_sha256,
    _source_document_ids_sha256,
    _text_sha256,
    _trace_omb_completions,
    close_and_verify_hindsight_daemon,
    disable_opaque_openai_retries,
    expected_retain_batch_count,
    summarize_completion_events,
)
from variance_trace import (
    append_jsonl,
    bind_completion_scope,
    canonical_json_sha256,
)
from memory_bench.runner import EvalRunner

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STUDY_ID = "deepseek-nondeterminism-20260822-55a"
DEFAULT_ANALYSIS_ROOT = Path(__file__).resolve().parent / "nondeterminism-results"
DEFAULT_REFERENCE = REPOSITORY_ROOT / "outputs/longmemeval/hindsight/rag/s.json.gz"
DEFAULT_PILOT_SUMMARY = Path(__file__).resolve().parent / "evidence/pilot5-20260822-b.json"
DEFAULT_RESUME_LOG = (
    REPOSITORY_ROOT
    / "run-artifacts/2028-0819->0822/longmemeval-hindsight-deepseek-resume-1.log"
)

EXPECTED_CANDIDATE_SHA256 = "4e94268f30bda9dedf45853c37b1aa9b4385c6e8a4121f2eb3141e77c1d4fb23"
EXPECTED_REFERENCE_GZIP_SHA256 = "2025b1def4794861ba768730d2090816c6a51425b46d29545762db554c3818ca"
EXPECTED_PILOT_SHA256 = "771ccdbf5b9cbde7f2a587bf60d4123f2e0909fe0ea01ebe4fdf948ec2514ba3"
EXPECTED_RESUME_LOG_SHA256 = "052450343a16bf61ef3b2cb1ebded489a7e09b4e02b66bf26c73ca76b86762d6"
EXPECTED_DATASET_SHA256 = "d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442"
EXPECTED_SELECTION_MANIFEST_SHA256 = "572a3a774c8e4734fb017f882c031f8299c6db08b1f623e76946c7b5eaa3a482"
EXPECTED_NEW_QUERY_IDS_SHA256 = "ff003d654962f23729d70b64106e812babdd3eb5504ffa4369dd567b53589653"

PILOT_QUERY_IDS = (
    "0ddfec37_abs",
    "0a995998",
    "15745da0",
    "b0479f84",
    "gpt4_f420262d",
)
PILOT_VERDICTS = {
    "0ddfec37_abs": (True, True, True),
    "0a995998": (False, False, False),
    "15745da0": (True, True, True),
    "b0479f84": (False, False, True),
    "gpt4_f420262d": (False, False, False),
}
REPAIR_QUERY_IDS = ("4f54b7c9", "gpt4_2312f94c", "gpt4_78cf46a3")
GOLD_AMBIGUOUS_QUERY_IDS = ("6d550036",)
QUESTION_TYPE_QUOTAS = {
    "knowledge-update": 2,
    "multi-session": 15,
    "single-session-assistant": 1,
    "single-session-preference": 1,
    "single-session-user": 3,
    "temporal-reasoning": 8,
}
POPULATION_BY_TYPE = {
    "knowledge-update": {"errors": 3, "correct": 75},
    "multi-session": {"errors": 21, "correct": 90},
    "single-session-assistant": {"errors": 2, "correct": 54},
    "single-session-preference": {"errors": 2, "correct": 10},
    "single-session-user": {"errors": 4, "correct": 66},
    "temporal-reasoning": {"errors": 11, "correct": 120},
}
ANALYSIS_FRAME = {
    "questions": 458,
    "original_errors": 43,
    "original_correct": 415,
    "observed_gap_verdicts": 25,
}
EXPECTED_NEW_ERROR_COUNT = 25
EXPECTED_CONTROL_COUNT = 30
EXPECTED_NEW_RUN_COUNT = EXPECTED_NEW_ERROR_COUNT + EXPECTED_CONTROL_COUNT
EXPECTED_DOCUMENT_COUNT = 2654
EXPECTED_RETAIN_BATCH_COUNT = 355
PREFLIGHT_MAX_AGE_SECONDS = 15 * 60
CODE_PATHS = (
    "eval_analysis/deepseek_nondeterminism.py",
    "eval_analysis/hindsight_repair.py",
    "eval_analysis/hindsight_repair_daemon.py",
    "eval_analysis/variance_probe.py",
    "eval_analysis/variance_trace.py",
    "src/memory_bench/runner.py",
    "src/memory_bench/llm/openai.py",
    "uv.lock",
)


def write_json_atomic_exclusive(path: Path, payload: Any) -> None:
    encoded = _json_file_text(payload)
    _write_text_atomic_exclusive(path, encoded)


def _json_file_text(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _json_file_sha256(payload: Any) -> str:
    return hashlib.sha256(_json_file_text(payload).encode()).hexdigest()


def _write_text_atomic_exclusive(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _result_by_query_id(artifact: dict[str, Any]) -> dict[str, dict[str, Any]]:
    results = artifact.get("results")
    if not isinstance(results, list):
        raise ValueError("artifact must contain a results list")
    by_id = {}
    for result in results:
        if not isinstance(result, dict) or not isinstance(result.get("query_id"), str):
            raise ValueError("every result must contain a query_id")
        query_id = result["query_id"]
        if query_id in by_id:
            raise ValueError(f"duplicate query ID: {query_id}")
        by_id[query_id] = result
    return by_id


def _question_family(query_id: str) -> str:
    return query_id.removesuffix("_abs")


def _selection_rank(study_id: str, cohort: str, query_id: str) -> tuple[str, str]:
    digest = hashlib.sha256(f"{study_id}\0{cohort}\0{query_id}".encode()).hexdigest()
    return digest, query_id


def select_questions(
    candidate: dict[str, Any],
    *,
    study_id: str,
    pilot_query_ids: tuple[str, ...],
    excluded_query_ids: set[str],
    quotas: dict[str, int],
) -> dict[str, list[str]]:
    by_id = _result_by_query_id(candidate)
    if len(set(pilot_query_ids)) != len(pilot_query_ids):
        raise ValueError("pilot query IDs must be unique")
    for query_id in pilot_query_ids:
        result = by_id.get(query_id)
        if result is None or result.get("correct") is not False:
            raise ValueError(f"pilot query must remain an original error: {query_id}")

    errors_by_type: dict[str, list[str]] = defaultdict(list)
    correct_by_type: dict[str, list[str]] = defaultdict(list)
    for query_id, result in by_id.items():
        if query_id in excluded_query_ids:
            continue
        question_type = (result.get("meta") or {}).get("question_type")
        if question_type not in quotas:
            continue
        target = correct_by_type if result.get("correct") is True else errors_by_type
        target[question_type].append(query_id)

    new_error_query_ids = []
    selected_error_query_ids = set(pilot_query_ids)
    for question_type, quota in quotas.items():
        fixed = [
            query_id
            for query_id in pilot_query_ids
            if (by_id[query_id].get("meta") or {}).get("question_type")
            == question_type
        ]
        needed = quota - len(fixed)
        if needed < 0:
            raise ValueError(f"pilot exceeds quota for {question_type}")
        candidates = [
            query_id
            for query_id in errors_by_type[question_type]
            if query_id not in selected_error_query_ids
        ]
        picked = sorted(
            candidates,
            key=lambda query_id: _selection_rank(study_id, "error", query_id),
        )[:needed]
        if len(picked) != needed:
            raise ValueError(f"not enough eligible errors for {question_type}")
        new_error_query_ids.extend(picked)
        selected_error_query_ids.update(picked)

    selected_families = {
        _question_family(query_id) for query_id in selected_error_query_ids
    }
    control_query_ids = []
    for question_type, quota in quotas.items():
        candidates = [
            query_id
            for query_id in correct_by_type[question_type]
            if _question_family(query_id) not in selected_families
        ]
        picked = sorted(
            candidates,
            key=lambda query_id: _selection_rank(study_id, "control", query_id),
        )[:quota]
        if len(picked) != quota:
            raise ValueError(f"not enough eligible controls for {question_type}")
        control_query_ids.extend(picked)

    return {
        "pilot_query_ids": list(pilot_query_ids),
        "new_error_query_ids": new_error_query_ids,
        "control_query_ids": control_query_ids,
    }


def selection_manifest_sha256(manifest: dict[str, Any]) -> str:
    return canonical_json_sha256(manifest)


def new_query_ids_sha256(manifest: dict[str, Any]) -> str:
    query_ids = manifest["new_error_query_ids"] + manifest["control_query_ids"]
    return hashlib.sha256("\n".join(sorted(query_ids)).encode()).hexdigest()


def build_selection_manifest(
    candidate: dict[str, Any],
    reference: dict[str, Any],
    *,
    study_id: str,
    pilot_query_ids: tuple[str, ...],
    excluded_query_ids: set[str],
    quotas: dict[str, int],
    analysis_frame: dict[str, int],
    exclusions: dict[str, Any],
    expected_manifest_sha256: str | None = None,
) -> dict[str, Any]:
    candidate_by_id = _result_by_query_id(candidate)
    reference_by_id = _result_by_query_id(reference)
    if set(candidate_by_id) != set(reference_by_id):
        raise ValueError("candidate and reference query IDs differ")
    for query_id in candidate_by_id:
        if any(
            candidate_by_id[query_id].get(field)
            != reference_by_id[query_id].get(field)
            for field in ("query", "gold_answers", "meta")
        ):
            raise ValueError(f"candidate/reference input mismatch: {query_id}")

    frame_ids = set(candidate_by_id) - excluded_query_ids
    candidate_correct = sum(
        candidate_by_id[query_id].get("correct") is True for query_id in frame_ids
    )
    reference_correct = sum(
        reference_by_id[query_id].get("correct") is True for query_id in frame_ids
    )
    observed_frame = {
        "questions": len(frame_ids),
        "original_errors": len(frame_ids) - candidate_correct,
        "original_correct": candidate_correct,
        "observed_gap_verdicts": reference_correct - candidate_correct,
    }
    if observed_frame != analysis_frame:
        raise ValueError(
            f"analysis frame drifted: expected {analysis_frame!r}, got {observed_frame!r}"
        )

    selected = select_questions(
        candidate,
        study_id=study_id,
        pilot_query_ids=pilot_query_ids,
        excluded_query_ids=excluded_query_ids,
        quotas=quotas,
    )
    manifest = {
        "study_id": study_id,
        "analysis_frame": analysis_frame,
        **selected,
        "exclusions": exclusions,
    }
    actual_hash = selection_manifest_sha256(manifest)
    if expected_manifest_sha256 is not None and actual_hash != expected_manifest_sha256:
        raise ValueError(
            "selection manifest SHA-256 drifted: "
            f"expected {expected_manifest_sha256}, got {actual_hash}"
        )
    return manifest


def _result_payload(result: Any) -> dict[str, Any]:
    if is_dataclass(result):
        return asdict(result)
    if isinstance(result, dict):
        return result
    if hasattr(result, "__dict__"):
        return dict(vars(result))
    raise ValueError("checkpoint result must be a dataclass, dict, or object")


class JournaledEvalRunner(EvalRunner):
    """EvalRunner variant whose only checkpoints are immutable per-query files."""

    def __init__(
        self,
        *,
        output_dir: Path,
        selected_query_ids: tuple[str, ...],
        journal_dir: Path,
        initialize_judge: bool = True,
    ) -> None:
        if initialize_judge:
            super().__init__(output_dir=output_dir)
        else:
            self.output_dir = output_dir
            self._judge = None
        self._selected_query_ids = selected_query_ids
        self._journal_dir = journal_dir
        self._journal_index = {
            query_id: index
            for index, query_id in enumerate(selected_query_ids, start=1)
        }

    def _save(self, summary: Any) -> None:
        seen = set()
        for result in summary.results:
            payload = _result_payload(result)
            query_id = payload.get("query_id")
            if query_id not in self._journal_index:
                raise RuntimeError(f"runner produced an unselected query: {query_id}")
            if query_id in seen:
                raise RuntimeError(f"runner produced a duplicate query: {query_id}")
            seen.add(query_id)
            index = self._journal_index[query_id]
            checkpoint_path = self._journal_dir / f"{index:03d}-{query_id}.json"
            if checkpoint_path.exists():
                existing = json.loads(checkpoint_path.read_text())
                if existing != payload:
                    raise RuntimeError(
                        f"immutable checkpoint changed for query {query_id}"
                    )
                continue
            write_json_atomic_exclusive(checkpoint_path, payload)


class StudyHindsightMemoryProvider(RepairHindsightMemoryProvider):
    """Create-only provider with per-batch receipts and per-unit document checks."""

    def __init__(self, namespace: str, batch_trace_path: Path) -> None:
        super().__init__(namespace)
        self.batch_trace_path = batch_trace_path
        self.confirmed_document_ids_sha256: dict[str, str] = {}
        self._batch_attempts: dict[str, int] = defaultdict(int)

    def prepare(
        self,
        store_dir: Path,
        unit_ids: set[str] | None = None,
        reset: bool = True,
    ) -> None:
        super().prepare(store_dir, unit_ids=unit_ids, reset=reset)
        original_retain_batch = self._client.aretain_batch

        async def traced_retain_batch(*args, **kwargs):
            bank_id = kwargs.get("bank_id")
            items = kwargs.get("items") or []
            document_ids = [item.get("document_id") for item in items]
            batch_id = canonical_json_sha256(
                {"bank_id": bank_id, "document_ids": document_ids}
            )
            self._batch_attempts[batch_id] += 1
            attempt_index = self._batch_attempts[batch_id]
            attempt_id = uuid.uuid4().hex
            append_jsonl(
                self.batch_trace_path,
                {
                    "event_type": "attempt",
                    "attempt_id": attempt_id,
                    "attempt_index": attempt_index,
                    "batch_id": batch_id,
                    "bank_id": bank_id,
                    "document_ids": document_ids,
                    "retain_async": kwargs.get("retain_async"),
                },
            )
            try:
                response = await original_retain_batch(*args, **kwargs)
            except Exception as error:
                append_jsonl(
                    self.batch_trace_path,
                    {
                        "event_type": "failure",
                        "attempt_id": attempt_id,
                        "batch_id": batch_id,
                        "error_type": type(error).__name__,
                        "request_id": getattr(error, "request_id", None),
                        "status_code": getattr(error, "status_code", None),
                    },
                )
                raise
            append_jsonl(
                self.batch_trace_path,
                {
                    "event_type": "success",
                    "attempt_id": attempt_id,
                    "batch_id": batch_id,
                    "operation_id": getattr(response, "operation_id", None),
                },
            )
            return response

        self._client.aretain_batch = traced_retain_batch

    async def async_ingest(self, documents) -> None:
        await super().async_ingest(documents)
        expected_by_query: dict[str, set[str]] = defaultdict(set)
        for document in documents:
            if document.user_id is None:
                raise RuntimeError("study document is missing its query isolation ID")
            expected_by_query[document.user_id].add(document.id)
        for query_id, expected_ids in expected_by_query.items():
            actual_ids = await asyncio.to_thread(self.document_ids, query_id)
            if actual_ids != expected_ids:
                raise RuntimeError(
                    f"Hindsight document set mismatch for {query_id}: "
                    f"missing={sorted(expected_ids - actual_ids)} "
                    f"unexpected={sorted(actual_ids - expected_ids)}"
                )
            self.confirmed_document_ids_sha256[query_id] = _identifier_set_sha256(
                actual_ids
            )

    async def async_retrieve(
        self,
        query: str,
        k: int = 10,
        user_id: str | None = None,
        query_timestamp: str | None = None,
    ):
        if user_id is None:
            raise RuntimeError("study retrieval is missing its query isolation ID")
        bind_completion_scope(user_id)
        return await super().async_retrieve(
            query,
            user_id=user_id,
            query_timestamp=query_timestamp,
        )


def _pilot_verdicts_by_id(pilot_summary: dict[str, Any]) -> dict[str, list[bool]]:
    questions = pilot_summary.get("questions")
    if not isinstance(questions, list):
        raise ValueError("pilot summary must contain questions")
    verdicts_by_id = {}
    for question in questions:
        query_id = question.get("query_id")
        verdicts = question.get("replica_verdicts")
        if (
            not isinstance(query_id, str)
            or not isinstance(verdicts, list)
            or not verdicts
            or any(type(verdict) is not bool for verdict in verdicts)
        ):
            raise ValueError("pilot summary contains invalid replica verdicts")
        verdicts_by_id[query_id] = verdicts
    return verdicts_by_id


def _estimate_study(
    candidate_by_id: dict[str, dict[str, Any]],
    rerun_by_id: dict[str, dict[str, Any]],
    manifest: dict[str, Any],
    error_values: dict[str, float],
    population_by_type: dict[str, dict[str, int]],
) -> tuple[dict[str, Any], dict[str, float]]:
    errors_by_type: dict[str, list[float]] = defaultdict(list)
    controls_by_type: dict[str, list[float]] = defaultdict(list)
    for query_id, value in error_values.items():
        question_type = candidate_by_id[query_id]["meta"]["question_type"]
        errors_by_type[question_type].append(value)
    for query_id in manifest["control_query_ids"]:
        question_type = candidate_by_id[query_id]["meta"]["question_type"]
        controls_by_type[question_type].append(
            float(rerun_by_id[query_id].get("correct") is not True)
        )

    by_type = {}
    net_verdicts = 0.0
    gross_verdicts = 0.0
    for question_type, population in population_by_type.items():
        error_sample = errors_by_type[question_type]
        control_sample = controls_by_type[question_type]
        if not error_sample or not control_sample:
            raise ValueError(f"analysis sample is empty for {question_type}")
        recovery_rate = sum(error_sample) / len(error_sample)
        regression_rate = sum(control_sample) / len(control_sample)
        recovered = population["errors"] * recovery_rate
        regressed = population["correct"] * regression_rate
        type_net = recovered - regressed
        type_gross = recovered + regressed
        by_type[question_type] = {
            "error_sample_questions": len(error_sample),
            "control_sample_questions": len(control_sample),
            "recovery_rate": recovery_rate,
            "regression_rate": regression_rate,
            "estimated_recoveries": recovered,
            "estimated_regressions": regressed,
            "net_verdicts": type_net,
            "gross_verdicts": type_gross,
        }
        net_verdicts += type_net
        gross_verdicts += type_gross
    return by_type, {
        "net_verdicts": net_verdicts,
        "gross_verdicts": gross_verdicts,
    }


def analyze_study(
    candidate: dict[str, Any],
    pilot_summary: dict[str, Any],
    rerun: dict[str, Any],
    manifest: dict[str, Any],
    *,
    population_by_type: dict[str, dict[str, int]],
) -> dict[str, Any]:
    candidate_by_id = _result_by_query_id(candidate)
    rerun_by_id = _result_by_query_id(rerun)
    new_query_ids = set(manifest["new_error_query_ids"]) | set(
        manifest["control_query_ids"]
    )
    if set(rerun_by_id) != new_query_ids:
        raise ValueError("rerun query IDs do not match the selection manifest")
    pilot_verdicts = _pilot_verdicts_by_id(pilot_summary)
    if set(pilot_verdicts) != set(manifest["pilot_query_ids"]):
        raise ValueError("pilot query IDs do not match the selection manifest")

    error_values = {
        query_id: sum(pilot_verdicts[query_id]) / len(pilot_verdicts[query_id])
        for query_id in manifest["pilot_query_ids"]
    }
    error_values.update(
        {
            query_id: float(rerun_by_id[query_id].get("correct") is True)
            for query_id in manifest["new_error_query_ids"]
        }
    )
    by_type, combined = _estimate_study(
        candidate_by_id,
        rerun_by_id,
        manifest,
        error_values,
        population_by_type,
    )

    raw_recoveries = sum(
        rerun_by_id[query_id].get("correct") is True
        for query_id in manifest["new_error_query_ids"]
    )
    raw_regressions = sum(
        rerun_by_id[query_id].get("correct") is not True
        for query_id in manifest["control_query_ids"]
    )
    sensitivity = []
    replica_count = len(next(iter(pilot_verdicts.values())))
    for replica_index in range(replica_count):
        replica_error_values = dict(error_values)
        for query_id in manifest["pilot_query_ids"]:
            replica_error_values[query_id] = float(
                pilot_verdicts[query_id][replica_index]
            )
        _, replica_combined = _estimate_study(
            candidate_by_id,
            rerun_by_id,
            manifest,
            replica_error_values,
            population_by_type,
        )
        sensitivity.append(
            {"pilot_replica": replica_index + 1, **replica_combined}
        )

    gap = manifest["analysis_frame"]["observed_gap_verdicts"]
    return {
        "study_id": manifest["study_id"],
        "estimand": (
            "pilot replicas averaged within question; questions equally weighted "
            "within type; type rates weighted to eligible error/correct populations"
        ),
        "raw_new_run": {
            "recoveries": raw_recoveries,
            "regressions": raw_regressions,
            "gross_flips": raw_recoveries + raw_regressions,
            "net_change": raw_recoveries - raw_regressions,
        },
        "by_question_type": by_type,
        "combined": {
            **combined,
            "net_scale_vs_25": combined["net_verdicts"] / gap,
            "gross_scale_vs_25": combined["gross_verdicts"] / gap,
        },
        "pilot_replica_sensitivity": sensitivity,
        "uncertainty_limitation": (
            "One new observation per non-pilot question is an approximation; "
            "no confidence interval is claimed."
        ),
    }


def validate_retrieval_isolation(
    result: dict[str, Any],
    *,
    expected_document_ids: set[str],
) -> None:
    raw_response = result.get("raw_response")
    if not isinstance(raw_response, dict):
        raise RuntimeError(f"{result.get('query_id')} has no raw retrieval response")
    raw_results = raw_response.get("results")
    chunks = raw_response.get("chunks")
    if not isinstance(raw_results, list) or not isinstance(chunks, dict):
        raise RuntimeError(f"{result.get('query_id')} has an invalid raw response")
    for raw_result in raw_results:
        if not isinstance(raw_result, dict):
            raise RuntimeError("raw retrieval result is invalid")
        document_id = raw_result.get("document_id")
        if document_id not in expected_document_ids:
            raise RuntimeError(
                f"foreign document in retrieval for {result.get('query_id')}: "
                f"{document_id}"
            )
        chunk_id = raw_result.get("chunk_id")
        if chunk_id is not None and (
            not isinstance(chunk_id, str)
            or document_id not in chunk_id
            or chunk_id not in chunks
        ):
            raise RuntimeError(
                f"foreign or missing chunk in retrieval for {result.get('query_id')}: "
                f"{chunk_id}"
            )
    for chunk_id, chunk in chunks.items():
        if not isinstance(chunk_id, str) or not isinstance(chunk, dict):
            raise RuntimeError("raw retrieval chunk map is invalid")
        if not any(document_id in chunk_id for document_id in expected_document_ids):
            raise RuntimeError(
                f"foreign chunk in retrieval for {result.get('query_id')}: "
                f"{chunk_id}"
            )
        embedded_id = chunk.get("id")
        if embedded_id is not None and embedded_id != chunk_id:
            raise RuntimeError(f"chunk key/id mismatch: {chunk_id}")


def summarize_retain_receipts(
    events: list[dict[str, Any]],
    expected_batch_ids: set[str],
) -> dict[str, Any]:
    attempts = {}
    terminals = {}
    for event in events:
        event_type = event.get("event_type")
        attempt_id = event.get("attempt_id")
        batch_id = event.get("batch_id")
        if event_type not in {"attempt", "success", "failure"}:
            raise ValueError("retain receipt has an invalid event type")
        if not isinstance(attempt_id, str) or not isinstance(batch_id, str):
            raise ValueError("retain receipt is missing an identity")
        target = attempts if event_type == "attempt" else terminals
        if attempt_id in target:
            raise ValueError(f"duplicate retain receipt event: {attempt_id}")
        target[attempt_id] = event
    if set(attempts) != set(terminals):
        raise ValueError("every retain attempt must have one terminal receipt")
    for attempt_id, attempt in attempts.items():
        if attempt.get("retain_async") is not False:
            raise ValueError(f"retain batch was not synchronous: {attempt_id}")
        if terminals[attempt_id].get("batch_id") != attempt.get("batch_id"):
            raise ValueError(f"retain batch identity changed: {attempt_id}")
    successful_batch_ids = {
        event["batch_id"]
        for event in terminals.values()
        if event["event_type"] == "success"
    }
    if successful_batch_ids != expected_batch_ids:
        raise ValueError(
            "batch receipt mismatch: "
            f"missing={sorted(expected_batch_ids - successful_batch_ids)} "
            f"unexpected={sorted(successful_batch_ids - expected_batch_ids)}"
        )
    success_counts: dict[str, int] = defaultdict(int)
    for event in terminals.values():
        if event["event_type"] == "success":
            success_counts[event["batch_id"]] += 1
    duplicate_successes = {
        batch_id: count for batch_id, count in success_counts.items() if count != 1
    }
    if duplicate_successes:
        raise ValueError(f"retain batches have duplicate successes: {duplicate_successes}")
    return {
        "expected_batches": len(expected_batch_ids),
        "attempts": len(attempts),
        "failed_attempts": sum(
            event["event_type"] == "failure" for event in terminals.values()
        ),
        "successful_batches": len(successful_batch_ids),
    }


def completion_metadata_by_query(
    events: list[dict[str, Any]],
    query_ids: tuple[str, ...],
) -> dict[str, dict[str, dict[str, Any]]]:
    successful = [event for event in events if event.get("event_type") == "success"]
    metadata = {}
    for query_id in query_ids:
        metadata[query_id] = {}
        for role in ("answer", "judge"):
            matches = [
                event
                for event in successful
                if event.get("scope") == query_id and event.get("role") == role
            ]
            if not matches:
                raise ValueError(
                    f"missing scoped completion for query={query_id} role={role}"
                )
            final = matches[-1]
            for field in ("request_id", "messages_sha256", "usage"):
                if not final.get(field):
                    raise ValueError(
                        f"scoped completion is missing {field}: {query_id}/{role}"
                    )
            metadata[query_id][role] = {
                "request_id": final["request_id"],
                "messages_sha256": final["messages_sha256"],
                "usage": final["usage"],
                "requested_model": final.get("requested_model"),
                "resolved_model": final.get("resolved_model"),
                "attempt_count": len(matches),
            }
    return metadata


def validate_run_attestation(
    attestation: dict[str, Any],
    manifest: dict[str, Any],
) -> None:
    required_fields = {
        "study_id",
        "selection_sha256",
        "profile",
        "daemon_running_postflight",
        "query_ids",
        "confirmed_document_ids_sha256",
        "document_count",
        "retain_batch_trace",
        "omb_completion_trace",
        "hindsight_completion_trace",
        "result_evidence",
        "result_file_sha256",
        "input_sha256",
        "code_sha256",
        "git_head",
    }
    if not isinstance(attestation, dict) or not required_fields <= set(attestation):
        raise ValueError("run attestation is incomplete")
    selected = tuple(
        manifest["new_error_query_ids"] + manifest["control_query_ids"]
    )
    if (
        attestation.get("study_id") != manifest.get("study_id")
        or attestation.get("selection_sha256")
        != EXPECTED_SELECTION_MANIFEST_SHA256
        or attestation.get("daemon_running_postflight") is not False
        or tuple(attestation.get("query_ids") or ()) != selected
        or set(attestation.get("confirmed_document_ids_sha256") or {})
        != set(selected)
        or set(attestation.get("result_evidence") or {}) != set(selected)
        or attestation.get("document_count") != EXPECTED_DOCUMENT_COUNT
    ):
        raise ValueError("run attestation identity or completeness drifted")
    if not isinstance(attestation.get("profile"), str) or not attestation["profile"]:
        raise ValueError("run attestation profile is invalid")
    if not isinstance(attestation.get("result_file_sha256"), str) or len(
        attestation["result_file_sha256"]
    ) != 64:
        raise ValueError("run attestation result hash is invalid")
    expected_inputs = {
        "candidate": EXPECTED_CANDIDATE_SHA256,
        "reference_gzip": EXPECTED_REFERENCE_GZIP_SHA256,
        "pilot": EXPECTED_PILOT_SHA256,
        "resume_log": EXPECTED_RESUME_LOG_SHA256,
        "dataset": EXPECTED_DATASET_SHA256,
    }
    if attestation.get("input_sha256") != expected_inputs:
        raise ValueError("run attestation input hashes drifted")
    retain_trace = attestation.get("retain_batch_trace") or {}
    omb_trace = attestation.get("omb_completion_trace") or {}
    hindsight_trace = attestation.get("hindsight_completion_trace") or {}
    if (
        retain_trace.get("successful_batches") != EXPECTED_RETAIN_BATCH_COUNT
        or retain_trace.get("expected_batches") != EXPECTED_RETAIN_BATCH_COUNT
        or omb_trace.get("calls_by_role", {}).get("answer", 0)
        < EXPECTED_NEW_RUN_COUNT
        or omb_trace.get("calls_by_role", {}).get("judge", 0)
        < EXPECTED_NEW_RUN_COUNT
        or hindsight_trace.get("calls_by_role", {}).get(
            "hindsight_extraction", 0
        )
        < EXPECTED_RETAIN_BATCH_COUNT
        or hindsight_trace.get("calls_by_role", {}).get(
            "hindsight_verification", 0
        )
        != 1
    ):
        raise ValueError("run attestation trace summaries are incomplete")
    for trace in (retain_trace, omb_trace, hindsight_trace):
        if not isinstance(trace.get("sha256"), str) or len(trace["sha256"]) != 64:
            raise ValueError("run attestation trace hash is invalid")
    if not isinstance(attestation.get("code_sha256"), dict) or not attestation[
        "code_sha256"
    ]:
        raise ValueError("run attestation code hashes are incomplete")


def _study_root(analysis_root: Path, study_id: str) -> Path:
    return analysis_root / _slug(study_id)


def _selection_path(analysis_root: Path, study_id: str) -> Path:
    return _study_root(analysis_root, study_id) / "selection.json"


def _preflight_path(analysis_root: Path, study_id: str) -> Path:
    return _study_root(analysis_root, study_id) / "preflight.json"


def _final_result_path(analysis_root: Path, study_id: str) -> Path:
    return _study_root(analysis_root, study_id) / "run/s.json"


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text())
    except FileNotFoundError as error:
        raise ValueError(f"required artifact is missing: {path}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid JSON artifact: {path}") from error
    if not isinstance(payload, dict):
        raise ValueError(f"artifact must be a JSON object: {path}")
    return payload


def _validate_hash(path: Path, expected: str, label: str) -> None:
    actual = _sha256(path)
    if actual != expected:
        raise ValueError(f"{label} SHA-256 drifted: expected {expected}, got {actual}")


def _parse_resume_query_ids(path: Path) -> set[str]:
    query_ids = re.findall(r"\[query:([^\]]+)\] start", path.read_text())
    if len(query_ids) != 38 or len(set(query_ids)) != 38:
        raise ValueError("resume-1 log must contain exactly 38 unique query starts")
    return set(query_ids)


def _validate_pilot_summary(pilot_summary: dict[str, Any]) -> None:
    verdicts_by_id = _pilot_verdicts_by_id(pilot_summary)
    actual = {
        query_id: tuple(verdicts_by_id.get(query_id, []))
        for query_id in PILOT_QUERY_IDS
    }
    if actual != PILOT_VERDICTS or set(verdicts_by_id) != set(PILOT_QUERY_IDS):
        raise ValueError("pilot verdict evidence drifted")


def _frozen_manifest(
    study_id: str,
    candidate_path: Path,
    reference_path: Path,
    pilot_summary_path: Path,
    resume_log_path: Path,
) -> dict[str, Any]:
    if study_id != DEFAULT_STUDY_ID:
        raise ValueError(f"this runner is frozen to study ID {DEFAULT_STUDY_ID}")
    _validate_hash(candidate_path, EXPECTED_CANDIDATE_SHA256, "candidate")
    _validate_hash(reference_path, EXPECTED_REFERENCE_GZIP_SHA256, "reference gzip")
    _validate_hash(pilot_summary_path, EXPECTED_PILOT_SHA256, "pilot summary")
    _validate_hash(resume_log_path, EXPECTED_RESUME_LOG_SHA256, "resume-1 log")
    candidate = load_amb_result(candidate_path)
    reference = load_amb_result(reference_path)
    pilot_summary = _load_json_object(pilot_summary_path)
    _validate_pilot_summary(pilot_summary)
    resume_query_ids = _parse_resume_query_ids(resume_log_path)
    excluded_query_ids = resume_query_ids | set(REPAIR_QUERY_IDS) | set(
        GOLD_AMBIGUOUS_QUERY_IDS
    )
    return build_selection_manifest(
        candidate,
        reference,
        study_id=study_id,
        pilot_query_ids=PILOT_QUERY_IDS,
        excluded_query_ids=excluded_query_ids,
        quotas=QUESTION_TYPE_QUOTAS,
        analysis_frame=ANALYSIS_FRAME,
        exclusions={
            "resume_1_query_count": len(resume_query_ids),
            "repair_query_ids": list(REPAIR_QUERY_IDS),
            "gold_ambiguous_query_ids": list(GOLD_AMBIGUOUS_QUERY_IDS),
        },
        expected_manifest_sha256=EXPECTED_SELECTION_MANIFEST_SHA256,
    )


def command_select(args: argparse.Namespace) -> Path:
    manifest = _frozen_manifest(
        args.study_id,
        args.candidate,
        args.reference,
        args.pilot_summary,
        args.resume_log,
    )
    id_set_hash = new_query_ids_sha256(manifest)
    if id_set_hash != EXPECTED_NEW_QUERY_IDS_SHA256:
        raise ValueError(
            "new query ID-set SHA-256 drifted: "
            f"expected {EXPECTED_NEW_QUERY_IDS_SHA256}, got {id_set_hash}"
        )
    output_path = _selection_path(args.analysis_root, args.study_id)
    write_json_atomic_exclusive(output_path, manifest)
    print("NONDETERMINISM_SELECTION_OK", flush=True)
    print(f"selection={output_path.resolve()}", flush=True)
    print(f"selection_manifest_sha256={selection_manifest_sha256(manifest)}", flush=True)
    print(f"new_query_ids_sha256={id_set_hash}", flush=True)
    print(
        f"new_errors={len(manifest['new_error_query_ids'])} "
        f"controls={len(manifest['control_query_ids'])} "
        f"new_runs={len(manifest['new_error_query_ids']) + len(manifest['control_query_ids'])}",
        flush=True,
    )
    return output_path


def _code_hashes() -> dict[str, str]:
    return {
        relative_path: _sha256(REPOSITORY_ROOT / relative_path)
        for relative_path in CODE_PATHS
    }


def _git_head() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _load_selected_dataset(
    manifest: dict[str, Any],
) -> tuple[SelectedLongMemEvalDataset, tuple[str, ...], dict[str, set[str]], list[Any]]:
    selected = tuple(
        manifest["new_error_query_ids"] + manifest["control_query_ids"]
    )
    dataset = SelectedLongMemEvalDataset(selected)
    queries = dataset.load_queries("s", limit=500)
    if tuple(query.id for query in queries) != selected:
        raise ValueError("LongMemEval selected query order or membership drifted")
    documents = dataset.load_documents("s", user_ids=set(selected))
    document_ids_by_query = {query_id: set() for query_id in selected}
    for document in documents:
        if document.user_id in document_ids_by_query:
            document_ids_by_query[document.user_id].add(document.id)
    if any(not document_ids for document_ids in document_ids_by_query.values()):
        raise ValueError("a selected query has no LongMemEval documents")
    if sum(map(len, document_ids_by_query.values())) != EXPECTED_DOCUMENT_COUNT:
        raise ValueError("selected LongMemEval document count drifted")
    expected_batches = expected_retain_batch_count(
        document_ids_by_query,
        StudyHindsightMemoryProvider._ASYNC_BATCH_SIZE,
    )
    if expected_batches != EXPECTED_RETAIN_BATCH_COUNT:
        raise ValueError("selected LongMemEval retain-batch count drifted")
    return dataset, selected, document_ids_by_query, documents


def _live_model_probe(llm: Any, requested_model: str) -> dict[str, Any]:
    client = llm._client.with_options(max_retries=0)
    response = client.chat.completions.create(
        model=requested_model,
        messages=[
            {"role": "system", "content": "Return only a JSON object."},
            {"role": "user", "content": 'Return exactly {"ok":true}.'},
        ],
        temperature=0.0,
        max_tokens=128,
        response_format={"type": "json_object"},
    )
    content = response.choices[0].message.content or ""
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"{requested_model} live probe returned invalid JSON") from error
    if payload.get("ok") is not True:
        raise RuntimeError(f"{requested_model} live probe returned the wrong payload")
    if response.model != requested_model:
        raise RuntimeError(
            f"{requested_model} resolved model drifted to {response.model}"
        )
    usage = response.usage
    return {
        "requested_model": requested_model,
        "resolved_model": response.model,
        "request_id": response.id,
        "usage": {
            "input": usage.prompt_tokens,
            "output": usage.completion_tokens,
            "total": usage.total_tokens,
        },
        "reasoning_content_present": bool(
            getattr(response.choices[0].message, "reasoning_content", None)
        ),
    }


def validate_live_model_probes(probes: Any) -> None:
    if not isinstance(probes, list) or len(probes) != 2:
        raise ValueError("live model probes must contain exactly Pro and Flash")
    by_model = {
        probe.get("requested_model"): probe
        for probe in probes
        if isinstance(probe, dict)
    }
    expected_models = {"deepseek-v4-pro", "deepseek-v4-flash"}
    if set(by_model) != expected_models:
        raise ValueError("live model probes do not cover Pro and Flash")
    for model, probe in by_model.items():
        usage = probe.get("usage")
        if (
            probe.get("resolved_model") != model
            or not probe.get("request_id")
            or not isinstance(usage, dict)
            or any(
                not isinstance(usage.get(field), int) or usage[field] < 0
                for field in ("input", "output", "total")
            )
            or usage["input"] + usage["output"] != usage["total"]
        ):
            raise ValueError(f"live model probe is invalid for {model}")
        if probe.get("reasoning_content_present") is not True:
            raise ValueError(f"live model probe lacks thinking evidence for {model}")


def validate_hindsight_canary(canary: Any) -> None:
    if not isinstance(canary, dict):
        raise ValueError("Hindsight canary evidence is missing")
    retain_trace = canary.get("retain_batch_trace") or {}
    completion_trace = canary.get("hindsight_completion_trace") or {}
    calls_by_role = completion_trace.get("calls_by_role") or {}
    if (
        not isinstance(canary.get("profile"), str)
        or not canary["profile"]
        or canary.get("daemon_running_postflight") is not False
        or canary.get("confirmed_document_count") != 1
        or retain_trace.get("expected_batches") != 1
        or retain_trace.get("successful_batches") != 1
        or calls_by_role.get("hindsight_extraction", 0) < 1
        or calls_by_role.get("hindsight_verification") != 1
    ):
        raise ValueError("Hindsight canary evidence is incomplete or invalid")
    for trace in (retain_trace, completion_trace):
        if not isinstance(trace.get("path"), str) or not isinstance(
            trace.get("sha256"), str
        ) or len(trace["sha256"]) != 64:
            raise ValueError("Hindsight canary trace identity is invalid")


def _run_hindsight_canary(study_root: Path, study_id: str) -> dict[str, Any]:
    from memory_bench.models import Document

    canary_root = study_root / "preflight-canary"
    if canary_root.exists():
        raise FileExistsError(f"Hindsight canary state already exists: {canary_root}")
    suffix = uuid.uuid4().hex[:12]
    namespace = f"preflight-{study_id}-{suffix}"
    query_id = "hindsight-canary"
    document = Document(
        id="hindsight-canary-document",
        content="The Hindsight extraction canary color is blue.",
        user_id=query_id,
    )
    batch_trace_path = canary_root / "retain-batches.jsonl"
    completion_trace_path = canary_root / "hindsight-completions.jsonl"
    provider = StudyHindsightMemoryProvider(namespace, batch_trace_path)
    expected_profile = _repair_profile(f"longmemeval-s-repair-{namespace}")
    stopped_profile = None
    try:
        os.environ["AMB_VARIANCE_HINDSIGHT_TRACE_PATH"] = str(
            completion_trace_path.resolve()
        )
        store_dir = (
            canary_root
            / "work/longmemeval/hindsight-canary/_store/s/all"
        )
        provider.prepare(store_dir, unit_ids={query_id})
        if provider.repair_profile != expected_profile:
            raise RuntimeError("Hindsight canary profile drifted")
        asyncio.run(provider.async_ingest([document]))
        actual_document_ids = provider.document_ids(query_id)
        if actual_document_ids != {document.id}:
            raise RuntimeError("Hindsight canary document confirmation failed")
        bank_id = provider._bank_id_for(query_id)
        expected_batch_id = canonical_json_sha256(
            {"bank_id": bank_id, "document_ids": [document.id]}
        )
        retain_summary = summarize_retain_receipts(
            _load_jsonl(batch_trace_path),
            {expected_batch_id},
        )
        completion_summary = summarize_completion_events(
            _load_jsonl(completion_trace_path),
            {"hindsight_extraction": 1, "hindsight_verification": 1},
            EXPECTED_RESOLVED_MODEL_BY_ROLE,
        )
    finally:
        os.environ.pop("AMB_VARIANCE_HINDSIGHT_TRACE_PATH", None)
        stopped_profile = close_and_verify_hindsight_daemon(provider)
    if stopped_profile != expected_profile:
        raise RuntimeError("Hindsight canary daemon shutdown was not verified")
    canary = {
        "profile": expected_profile,
        "daemon_running_postflight": False,
        "confirmed_document_count": 1,
        "confirmed_document_ids_sha256": _identifier_set_sha256({document.id}),
        "retain_batch_trace": {
            "path": str(batch_trace_path.resolve()),
            "sha256": _sha256(batch_trace_path),
            **retain_summary,
        },
        "hindsight_completion_trace": {
            "path": str(completion_trace_path.resolve()),
            "sha256": _sha256(completion_trace_path),
            **completion_summary,
        },
    }
    validate_hindsight_canary(canary)
    return canary


def command_preflight(args: argparse.Namespace) -> Path:
    if not args.live:
        raise ValueError("paid-run preflight requires --live")
    if not args.confirm_supplier_versions:
        raise ValueError(
            "preflight requires --confirm-supplier-versions after manually confirming "
            "Flash 0731, Pro 0813, and default thinking high"
        )
    selection_path = _selection_path(args.analysis_root, args.study_id)
    manifest = _load_json_object(selection_path)
    if selection_manifest_sha256(manifest) != EXPECTED_SELECTION_MANIFEST_SHA256:
        raise ValueError("saved selection manifest hash drifted")
    _load_environment()
    data_path = _validate_expected_runtime_configuration()
    _validate_hash(data_path, EXPECTED_DATASET_SHA256, "LongMemEval dataset")
    if version("hindsight-embed") != EXPECTED_HINDSIGHT_EMBED_VERSION:
        raise ValueError("installed hindsight-embed version drifted")
    _, selected, document_ids_by_query, _ = _load_selected_dataset(manifest)

    namespace = f"nondeterminism-{args.study_id}"
    profile = _repair_profile(f"longmemeval-s-repair-{namespace}")
    for profile_path in _repair_profile_paths(profile):
        if profile_path.exists():
            raise FileExistsError(
                f"study profile already has state at {profile_path}; refuse reuse"
            )
    study_root = _study_root(args.analysis_root, args.study_id)
    protected_outputs = (
        study_root / "run/s.json",
        study_root / "run/retain-attestation.json",
        study_root / "run/omb-completions.jsonl",
        study_root / "run/hindsight-completions.jsonl",
        study_root / "run/retain-batches.jsonl",
        study_root / "journal",
        study_root / "preflight-canary",
    )
    existing = [str(path) for path in protected_outputs if path.exists()]
    if existing:
        raise FileExistsError(f"study run state already exists: {existing}")

    from memory_bench.llm import get_answer_llm, get_judge_llm

    answer_llm = get_answer_llm()
    judge_llm = get_judge_llm()
    live_probes = [
        _live_model_probe(answer_llm, "deepseek-v4-pro"),
        _live_model_probe(judge_llm, "deepseek-v4-flash"),
    ]
    validate_live_model_probes(live_probes)
    hindsight_canary = _run_hindsight_canary(study_root, args.study_id)
    now = datetime.now(timezone.utc)
    payload = {
        "study_id": args.study_id,
        "created_at": now.isoformat(),
        "expires_at_epoch": now.timestamp() + PREFLIGHT_MAX_AGE_SECONDS,
        "selection_sha256": EXPECTED_SELECTION_MANIFEST_SHA256,
        "candidate_sha256": EXPECTED_CANDIDATE_SHA256,
        "reference_gzip_sha256": EXPECTED_REFERENCE_GZIP_SHA256,
        "pilot_sha256": EXPECTED_PILOT_SHA256,
        "resume_log_sha256": EXPECTED_RESUME_LOG_SHA256,
        "dataset_sha256": EXPECTED_DATASET_SHA256,
        "runtime_config": _redacted_runtime_config(),
        "code_sha256": _code_hashes(),
        "git_head": _git_head(),
        "profile": profile,
        "query_count": len(selected),
        "document_count": sum(map(len, document_ids_by_query.values())),
        "retain_batch_count": EXPECTED_RETAIN_BATCH_COUNT,
        "live": True,
        "live_model_probes": live_probes,
        "hindsight_canary": hindsight_canary,
        "manual_supplier_confirmation": {
            "confirmed_at": now.isoformat(),
            "flash_version": "0731",
            "pro_version": "0813",
            "default_thinking": "high",
        },
    }
    output_path = _preflight_path(args.analysis_root, args.study_id)
    write_json_atomic_exclusive(output_path, payload)
    print("NONDETERMINISM_PREFLIGHT_OK", flush=True)
    print(f"preflight={output_path.resolve()}", flush=True)
    print(f"profile={profile}", flush=True)
    print("runtime_config=" + json.dumps(payload["runtime_config"], sort_keys=True), flush=True)
    print(
        f"queries={len(selected)} documents={payload['document_count']} "
        f"retain_batches={payload['retain_batch_count']}",
        flush=True,
    )
    return output_path


def build_final_artifact(
    summary: Any,
    journal_dir: Path,
    selected_query_ids: tuple[str, ...],
) -> dict[str, Any]:
    if is_dataclass(summary):
        payload = asdict(summary)
    elif hasattr(summary, "__dict__"):
        payload = dict(vars(summary))
    else:
        raise ValueError("evaluation summary has an unsupported type")
    results = []
    for index, query_id in enumerate(selected_query_ids, start=1):
        checkpoint_path = journal_dir / f"{index:03d}-{query_id}.json"
        result = _load_json_object(checkpoint_path)
        if result.get("query_id") != query_id:
            raise ValueError(f"journal query order drifted at {checkpoint_path}")
        results.append(result)
    if len(results) != len(selected_query_ids):
        raise ValueError("journal is incomplete")
    payload["results"] = results
    payload["total_queries"] = len(results)
    payload["correct"] = sum(result.get("correct") is True for result in results)
    payload["accuracy"] = payload["correct"] / len(results)
    retrieval_times = [
        result["retrieve_time_ms"]
        for result in results
        if isinstance(result.get("retrieve_time_ms"), (int, float))
    ]
    context_tokens = [
        result["context_tokens"]
        for result in results
        if isinstance(result.get("context_tokens"), int)
    ]
    payload["avg_retrieve_time_ms"] = (
        round(sum(retrieval_times) / len(retrieval_times), 1)
        if retrieval_times
        else None
    )
    payload["avg_context_tokens"] = (
        round(sum(context_tokens) / len(context_tokens), 1)
        if context_tokens
        else None
    )
    return payload


def _expected_retain_batch_ids(
    provider: StudyHindsightMemoryProvider,
    documents: list[Any],
    selected_query_ids: tuple[str, ...],
) -> set[str]:
    document_ids_by_query: dict[str, list[str]] = {
        query_id: [] for query_id in selected_query_ids
    }
    seen_by_query: dict[str, set[str]] = {
        query_id: set() for query_id in selected_query_ids
    }
    for document in documents:
        query_id = document.user_id
        if query_id not in document_ids_by_query:
            raise ValueError(f"unselected study document: {document.id}")
        if document.id not in seen_by_query[query_id]:
            seen_by_query[query_id].add(document.id)
            document_ids_by_query[query_id].append(document.id)
    batch_ids = set()
    batch_size = provider._ASYNC_BATCH_SIZE
    for query_id in selected_query_ids:
        bank_id = provider._bank_id_for(query_id)
        document_ids = document_ids_by_query[query_id]
        for index in range(0, len(document_ids), batch_size):
            batch_ids.add(
                canonical_json_sha256(
                    {
                        "bank_id": bank_id,
                        "document_ids": document_ids[index : index + batch_size],
                    }
                )
            )
    if len(batch_ids) != EXPECTED_RETAIN_BATCH_COUNT:
        raise ValueError("expected retain batch identity set drifted")
    return batch_ids


def _validate_preflight_for_run(
    analysis_root: Path,
    study_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    selection = _load_json_object(_selection_path(analysis_root, study_id))
    if selection_manifest_sha256(selection) != EXPECTED_SELECTION_MANIFEST_SHA256:
        raise ValueError("saved selection manifest hash drifted")
    preflight = _load_json_object(_preflight_path(analysis_root, study_id))
    if preflight.get("study_id") != study_id or preflight.get("live") is not True:
        raise ValueError("saved preflight is not a live preflight for this study")
    if datetime.now(timezone.utc).timestamp() > preflight.get("expires_at_epoch", 0):
        raise ValueError("saved live preflight expired; run the one-line launcher again")
    expected_fields = {
        "selection_sha256": EXPECTED_SELECTION_MANIFEST_SHA256,
        "candidate_sha256": EXPECTED_CANDIDATE_SHA256,
        "reference_gzip_sha256": EXPECTED_REFERENCE_GZIP_SHA256,
        "pilot_sha256": EXPECTED_PILOT_SHA256,
        "resume_log_sha256": EXPECTED_RESUME_LOG_SHA256,
        "dataset_sha256": EXPECTED_DATASET_SHA256,
        "query_count": EXPECTED_NEW_RUN_COUNT,
        "document_count": EXPECTED_DOCUMENT_COUNT,
        "retain_batch_count": EXPECTED_RETAIN_BATCH_COUNT,
    }
    drift = {
        key: (preflight.get(key), value)
        for key, value in expected_fields.items()
        if preflight.get(key) != value
    }
    if drift:
        raise ValueError(f"saved preflight evidence drifted: {drift}")
    confirmation = preflight.get("manual_supplier_confirmation") or {}
    if confirmation.get("flash_version") != "0731" or confirmation.get(
        "pro_version"
    ) != "0813" or confirmation.get("default_thinking") != "high":
        raise ValueError("manual supplier confirmation is missing or drifted")
    if preflight.get("code_sha256") != _code_hashes():
        raise ValueError("study code changed after the live preflight")
    if preflight.get("git_head") != _git_head():
        raise ValueError("git HEAD changed after the live preflight")
    validate_live_model_probes(preflight.get("live_model_probes"))
    validate_hindsight_canary(preflight.get("hindsight_canary"))
    for trace_field in ("retain_batch_trace", "hindsight_completion_trace"):
        trace = preflight["hindsight_canary"][trace_field]
        trace_path = Path(trace["path"])
        if _sha256(trace_path) != trace["sha256"]:
            raise ValueError(f"saved Hindsight canary {trace_field} hash drifted")
    runtime_config = preflight.get("runtime_config")
    required_runtime_roles = {
        "answer",
        "judge",
        "hindsight_extraction",
        "hindsight",
    }
    if not isinstance(runtime_config, dict) or set(runtime_config) != required_runtime_roles:
        raise ValueError("saved preflight runtime configuration is incomplete")
    return selection, preflight


def _validate_scoped_completion_pairs(
    events: list[dict[str, Any]],
    selected_query_ids: tuple[str, ...],
) -> dict[str, dict[str, dict[str, Any]]]:
    for event in events:
        if event.get("role") in {"answer", "judge"} and event.get("scope") not in set(
            selected_query_ids
        ):
            raise ValueError(f"completion event has an invalid scope: {event.get('scope')}")
    return completion_metadata_by_query(events, selected_query_ids)


def _result_evidence(
    artifact: dict[str, Any],
    completion_metadata: dict[str, dict[str, dict[str, Any]]],
) -> dict[str, Any]:
    evidence = {}
    for result in artifact["results"]:
        query_id = result["query_id"]
        answer_prompt_hash, judge_prompt_hash = _longmemeval_prompt_hashes(result)
        evidence[query_id] = {
            "context_sha256": _text_sha256(result.get("context") or ""),
            "answer_sha256": _text_sha256(result.get("answer") or ""),
            "normalized_answer_sha256": _normalized_answer_sha256(
                result.get("answer") or ""
            ),
            "raw_response_sha256": _json_sha256(result.get("raw_response")),
            "source_document_ids_sha256": _source_document_ids_sha256(
                result.get("raw_response")
            ),
            "answer_user_prompt_sha256": answer_prompt_hash,
            "judge_user_prompt_sha256": judge_prompt_hash,
            "completion": completion_metadata[query_id],
        }
    return evidence


def command_run(args: argparse.Namespace) -> Path:
    manifest, preflight = _validate_preflight_for_run(
        args.analysis_root,
        args.study_id,
    )
    _load_environment()
    data_path = _validate_expected_runtime_configuration()
    if preflight.get("runtime_config") != _redacted_runtime_config():
        raise ValueError("runtime configuration changed after the live preflight")
    _validate_hash(data_path, EXPECTED_DATASET_SHA256, "LongMemEval dataset")
    frozen_manifest = _frozen_manifest(
        args.study_id,
        DEFAULT_SOURCE,
        DEFAULT_REFERENCE,
        DEFAULT_PILOT_SUMMARY,
        DEFAULT_RESUME_LOG,
    )
    if frozen_manifest != manifest:
        raise ValueError("saved selection differs from the current frozen selection")
    dataset, selected, document_ids_by_query, documents = _load_selected_dataset(
        manifest
    )
    study_root = _study_root(args.analysis_root, args.study_id)
    run_dir = study_root / "run"
    journal_dir = study_root / "journal"
    work_dir = study_root / "work"
    final_path = _final_result_path(args.analysis_root, args.study_id)
    attestation_path = run_dir / "retain-attestation.json"
    omb_trace_path = run_dir / "omb-completions.jsonl"
    hindsight_trace_path = run_dir / "hindsight-completions.jsonl"
    batch_trace_path = run_dir / "retain-batches.jsonl"
    for path in (
        journal_dir,
        work_dir,
        final_path,
        attestation_path,
        omb_trace_path,
        hindsight_trace_path,
        batch_trace_path,
    ):
        if path.exists():
            raise FileExistsError(f"study run state already exists: {path}")
    namespace = f"nondeterminism-{args.study_id}"
    expected_profile = _repair_profile(f"longmemeval-s-repair-{namespace}")
    if preflight.get("profile") != expected_profile:
        raise ValueError("preflight profile identity drifted")
    for profile_path in _repair_profile_paths(expected_profile):
        if profile_path.exists():
            raise FileExistsError(f"study profile already has state at {profile_path}")

    from memory_bench.llm import get_answer_llm
    from memory_bench.modes import get_mode

    provider = StudyHindsightMemoryProvider(namespace, batch_trace_path)
    runner = JournaledEvalRunner(
        output_dir=work_dir,
        selected_query_ids=selected,
        journal_dir=journal_dir,
    )
    summary = None
    stopped_profile = None
    try:
        os.environ["AMB_VARIANCE_HINDSIGHT_TRACE_PATH"] = str(
            hindsight_trace_path.resolve()
        )
        answer_llm = get_answer_llm()
        disable_opaque_openai_retries(answer_llm)
        disable_opaque_openai_retries(runner._judge._llm)
        print(
            f"NONDETERMINISM_RUN_START study={args.study_id} "
            f"queries={len(selected)} profile={expected_profile}",
            flush=True,
        )
        with _trace_omb_completions(omb_trace_path):
            summary = runner.run(
                dataset=dataset,
                split="s",
                memory=provider,
                mode=get_mode("rag", llm=answer_llm),
                query_limit=500,
                run_name=f"nondeterminism-{args.study_id}",
                description=(
                    "Frozen 55-question DeepSeek nondeterminism study; "
                    f"study={args.study_id}; selection_sha256="
                    f"{EXPECTED_SELECTION_MANIFEST_SHA256}; profile={expected_profile}."
                ),
            )
        if summary.total_queries != EXPECTED_NEW_RUN_COUNT:
            raise RuntimeError(
                f"runner returned {summary.total_queries} results; "
                f"expected {EXPECTED_NEW_RUN_COUNT}"
            )
        if summary.ingested_docs != EXPECTED_DOCUMENT_COUNT:
            raise RuntimeError(
                f"runner reported {summary.ingested_docs} documents; "
                f"expected {EXPECTED_DOCUMENT_COUNT}"
            )
        artifact = build_final_artifact(summary, journal_dir, selected)
        result_by_id = _result_by_query_id(artifact)
        if set(result_by_id) != set(selected):
            raise RuntimeError("final journal query IDs differ from the selection")
        for query_id in selected:
            validate_retrieval_isolation(
                result_by_id[query_id],
                expected_document_ids=document_ids_by_query[query_id],
            )
        if set(provider.confirmed_document_ids_sha256) != set(selected):
            raise RuntimeError("not every selected bank has a document attestation")

        expected_batch_ids = _expected_retain_batch_ids(
            provider,
            documents,
            selected,
        )
        retain_summary = summarize_retain_receipts(
            _load_jsonl(batch_trace_path),
            expected_batch_ids,
        )
        omb_events = _load_jsonl(omb_trace_path)
        hindsight_events = _load_jsonl(hindsight_trace_path)
        omb_trace_summary = summarize_completion_events(
            omb_events,
            {"answer": len(selected), "judge": len(selected)},
            EXPECTED_RESOLVED_MODEL_BY_ROLE,
        )
        hindsight_trace_summary = summarize_completion_events(
            hindsight_events,
            {
                "hindsight_extraction": EXPECTED_RETAIN_BATCH_COUNT,
                "hindsight_verification": 1,
            },
            EXPECTED_RESOLVED_MODEL_BY_ROLE,
        )
        completion_metadata = _validate_scoped_completion_pairs(
            omb_events,
            selected,
        )
        result_evidence = _result_evidence(artifact, completion_metadata)
    finally:
        os.environ.pop("AMB_VARIANCE_HINDSIGHT_TRACE_PATH", None)
        stopped_profile = close_and_verify_hindsight_daemon(provider)
        if stopped_profile is not None:
            print(
                f"NONDETERMINISM_DAEMON_STOPPED profile={stopped_profile}",
                flush=True,
            )

    if summary is None or stopped_profile != expected_profile:
        raise RuntimeError("study did not complete with a verified daemon shutdown")
    for path, expected, label in (
        (DEFAULT_SOURCE, EXPECTED_CANDIDATE_SHA256, "candidate"),
        (DEFAULT_REFERENCE, EXPECTED_REFERENCE_GZIP_SHA256, "reference gzip"),
        (DEFAULT_PILOT_SUMMARY, EXPECTED_PILOT_SHA256, "pilot summary"),
        (DEFAULT_RESUME_LOG, EXPECTED_RESUME_LOG_SHA256, "resume-1 log"),
        (data_path, EXPECTED_DATASET_SHA256, "LongMemEval dataset"),
    ):
        _validate_hash(path, expected, label)
    postflight_code_hashes = _code_hashes()
    if postflight_code_hashes != preflight.get("code_sha256"):
        raise RuntimeError("study code changed while the paid run was executing")
    if _git_head() != preflight.get("git_head"):
        raise RuntimeError("git HEAD changed while the paid run was executing")
    attestation = {
        "study_id": args.study_id,
        "selection_sha256": EXPECTED_SELECTION_MANIFEST_SHA256,
        "profile": expected_profile,
        "daemon_running_postflight": False,
        "query_ids": list(selected),
        "confirmed_document_ids_sha256": provider.confirmed_document_ids_sha256,
        "document_count": EXPECTED_DOCUMENT_COUNT,
        "retain_batch_trace": {
            "path": str(batch_trace_path.resolve()),
            "sha256": _sha256(batch_trace_path),
            **retain_summary,
        },
        "omb_completion_trace": {
            "path": str(omb_trace_path.resolve()),
            "sha256": _sha256(omb_trace_path),
            **omb_trace_summary,
        },
        "hindsight_completion_trace": {
            "path": str(hindsight_trace_path.resolve()),
            "sha256": _sha256(hindsight_trace_path),
            **hindsight_trace_summary,
        },
        "result_evidence": result_evidence,
        "result_file_sha256": _json_file_sha256(artifact),
        "input_sha256": {
            "candidate": EXPECTED_CANDIDATE_SHA256,
            "reference_gzip": EXPECTED_REFERENCE_GZIP_SHA256,
            "pilot": EXPECTED_PILOT_SHA256,
            "resume_log": EXPECTED_RESUME_LOG_SHA256,
            "dataset": EXPECTED_DATASET_SHA256,
        },
        "code_sha256": preflight["code_sha256"],
        "git_head": preflight["git_head"],
    }
    validate_run_attestation(attestation, manifest)
    write_json_atomic_exclusive(attestation_path, attestation)
    write_json_atomic_exclusive(final_path, artifact)
    print(
        f"NONDETERMINISM_RUN_OK correct={artifact['correct']}/{EXPECTED_NEW_RUN_COUNT} "
        f"output={final_path.resolve()} sha256={_sha256(final_path)}",
        flush=True,
    )
    return final_path


def _analysis_markdown(analysis: dict[str, Any]) -> str:
    raw = analysis["raw_new_run"]
    combined = analysis["combined"]
    lines = [
        "# DeepSeek nondeterminism Stage 1 result",
        "",
        (
            f"> **Result:** {raw['recoveries']} new-error recoveries, "
            f"{raw['regressions']} control regressions, and "
            f"{raw['gross_flips']} raw verdict flips."
        ),
        "",
        "## Population-weighted estimate",
        "",
        f"- Net drift: `{combined['net_verdicts']:.3f}` verdicts.",
        f"- Gross instability: `{combined['gross_verdicts']:.3f}` verdicts.",
        f"- Net scale versus 25-verdict gap: `{combined['net_scale_vs_25']:.3f}`.",
        f"- Gross scale versus 25-verdict gap: `{combined['gross_scale_vs_25']:.3f}`.",
        "",
        "## Proof boundary",
        "",
        analysis["uncertainty_limitation"],
        "",
    ]
    return "\n".join(lines)


def command_analyze(args: argparse.Namespace) -> Path:
    manifest = _load_json_object(_selection_path(args.analysis_root, args.study_id))
    if selection_manifest_sha256(manifest) != EXPECTED_SELECTION_MANIFEST_SHA256:
        raise ValueError("saved selection manifest hash drifted")
    current_manifest = _frozen_manifest(
        args.study_id,
        DEFAULT_SOURCE,
        DEFAULT_REFERENCE,
        DEFAULT_PILOT_SUMMARY,
        DEFAULT_RESUME_LOG,
    )
    if current_manifest != manifest:
        raise ValueError("analysis inputs differ from the frozen selection")
    candidate = load_amb_result(DEFAULT_SOURCE)
    pilot_summary = _load_json_object(DEFAULT_PILOT_SUMMARY)
    rerun_path = _final_result_path(args.analysis_root, args.study_id)
    rerun = load_amb_result(rerun_path)
    attestation = _load_json_object(
        _study_root(args.analysis_root, args.study_id) / "run/retain-attestation.json"
    )
    validate_run_attestation(attestation, manifest)
    if _sha256(rerun_path) != attestation["result_file_sha256"]:
        raise ValueError("run result hash does not match its attestation")
    run_dir = _study_root(args.analysis_root, args.study_id) / "run"
    trace_paths = {
        "retain_batch_trace": run_dir / "retain-batches.jsonl",
        "omb_completion_trace": run_dir / "omb-completions.jsonl",
        "hindsight_completion_trace": run_dir / "hindsight-completions.jsonl",
    }
    for field, trace_path in trace_paths.items():
        if _sha256(trace_path) != attestation[field]["sha256"]:
            raise ValueError(f"{field} hash does not match its attestation")
    preflight = _load_json_object(_preflight_path(args.analysis_root, args.study_id))
    validate_live_model_probes(preflight.get("live_model_probes"))
    validate_hindsight_canary(preflight.get("hindsight_canary"))
    for trace_field in ("retain_batch_trace", "hindsight_completion_trace"):
        trace = preflight["hindsight_canary"][trace_field]
        if _sha256(Path(trace["path"])) != trace["sha256"]:
            raise ValueError(f"saved Hindsight canary {trace_field} hash drifted")
    if (
        preflight.get("code_sha256") != attestation["code_sha256"]
        or preflight.get("git_head") != attestation["git_head"]
        or preflight.get("profile") != attestation["profile"]
    ):
        raise ValueError("run attestation does not match the live preflight")
    analysis = analyze_study(
        candidate,
        pilot_summary,
        rerun,
        manifest,
        population_by_type=POPULATION_BY_TYPE,
    )
    analysis["run_result_sha256"] = _sha256(rerun_path)
    analysis["run_attestation_sha256"] = _sha256(
        _study_root(args.analysis_root, args.study_id)
        / "run/retain-attestation.json"
    )
    study_root = _study_root(args.analysis_root, args.study_id)
    analysis_path = study_root / "analysis.json"
    markdown_path = study_root / "analysis.md"
    write_json_atomic_exclusive(analysis_path, analysis)
    _write_text_atomic_exclusive(markdown_path, _analysis_markdown(analysis))
    print("NONDETERMINISM_ANALYSIS_OK", flush=True)
    print(f"analysis={analysis_path.resolve()}", flush=True)
    print(f"report={markdown_path.resolve()}", flush=True)
    return analysis_path


def _add_study_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--study-id", default=DEFAULT_STUDY_ID)
    parser.add_argument("--analysis-root", type=Path, default=DEFAULT_ANALYSIS_ROOT)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    select_parser = commands.add_parser("select", help="build the frozen selection")
    _add_study_arguments(select_parser)
    select_parser.add_argument("--candidate", type=Path, default=DEFAULT_SOURCE)
    select_parser.add_argument("--reference", type=Path, default=DEFAULT_REFERENCE)
    select_parser.add_argument(
        "--pilot-summary",
        type=Path,
        default=DEFAULT_PILOT_SUMMARY,
    )
    select_parser.add_argument("--resume-log", type=Path, default=DEFAULT_RESUME_LOG)
    select_parser.set_defaults(handler=command_select)

    preflight_parser = commands.add_parser(
        "preflight",
        help="validate frozen inputs, runtime roles, state, and live models",
    )
    _add_study_arguments(preflight_parser)
    preflight_parser.add_argument("--live", action="store_true")
    preflight_parser.add_argument(
        "--confirm-supplier-versions",
        action="store_true",
        help="confirm Flash 0731, Pro 0813, and default thinking high",
    )
    preflight_parser.set_defaults(handler=command_preflight)

    run_parser = commands.add_parser("run", help="run the frozen new 55 once")
    _add_study_arguments(run_parser)
    run_parser.set_defaults(handler=command_run)

    analyze_parser = commands.add_parser(
        "analyze",
        help="analyze the completed run without model calls",
    )
    _add_study_arguments(analyze_parser)
    analyze_parser.set_defaults(handler=command_analyze)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    args.handler(args)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileExistsError, OSError, RuntimeError, ValueError) as error:
        print(f"Error: {error}", file=os.sys.stderr)
        raise SystemExit(2) from None
