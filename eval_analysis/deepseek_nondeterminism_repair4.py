#!/usr/bin/env python3
"""Repair the four incomplete questions in the frozen DeepSeek study."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
from contextlib import contextmanager
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from typing import Any

import deepseek_nondeterminism as study
from amb_report import load_amb_result
from hindsight_repair import (
    _redacted_runtime_config,
    _repair_profile,
    _repair_profile_paths,
    _sha256,
    _validate_expected_runtime_configuration,
)
from variance_probe import (
    EXPECTED_RESOLVED_MODEL_BY_ROLE,
    _load_jsonl,
    _trace_omb_completions,
    close_and_verify_hindsight_daemon,
    disable_opaque_openai_retries,
    summarize_completion_events,
)

REPAIR_ID = "repair-4"
REPAIR_QUERY_IDS = ("5c40ec5b", "1568498a", "f685340e", "18dcd5a5")
REPAIR_QUERY_ORDINALS = (1, 16, 26, 43)
REPAIR_DOCUMENT_COUNT = 189
REPAIR_RETAIN_BATCH_COUNT = 26
ORIGINAL_COMPLETED_QUERY_COUNT = 51
ORIGINAL_COMPLETED_DOCUMENT_COUNT = 2465
ORIGINAL_COMPLETED_RETAIN_BATCH_COUNT = 329
REPAIR_DAEMON_IDLE_TIMEOUT_SECONDS = 0
REPAIR_PREFLIGHT_MAX_AGE_SECONDS = 15 * 60
LONGMEMEVAL_SPLIT_BANK_ID = "longmemeval-s"
REPAIR_NAMESPACE = f"nondeterminism-{study.DEFAULT_STUDY_ID}-{REPAIR_ID}"
ORIGINAL_NAMESPACE = f"nondeterminism-{study.DEFAULT_STUDY_ID}"
ORIGINAL_MAIN_LOG = (
    study.REPOSITORY_ROOT / "run-artifacts/2028-0819->0822/"
    "longmemeval-hindsight-deepseek-nondeterminism-55.log"
)
REPAIR_LAUNCHER_PATH = "eval_analysis/run_deepseek_nondeterminism_repair_4.sh"
REPAIR_MODULE_PATH = "eval_analysis/deepseek_nondeterminism_repair4.py"
REPAIR_CODE_PATHS = (*study.CODE_PATHS, REPAIR_MODULE_PATH, REPAIR_LAUNCHER_PATH)


def _repair_root(analysis_root: Path, study_id: str) -> Path:
    return study._study_root(analysis_root, study_id) / REPAIR_ID


def _plan_path(analysis_root: Path, study_id: str) -> Path:
    return _repair_root(analysis_root, study_id) / "plan.json"


def _repair_preflight_path(analysis_root: Path, study_id: str) -> Path:
    return _repair_root(analysis_root, study_id) / "preflight.json"


def _repair_result_path(analysis_root: Path, study_id: str) -> Path:
    return _repair_root(analysis_root, study_id) / "run/s.json"


def _repair_attestation_path(analysis_root: Path, study_id: str) -> Path:
    return _repair_root(analysis_root, study_id) / "run/retain-attestation.json"


def _composite_attestation_path(analysis_root: Path, study_id: str) -> Path:
    return study._study_root(analysis_root, study_id) / "run/retain-attestation.json"


def write_text_create_or_verify(path: Path, text: str) -> None:
    if path.exists():
        if path.read_text() != text:
            raise RuntimeError(f"immutable artifact drifted: {path}")
        return
    try:
        study._write_text_atomic_exclusive(path, text)
    except FileExistsError:
        if path.read_text() != text:
            raise RuntimeError(f"immutable artifact drifted: {path}") from None


def write_json_create_or_verify(path: Path, payload: Any) -> None:
    write_text_create_or_verify(path, study._json_file_text(payload))


def load_partial_journal(
    journal_dir: Path,
    selected_query_ids: tuple[str, ...],
) -> tuple[dict[str, dict[str, Any]], tuple[str, ...]]:
    expected_paths = {
        f"{index:03d}-{query_id}.json": query_id
        for index, query_id in enumerate(selected_query_ids, start=1)
    }
    actual_paths = sorted(journal_dir.glob("*.json")) if journal_dir.exists() else []
    unexpected = [path.name for path in actual_paths if path.name not in expected_paths]
    if unexpected:
        raise ValueError(f"journal filename drifted: {unexpected}")
    results: dict[str, dict[str, Any]] = {}
    for path in actual_paths:
        query_id = expected_paths[path.name]
        payload = study._load_json_object(path)
        if payload.get("query_id") != query_id:
            raise ValueError(f"journal query identity drifted: {path}")
        if query_id in results:
            raise ValueError(f"duplicate journal query: {query_id}")
        results[query_id] = payload
    ordered = {
        query_id: results[query_id]
        for query_id in selected_query_ids
        if query_id in results
    }
    missing = tuple(
        query_id for query_id in selected_query_ids if query_id not in ordered
    )
    return ordered, missing


def _artifact_statistics(results: list[dict[str, Any]]) -> dict[str, Any]:
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
    return {
        "total_queries": len(results),
        "correct": sum(result.get("correct") is True for result in results),
        "accuracy": (
            sum(result.get("correct") is True for result in results) / len(results)
        ),
        "avg_retrieve_time_ms": round(sum(retrieval_times) / len(retrieval_times), 1),
        "avg_context_tokens": round(sum(context_tokens) / len(context_tokens), 1),
    }


def build_merged_artifact(
    *,
    original_results: dict[str, dict[str, Any]],
    repair_results: dict[str, dict[str, Any]],
    selected_query_ids: tuple[str, ...],
    repair_query_ids: tuple[str, ...],
    document_count: int,
    study_id: str,
) -> dict[str, Any]:
    repair_set = set(repair_query_ids)
    selected_set = set(selected_query_ids)
    if set(repair_results) != repair_set or set(original_results) != (
        selected_set - repair_set
    ):
        raise ValueError("repair result membership does not match the frozen split")
    if set(original_results) & set(repair_results):
        raise ValueError("repair result membership overlaps completed results")
    results = [
        repair_results.get(query_id, original_results.get(query_id))
        for query_id in selected_query_ids
    ]
    if any(result is None for result in results):
        raise ValueError("merged journal is incomplete")
    typed_results = [result for result in results if result is not None]
    statistics = _artifact_statistics(typed_results)
    return {
        "dataset": "longmemeval",
        "split": "s",
        "category": None,
        "memory_provider": "hindsight",
        "run_name": f"nondeterminism-{study_id}",
        "mode": "rag",
        "oracle": False,
        **statistics,
        "ingestion_time_ms": None,
        "ingested_docs": document_count,
        "description": (
            "Frozen DeepSeek nondeterminism study composite: "
            "51 original journals plus four create-only repair journals."
        ),
        "answer_llm": "openai:deepseek-v4-pro",
        "judge_llm": "openai:deepseek-v4-flash",
        "results": typed_results,
    }


def _require_sha256(value: Any, label: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError(f"{label} SHA-256 is invalid")


def validate_recorded_repair_code_provenance(
    plan: dict[str, Any],
    preflight: dict[str, Any],
    attestation: dict[str, Any] | None = None,
    *,
    require_current_code_hashes: bool = False,
) -> None:
    code_sha256 = plan.get("code_sha256")
    git_head = plan.get("git_head")
    if (
        not isinstance(code_sha256, dict)
        or not code_sha256
        or not isinstance(git_head, str)
    ):
        raise ValueError("repair-4 recorded code provenance is incomplete")
    for record in (preflight, attestation):
        if record is not None and (
            record.get("code_sha256") != code_sha256
            or record.get("git_head") != git_head
        ):
            raise ValueError("repair-4 recorded code provenance drifted")
    if require_current_code_hashes and _repair_code_hashes() != code_sha256:
        raise ValueError("repair-4 current code differs from recorded provenance")


def validate_repair_attestation(
    attestation: dict[str, Any],
    plan: dict[str, Any],
) -> None:
    required = {
        "study_id",
        "repair_query_ids",
        "profile",
        "daemon_idle_timeout_seconds",
        "daemon_running_postflight",
        "document_count",
        "confirmed_document_ids_sha256",
        "retain_batch_trace",
        "omb_completion_trace",
        "hindsight_completion_trace",
        "result_evidence",
        "repair_journal_sha256",
        "result_file_sha256",
        "preflight_sha256",
        "code_sha256",
        "git_head",
    }
    if not isinstance(attestation, dict) or not required <= set(attestation):
        raise ValueError("repair attestation is incomplete")
    repair_ids = tuple(plan["repair_query_ids"])
    if (
        attestation.get("study_id") != plan.get("study_id")
        or tuple(attestation.get("repair_query_ids") or ()) != repair_ids
        or attestation.get("document_count") != plan.get("repair_document_count")
        or set(attestation.get("confirmed_document_ids_sha256") or {})
        != set(repair_ids)
        or set(attestation.get("result_evidence") or {}) != set(repair_ids)
        or set(attestation.get("repair_journal_sha256") or {}) != set(repair_ids)
        or attestation.get("daemon_running_postflight") is not False
    ):
        raise ValueError("repair attestation identity or completeness drifted")
    if attestation.get("daemon_idle_timeout_seconds") != 0:
        raise ValueError("repair attestation idle timeout is not disabled")
    if (
        not isinstance(attestation.get("profile"), str)
        or not attestation["profile"]
        or attestation["profile"] == plan.get("original_profile")
    ):
        raise ValueError("repair attestation did not use a fresh profile")
    retain = attestation["retain_batch_trace"]
    omb = attestation["omb_completion_trace"]
    hindsight = attestation["hindsight_completion_trace"]
    expected_batches = plan["repair_retain_batch_count"]
    if (
        retain.get("expected_batches") != expected_batches
        or retain.get("successful_batches") != expected_batches
        or omb.get("calls_by_role", {}).get("answer", 0) < len(repair_ids)
        or omb.get("calls_by_role", {}).get("judge", 0) < len(repair_ids)
        or hindsight.get("calls_by_role", {}).get("hindsight_extraction", 0)
        < expected_batches
        or hindsight.get("calls_by_role", {}).get("hindsight_verification") != 1
    ):
        raise ValueError("repair attestation trace summaries are incomplete")
    for label, trace in (
        ("repair retain trace", retain),
        ("repair OMB trace", omb),
        ("repair Hindsight trace", hindsight),
    ):
        _require_sha256(trace.get("sha256"), label)
    _require_sha256(attestation.get("result_file_sha256"), "repair result")
    _require_sha256(attestation.get("preflight_sha256"), "repair preflight")
    if (
        not isinstance(attestation.get("code_sha256"), dict)
        or not attestation["code_sha256"]
    ):
        raise ValueError("repair attestation code hashes are incomplete")


def validate_merged_attestation(
    attestation: dict[str, Any],
    plan: dict[str, Any],
    repair_attestation: dict[str, Any],
) -> None:
    selected = tuple(plan["selected_query_ids"])
    completed = set(plan["completed_query_ids"])
    repaired = set(plan["repair_query_ids"])
    if (
        attestation.get("schema_version") != 2
        or attestation.get("study_id") != plan.get("study_id")
        or attestation.get("selection_sha256") != plan.get("selection_sha256")
        or attestation.get("merge_mode") != "original-51-plus-repair-4"
        or tuple(attestation.get("query_ids") or ()) != selected
        or set(attestation.get("result_evidence") or {}) != set(selected)
        or set(attestation.get("original_journal_sha256") or {}) != completed
        or set(attestation.get("repair_journal_sha256") or {}) != repaired
    ):
        raise ValueError("merged attestation identity or completeness drifted")
    if completed & repaired or completed | repaired != set(selected):
        raise ValueError("merged attestation source membership drifted")
    profiles = attestation.get("profiles") or {}
    documents = attestation.get("document_evidence") or {}
    retains = attestation.get("retain_evidence") or {}
    failure = attestation.get("original_hindsight_failure_provenance") or {}
    expected_original_documents = plan.get("original_completed_document_count")
    expected_repair_documents = plan.get("repair_document_count")
    expected_original_batches = plan.get("original_completed_retain_batch_count")
    expected_repair_batches = plan.get("repair_retain_batch_count")
    if (
        profiles
        != {
            "original": plan.get("original_profile"),
            "repair": repair_attestation.get("profile"),
        }
        or documents
        != {
            "original_completed_questions": len(completed),
            "original_completed_documents": expected_original_documents,
            "repair_questions": len(repaired),
            "repair_documents": expected_repair_documents,
            "total_documents": expected_original_documents + expected_repair_documents,
        }
        or retains
        != {
            "original_complete_batches": expected_original_batches,
            "repair_batches": expected_repair_batches,
            "total_batches": expected_original_batches + expected_repair_batches,
        }
        or attestation.get("source_sha256") != plan.get("source_sha256")
        or not isinstance(failure.get("unpaired_attempts"), int)
        or failure["unpaired_attempts"] <= 0
        or failure.get("attempts", 0) - failure.get("terminals", 0)
        != failure["unpaired_attempts"]
    ):
        raise ValueError("merged attestation component evidence drifted")
    for label in (
        "result_file_sha256",
        "repair_attestation_sha256",
        "plan_sha256",
    ):
        _require_sha256(attestation.get(label), f"merged {label}")
    canary = attestation.get("canary_evidence")
    expected_canary_fields = {
        "original_preflight_sha256",
        "original_retain_trace_sha256",
        "original_hindsight_trace_sha256",
        "repair_preflight_sha256",
        "repair_retain_trace_sha256",
        "repair_hindsight_trace_sha256",
    }
    if not isinstance(canary, dict) or set(canary) != expected_canary_fields:
        raise ValueError("merged attestation canary evidence is incomplete")
    if canary["original_preflight_sha256"] != plan.get(
        "original_preflight_sha256"
    ) or canary["repair_preflight_sha256"] != repair_attestation.get(
        "preflight_sha256"
    ):
        raise ValueError("merged attestation canary evidence drifted")
    for field, value in canary.items():
        _require_sha256(value, f"merged canary {field}")
    if tuple(repair_attestation.get("repair_query_ids") or ()) != tuple(
        plan["repair_query_ids"]
    ):
        raise ValueError("merged attestation repair component drifted")


def _load_environment_for_repair() -> Path:
    study._load_environment()
    os.environ["HINDSIGHT_EMBED_DAEMON_IDLE_TIMEOUT"] = str(
        REPAIR_DAEMON_IDLE_TIMEOUT_SECONDS
    )
    data_path = _validate_expected_runtime_configuration()
    if os.environ.get("HINDSIGHT_EMBED_DAEMON_IDLE_TIMEOUT") != "0":
        raise RuntimeError("failed to disable the Hindsight daemon idle timeout")
    return data_path


def _repair_code_hashes() -> dict[str, str]:
    return {
        relative_path: _sha256(study.REPOSITORY_ROOT / relative_path)
        for relative_path in REPAIR_CODE_PATHS
    }


def _git_head() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=study.REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _validate_historical_code(preflight: dict[str, Any]) -> None:
    git_head = preflight.get("git_head")
    code_hashes = preflight.get("code_sha256")
    if not isinstance(git_head, str) or not isinstance(code_hashes, dict):
        raise ValueError("original preflight code identity is incomplete")
    for relative_path, expected_hash in code_hashes.items():
        result = subprocess.run(
            ["git", "show", f"{git_head}:{relative_path}"],
            cwd=study.REPOSITORY_ROOT,
            check=True,
            capture_output=True,
        )
        actual_hash = hashlib.sha256(result.stdout).hexdigest()
        if actual_hash != expected_hash:
            raise ValueError(
                f"original preflight code hash drifted for {relative_path}"
            )


def _frozen_manifest(analysis_root: Path, study_id: str) -> dict[str, Any]:
    manifest = study._load_json_object(study._selection_path(analysis_root, study_id))
    current = study._frozen_manifest(
        study_id,
        study.DEFAULT_SOURCE,
        study.DEFAULT_REFERENCE,
        study.DEFAULT_PILOT_SUMMARY,
        study.DEFAULT_RESUME_LOG,
    )
    if manifest != current:
        raise ValueError("repair selection differs from the frozen manifest")
    if (
        study.selection_manifest_sha256(manifest)
        != study.EXPECTED_SELECTION_MANIFEST_SHA256
    ):
        raise ValueError("repair selection manifest hash drifted")
    return manifest


@contextmanager
def _bound_dataset_path(data_path: Path):
    key = "LONGMEMEVAL_DATA_PATH"
    had_value = key in os.environ
    previous = os.environ.get(key)
    os.environ[key] = str(data_path.resolve())
    try:
        yield
    finally:
        if had_value and previous is not None:
            os.environ[key] = previous
        else:
            os.environ.pop(key, None)


def _load_dataset(
    selected_query_ids: tuple[str, ...],
    data_path: Path | None = None,
) -> tuple[Any, list[Any], dict[str, list[str]], dict[str, set[str]]]:
    def load():
        dataset = study.SelectedLongMemEvalDataset(selected_query_ids)
        queries = dataset.load_queries("s", limit=500)
        if tuple(query.id for query in queries) != selected_query_ids:
            raise ValueError("repair dataset query order or membership drifted")
        documents = dataset.load_documents("s", user_ids=set(selected_query_ids))
        ordered_ids = {query_id: [] for query_id in selected_query_ids}
        id_sets = {query_id: set() for query_id in selected_query_ids}
        for document in documents:
            query_id = document.user_id
            if query_id not in ordered_ids:
                raise ValueError(
                    f"repair dataset contains an unselected document: {document.id}"
                )
            if document.id not in id_sets[query_id]:
                id_sets[query_id].add(document.id)
                ordered_ids[query_id].append(document.id)
        if any(not id_sets[query_id] for query_id in selected_query_ids):
            raise ValueError("repair dataset contains an empty question unit")
        return dataset, documents, ordered_ids, id_sets

    if data_path is None:
        return load()
    with _bound_dataset_path(data_path):
        return load()


def _expected_batch_ids(
    namespace: str,
    document_ids_by_query: dict[str, list[str]],
    query_ids: tuple[str, ...],
) -> set[str]:
    batch_ids = set()
    for query_id in query_ids:
        bank_id = f"{LONGMEMEVAL_SPLIT_BANK_ID}-repair-{namespace}-u{query_id}"
        document_ids = document_ids_by_query[query_id]
        for start in range(
            0,
            len(document_ids),
            study.StudyHindsightMemoryProvider._ASYNC_BATCH_SIZE,
        ):
            batch_ids.add(
                study.canonical_json_sha256(
                    {
                        "bank_id": bank_id,
                        "document_ids": document_ids[
                            start : start
                            + study.StudyHindsightMemoryProvider._ASYNC_BATCH_SIZE
                        ],
                    }
                )
            )
    return batch_ids


def _profile_is_running(profile: str) -> bool:
    from hindsight_embed.daemon_embed_manager import DaemonEmbedManager

    return DaemonEmbedManager().is_running(profile)


def _path_hash_map(paths: dict[str, Path]) -> dict[str, str]:
    return {name: _sha256(path) for name, path in paths.items()}


def _validate_original_failure_logs(
    main_log: Path,
    daemon_log: Path,
) -> None:
    main_text = main_log.read_text()
    daemon_text = daemon_log.read_text()
    if (
        "NONDETERMINISM_PREFLIGHT_OK" not in main_text
        or "NONDETERMINISM_DAEMON_STOPPED" not in main_text
        or "Error: Hindsight did not confirm every retain batch" not in main_text
        or "NONDETERMINISM_RUN_OK" in main_text
        or "NONDETERMINISM_ANALYSIS_OK" in main_text
    ):
        raise ValueError("original failure log markers drifted")
    if (
        "Idle timeout reached (300s)" not in daemon_text
        or "Cancel 4 running task(s)" not in daemon_text
    ):
        raise ValueError("original daemon root-cause markers drifted")


def _validate_original_plan_artifacts(plan: dict[str, Any]) -> None:
    for query_id, path_value in plan["original_journal_paths"].items():
        path = Path(path_value)
        if _sha256(path) != plan["original_journal_sha256"][query_id]:
            raise ValueError(f"original journal hash drifted: {query_id}")
    for name, path_value in plan["original_trace_paths"].items():
        if _sha256(Path(path_value)) != plan["original_trace_sha256"][name]:
            raise ValueError(f"original trace hash drifted: {name}")
    if _sha256(Path(plan["selection_path"])) != plan["selection_file_sha256"]:
        raise ValueError("repair plan artifact hash drifted: selection")
    for prefix in (
        "original_preflight",
        "original_main_log",
        "original_daemon_log",
    ):
        if _sha256(Path(plan[f"{prefix}_path"])) != plan[f"{prefix}_sha256"]:
            raise ValueError(f"repair plan artifact hash drifted: {prefix}")


def _build_repair_plan(
    analysis_root: Path,
    study_id: str,
    data_path: Path,
) -> dict[str, Any]:
    manifest = _frozen_manifest(analysis_root, study_id)
    selected = tuple(manifest["new_error_query_ids"] + manifest["control_query_ids"])
    original_root = study._study_root(analysis_root, study_id)
    original_results, missing = load_partial_journal(
        original_root / "journal", selected
    )
    if missing != REPAIR_QUERY_IDS:
        raise ValueError(f"repair missing-query set drifted: {missing}")
    if len(original_results) != ORIGINAL_COMPLETED_QUERY_COUNT:
        raise ValueError("repair expected exactly 51 completed original journals")
    for forbidden in (
        study._final_result_path(analysis_root, study_id),
        _composite_attestation_path(analysis_root, study_id),
        original_root / "analysis.json",
        original_root / "analysis.md",
    ):
        if forbidden.exists():
            raise FileExistsError(f"repair final state already exists: {forbidden}")

    _, _, ordered_ids, id_sets = _load_dataset(selected, data_path)
    completed = tuple(query_id for query_id in selected if query_id in original_results)
    completed_documents = sum(len(id_sets[query_id]) for query_id in completed)
    repair_documents = sum(len(id_sets[query_id]) for query_id in REPAIR_QUERY_IDS)
    if (
        completed_documents != ORIGINAL_COMPLETED_DOCUMENT_COUNT
        or repair_documents != REPAIR_DOCUMENT_COUNT
    ):
        raise ValueError("repair document counts drifted")
    for query_id in completed:
        study.validate_retrieval_isolation(
            original_results[query_id],
            expected_document_ids=id_sets[query_id],
        )

    original_trace_paths = {
        "retain": original_root / "run/retain-batches.jsonl",
        "omb": original_root / "run/omb-completions.jsonl",
        "hindsight": original_root / "run/hindsight-completions.jsonl",
    }
    expected_batches = _expected_batch_ids(ORIGINAL_NAMESPACE, ordered_ids, completed)
    if len(expected_batches) != ORIGINAL_COMPLETED_RETAIN_BATCH_COUNT:
        raise ValueError("original completed retain-batch count drifted")
    retain_events = _load_jsonl(original_trace_paths["retain"])
    accepted_retain_events = [
        event for event in retain_events if event.get("batch_id") in expected_batches
    ]
    retain_summary = study.summarize_retain_receipts(
        accepted_retain_events,
        expected_batches,
    )
    omb_events = _load_jsonl(original_trace_paths["omb"])
    completed_set = set(completed)
    accepted_omb_events = [
        event for event in omb_events if event.get("scope") in completed_set
    ]
    omb_summary = summarize_completion_events(
        accepted_omb_events,
        {"answer": len(completed), "judge": len(completed)},
        EXPECTED_RESOLVED_MODEL_BY_ROLE,
    )
    completion_metadata = study.completion_metadata_by_query(
        accepted_omb_events,
        completed,
    )
    original_result_evidence = study._result_evidence(
        {"results": [original_results[query_id] for query_id in completed]},
        completion_metadata,
    )

    original_preflight_path = study._preflight_path(analysis_root, study_id)
    original_preflight = study._load_json_object(original_preflight_path)
    study.validate_live_model_probes(original_preflight.get("live_model_probes"))
    study.validate_hindsight_canary(original_preflight.get("hindsight_canary"))
    for trace_field in ("retain_batch_trace", "hindsight_completion_trace"):
        trace = original_preflight["hindsight_canary"][trace_field]
        if _sha256(Path(trace["path"])) != trace["sha256"]:
            raise ValueError(f"original canary {trace_field} hash drifted")
    _validate_historical_code(original_preflight)
    original_profile = original_preflight.get("profile")
    if not isinstance(original_profile, str) or _profile_is_running(original_profile):
        raise RuntimeError("original Hindsight profile is unexpectedly running")
    original_daemon_log, original_database = _repair_profile_paths(original_profile)
    _validate_original_failure_logs(ORIGINAL_MAIN_LOG, original_daemon_log)

    repair_profile = _repair_profile(f"longmemeval-s-repair-{REPAIR_NAMESPACE}")
    repair_daemon_log, repair_database = _repair_profile_paths(repair_profile)
    for profile_path in (repair_daemon_log, repair_database):
        if profile_path.exists():
            raise FileExistsError(
                f"repair paid profile already has state: {profile_path}"
            )

    journal_paths = {
        query_id: original_root
        / "journal"
        / f"{selected.index(query_id) + 1:03d}-{query_id}.json"
        for query_id in completed
    }
    plan = {
        "schema_version": 1,
        "study_id": study_id,
        "repair_id": REPAIR_ID,
        "selection_sha256": study.EXPECTED_SELECTION_MANIFEST_SHA256,
        "selected_query_ids": list(selected),
        "completed_query_ids": list(completed),
        "repair_query_ids": list(REPAIR_QUERY_IDS),
        "repair_query_ordinals": list(REPAIR_QUERY_ORDINALS),
        "original_profile": original_profile,
        "repair_profile": repair_profile,
        "daemon_idle_timeout_seconds": REPAIR_DAEMON_IDLE_TIMEOUT_SECONDS,
        "original_completed_document_count": completed_documents,
        "original_completed_retain_batch_count": len(expected_batches),
        "repair_document_count": repair_documents,
        "repair_retain_batch_count": REPAIR_RETAIN_BATCH_COUNT,
        "selection_path": str(study._selection_path(analysis_root, study_id).resolve()),
        "selection_file_sha256": _sha256(
            study._selection_path(analysis_root, study_id)
        ),
        "original_preflight_path": str(original_preflight_path.resolve()),
        "original_preflight_sha256": _sha256(original_preflight_path),
        "original_main_log_path": str(ORIGINAL_MAIN_LOG.resolve()),
        "original_main_log_sha256": _sha256(ORIGINAL_MAIN_LOG),
        "original_daemon_log_path": str(original_daemon_log.resolve()),
        "original_daemon_log_sha256": _sha256(original_daemon_log),
        "original_database_path": str(original_database.resolve()),
        "repair_daemon_log_path": str(repair_daemon_log.resolve()),
        "repair_database_path": str(repair_database.resolve()),
        "original_journal_paths": {
            query_id: str(path.resolve()) for query_id, path in journal_paths.items()
        },
        "original_journal_sha256": {
            query_id: _sha256(path) for query_id, path in journal_paths.items()
        },
        "original_trace_paths": {
            name: str(path.resolve()) for name, path in original_trace_paths.items()
        },
        "original_trace_sha256": _path_hash_map(original_trace_paths),
        "original_clean_retain_summary": retain_summary,
        "original_clean_omb_summary": omb_summary,
        "original_result_evidence": original_result_evidence,
        "original_canary": original_preflight["hindsight_canary"],
        "original_code_sha256": original_preflight["code_sha256"],
        "original_git_head": original_preflight["git_head"],
        "source_sha256": {
            "candidate": study.EXPECTED_CANDIDATE_SHA256,
            "reference_gzip": study.EXPECTED_REFERENCE_GZIP_SHA256,
            "pilot": study.EXPECTED_PILOT_SHA256,
            "resume_log": study.EXPECTED_RESUME_LOG_SHA256,
            "dataset": study.EXPECTED_DATASET_SHA256,
        },
        "dataset_path": str(data_path.resolve()),
        "code_sha256": _repair_code_hashes(),
        "git_head": _git_head(),
    }
    _validate_original_plan_artifacts(plan)
    return plan


def _validate_repair_canary(preflight: dict[str, Any]) -> None:
    canary = preflight.get("hindsight_canary")
    study.validate_hindsight_canary(canary)
    if canary.get("profile") == preflight.get("profile"):
        raise ValueError("repair canary reused the paid profile")
    for trace_field in ("retain_batch_trace", "hindsight_completion_trace"):
        trace = canary[trace_field]
        if _sha256(Path(trace["path"])) != trace["sha256"]:
            raise ValueError(f"repair canary {trace_field} hash drifted")


def command_preflight(args: argparse.Namespace) -> Path:
    if args.study_id != study.DEFAULT_STUDY_ID:
        raise ValueError("repair-4 is frozen to the Stage 1 study ID")
    if not args.live:
        raise ValueError("repair-4 preflight requires --live")
    if not args.confirm_supplier_versions:
        raise ValueError(
            "repair-4 requires --confirm-supplier-versions after manually confirming "
            "Flash 0731, Pro 0813, and default thinking high"
        )
    repair_root = _repair_root(args.analysis_root, args.study_id)
    if repair_root.exists():
        raise FileExistsError(f"repair-4 state already exists: {repair_root}")
    data_path = _load_environment_for_repair()
    study._validate_hash(
        data_path, study.EXPECTED_DATASET_SHA256, "LongMemEval dataset"
    )
    if version("hindsight-embed") != "0.4.17":
        raise ValueError("installed hindsight-embed version drifted")
    plan = _build_repair_plan(args.analysis_root, args.study_id, data_path)
    plan_path = _plan_path(args.analysis_root, args.study_id)
    study.write_json_atomic_exclusive(plan_path, plan)

    from memory_bench.llm import get_answer_llm, get_judge_llm

    live_probes = [
        study._live_model_probe(get_answer_llm(), "deepseek-v4-pro"),
        study._live_model_probe(get_judge_llm(), "deepseek-v4-flash"),
    ]
    study.validate_live_model_probes(live_probes)
    canary = study._run_hindsight_canary(
        repair_root,
        f"{args.study_id}-{REPAIR_ID}",
    )
    now = datetime.now(timezone.utc)
    preflight = {
        "schema_version": 1,
        "study_id": args.study_id,
        "repair_id": REPAIR_ID,
        "created_at": now.isoformat(),
        "expires_at_epoch": now.timestamp() + REPAIR_PREFLIGHT_MAX_AGE_SECONDS,
        "live": True,
        "plan_path": str(plan_path.resolve()),
        "plan_sha256": _sha256(plan_path),
        "profile": plan["repair_profile"],
        "daemon_idle_timeout_seconds": REPAIR_DAEMON_IDLE_TIMEOUT_SECONDS,
        "query_count": len(REPAIR_QUERY_IDS),
        "document_count": REPAIR_DOCUMENT_COUNT,
        "retain_batch_count": REPAIR_RETAIN_BATCH_COUNT,
        "runtime_config": _redacted_runtime_config(),
        "code_sha256": _repair_code_hashes(),
        "git_head": _git_head(),
        "live_model_probes": live_probes,
        "hindsight_canary": canary,
        "manual_supplier_confirmation": {
            "confirmed_at": now.isoformat(),
            "flash_version": "0731",
            "pro_version": "0813",
            "default_thinking": "high",
        },
    }
    _validate_repair_canary(preflight)
    preflight_path = _repair_preflight_path(args.analysis_root, args.study_id)
    study.write_json_atomic_exclusive(preflight_path, preflight)
    print("NONDETERMINISM_REPAIR4_PREFLIGHT_OK", flush=True)
    print(f"plan={plan_path.resolve()} sha256={_sha256(plan_path)}", flush=True)
    print(f"preflight={preflight_path.resolve()}", flush=True)
    print(f"profile={plan['repair_profile']}", flush=True)
    print(
        "queries=4 documents=189 retain_batches=26 daemon_idle_timeout_seconds=0",
        flush=True,
    )
    return preflight_path


def _validate_repair_preflight_for_run(
    analysis_root: Path,
    study_id: str,
) -> tuple[dict[str, Any], dict[str, Any], Path]:
    plan_path = _plan_path(analysis_root, study_id)
    preflight_path = _repair_preflight_path(analysis_root, study_id)
    plan = study._load_json_object(plan_path)
    preflight = study._load_json_object(preflight_path)
    if (
        preflight.get("study_id") != study_id
        or preflight.get("repair_id") != REPAIR_ID
        or preflight.get("live") is not True
        or preflight.get("plan_sha256") != _sha256(plan_path)
        or preflight.get("profile") != plan.get("repair_profile")
        or preflight.get("daemon_idle_timeout_seconds") != 0
        or preflight.get("query_count") != len(REPAIR_QUERY_IDS)
        or preflight.get("document_count") != REPAIR_DOCUMENT_COUNT
        or preflight.get("retain_batch_count") != REPAIR_RETAIN_BATCH_COUNT
    ):
        raise ValueError("repair-4 preflight identity or scope drifted")
    if datetime.now(timezone.utc).timestamp() > preflight.get("expires_at_epoch", 0):
        raise ValueError("repair-4 live preflight expired")
    confirmation = preflight.get("manual_supplier_confirmation") or {}
    if (
        confirmation.get("flash_version") != "0731"
        or confirmation.get("pro_version") != "0813"
        or confirmation.get("default_thinking") != "high"
    ):
        raise ValueError("repair-4 supplier confirmation drifted")
    data_path = _load_environment_for_repair()
    if preflight.get("runtime_config") != _redacted_runtime_config():
        raise ValueError("repair-4 runtime configuration changed after preflight")
    validate_recorded_repair_code_provenance(
        plan,
        preflight,
        require_current_code_hashes=True,
    )
    if preflight.get("git_head") != _git_head():
        raise ValueError("repair-4 git HEAD changed after preflight")
    study.validate_live_model_probes(preflight.get("live_model_probes"))
    _validate_repair_canary(preflight)
    _validate_original_plan_artifacts(plan)
    study._validate_hash(
        data_path, study.EXPECTED_DATASET_SHA256, "LongMemEval dataset"
    )
    return plan, preflight, data_path


def _build_repair_artifact(
    results_by_id: dict[str, dict[str, Any]],
    study_id: str,
) -> dict[str, Any]:
    if set(results_by_id) != set(REPAIR_QUERY_IDS):
        raise ValueError("repair journal is incomplete")
    results = [results_by_id[query_id] for query_id in REPAIR_QUERY_IDS]
    return {
        "dataset": "longmemeval",
        "split": "s",
        "category": None,
        "memory_provider": "hindsight",
        "run_name": f"nondeterminism-{study_id}-{REPAIR_ID}",
        "mode": "rag",
        "oracle": False,
        **_artifact_statistics(results),
        "ingestion_time_ms": None,
        "ingested_docs": REPAIR_DOCUMENT_COUNT,
        "description": (
            "Create-only repair of the four incomplete questions in the frozen "
            "DeepSeek nondeterminism study."
        ),
        "answer_llm": "openai:deepseek-v4-pro",
        "judge_llm": "openai:deepseek-v4-flash",
        "results": results,
    }


def _repair_journal_paths(
    analysis_root: Path,
    study_id: str,
) -> dict[str, Path]:
    journal_dir = _repair_root(analysis_root, study_id) / "journal"
    return {
        query_id: journal_dir / f"{index:03d}-{query_id}.json"
        for index, query_id in enumerate(REPAIR_QUERY_IDS, start=1)
    }


def _build_repair_attestation(
    *,
    analysis_root: Path,
    study_id: str,
    plan: dict[str, Any],
    preflight: dict[str, Any],
    artifact: dict[str, Any],
    confirmed_document_ids_sha256: dict[str, str],
    retain_summary: dict[str, Any],
    omb_summary: dict[str, Any],
    hindsight_summary: dict[str, Any],
    result_evidence: dict[str, Any],
) -> dict[str, Any]:
    run_dir = _repair_root(analysis_root, study_id) / "run"
    journal_paths = _repair_journal_paths(analysis_root, study_id)
    return {
        "schema_version": 1,
        "study_id": study_id,
        "repair_id": REPAIR_ID,
        "repair_query_ids": list(REPAIR_QUERY_IDS),
        "namespace": REPAIR_NAMESPACE,
        "profile": plan["repair_profile"],
        "daemon_idle_timeout_seconds": REPAIR_DAEMON_IDLE_TIMEOUT_SECONDS,
        "daemon_running_postflight": False,
        "document_count": REPAIR_DOCUMENT_COUNT,
        "confirmed_document_ids_sha256": confirmed_document_ids_sha256,
        "repair_journal_paths": {
            query_id: str(path.resolve()) for query_id, path in journal_paths.items()
        },
        "repair_journal_sha256": {
            query_id: _sha256(path) for query_id, path in journal_paths.items()
        },
        "retain_batch_trace": {
            "path": str((run_dir / "retain-batches.jsonl").resolve()),
            "sha256": _sha256(run_dir / "retain-batches.jsonl"),
            **retain_summary,
        },
        "omb_completion_trace": {
            "path": str((run_dir / "omb-completions.jsonl").resolve()),
            "sha256": _sha256(run_dir / "omb-completions.jsonl"),
            **omb_summary,
        },
        "hindsight_completion_trace": {
            "path": str((run_dir / "hindsight-completions.jsonl").resolve()),
            "sha256": _sha256(run_dir / "hindsight-completions.jsonl"),
            **hindsight_summary,
        },
        "result_evidence": result_evidence,
        "result_file_sha256": study._json_file_sha256(artifact),
        "plan_sha256": preflight["plan_sha256"],
        "preflight_sha256": _sha256(_repair_preflight_path(analysis_root, study_id)),
        "source_sha256": plan["source_sha256"],
        "code_sha256": preflight["code_sha256"],
        "git_head": preflight["git_head"],
    }


def command_run(args: argparse.Namespace) -> Path:
    plan, preflight, data_path = _validate_repair_preflight_for_run(
        args.analysis_root,
        args.study_id,
    )
    repair_root = _repair_root(args.analysis_root, args.study_id)
    run_dir = repair_root / "run"
    journal_dir = repair_root / "journal"
    work_dir = repair_root / "work"
    repair_result_path = _repair_result_path(args.analysis_root, args.study_id)
    attestation_path = _repair_attestation_path(args.analysis_root, args.study_id)
    omb_trace_path = run_dir / "omb-completions.jsonl"
    hindsight_trace_path = run_dir / "hindsight-completions.jsonl"
    batch_trace_path = run_dir / "retain-batches.jsonl"
    for path in (
        journal_dir,
        work_dir,
        repair_result_path,
        attestation_path,
        omb_trace_path,
        hindsight_trace_path,
        batch_trace_path,
    ):
        if path.exists():
            raise FileExistsError(f"repair-4 paid state already exists: {path}")
    expected_profile = plan["repair_profile"]
    for profile_path in _repair_profile_paths(expected_profile):
        if profile_path.exists():
            raise FileExistsError(
                f"repair-4 paid profile already has state: {profile_path}"
            )

    dataset, documents, ordered_ids, id_sets = _load_dataset(
        REPAIR_QUERY_IDS,
        data_path,
    )
    if len(documents) != REPAIR_DOCUMENT_COUNT:
        raise ValueError("repair-4 document count drifted")
    expected_batch_ids = _expected_batch_ids(
        REPAIR_NAMESPACE,
        ordered_ids,
        REPAIR_QUERY_IDS,
    )
    if len(expected_batch_ids) != REPAIR_RETAIN_BATCH_COUNT:
        raise ValueError("repair-4 retain-batch count drifted")

    from memory_bench.llm import get_answer_llm
    from memory_bench.modes import get_mode

    provider = study.StudyHindsightMemoryProvider(REPAIR_NAMESPACE, batch_trace_path)
    runner = study.JournaledEvalRunner(
        output_dir=work_dir,
        selected_query_ids=REPAIR_QUERY_IDS,
        journal_dir=journal_dir,
    )
    summary = None
    stopped_profile = None
    try:
        os.environ["AMB_VARIANCE_HINDSIGHT_TRACE_PATH"] = str(
            hindsight_trace_path.resolve()
        )
        os.environ["HINDSIGHT_EMBED_DAEMON_IDLE_TIMEOUT"] = "0"
        answer_llm = get_answer_llm()
        disable_opaque_openai_retries(answer_llm)
        disable_opaque_openai_retries(runner._judge._llm)
        print(
            f"NONDETERMINISM_REPAIR4_RUN_START study={args.study_id} "
            f"queries={len(REPAIR_QUERY_IDS)} profile={expected_profile} "
            "daemon_idle_timeout_seconds=0",
            flush=True,
        )
        with _trace_omb_completions(omb_trace_path):
            summary = runner.run(
                dataset=dataset,
                split="s",
                memory=provider,
                mode=get_mode("rag", llm=answer_llm),
                query_limit=500,
                run_name=f"nondeterminism-{args.study_id}-{REPAIR_ID}",
                description=(
                    "Failure-specific four-question DeepSeek nondeterminism repair; "
                    f"study={args.study_id}; repair={REPAIR_ID}; "
                    f"plan_sha256={preflight['plan_sha256']}; "
                    f"profile={expected_profile}."
                ),
            )
        if summary.total_queries != len(REPAIR_QUERY_IDS):
            raise RuntimeError(
                f"repair-4 returned {summary.total_queries} results; expected 4"
            )
        if summary.ingested_docs != REPAIR_DOCUMENT_COUNT:
            raise RuntimeError(
                f"repair-4 reported {summary.ingested_docs} documents; expected 189"
            )
        repair_results, missing = load_partial_journal(journal_dir, REPAIR_QUERY_IDS)
        if missing:
            raise RuntimeError(f"repair-4 journal is incomplete: {missing}")
        artifact = _build_repair_artifact(repair_results, args.study_id)
        for query_id in REPAIR_QUERY_IDS:
            study.validate_retrieval_isolation(
                repair_results[query_id],
                expected_document_ids=id_sets[query_id],
            )
        if set(provider.confirmed_document_ids_sha256) != set(REPAIR_QUERY_IDS):
            raise RuntimeError("repair-4 document attestations are incomplete")
        retain_summary = study.summarize_retain_receipts(
            _load_jsonl(batch_trace_path),
            expected_batch_ids,
        )
        omb_events = _load_jsonl(omb_trace_path)
        hindsight_events = _load_jsonl(hindsight_trace_path)
        omb_summary = summarize_completion_events(
            omb_events,
            {"answer": len(REPAIR_QUERY_IDS), "judge": len(REPAIR_QUERY_IDS)},
            EXPECTED_RESOLVED_MODEL_BY_ROLE,
        )
        hindsight_summary = summarize_completion_events(
            hindsight_events,
            {
                "hindsight_extraction": REPAIR_RETAIN_BATCH_COUNT,
                "hindsight_verification": 1,
            },
            EXPECTED_RESOLVED_MODEL_BY_ROLE,
        )
        completion_metadata = study._validate_scoped_completion_pairs(
            omb_events,
            REPAIR_QUERY_IDS,
        )
        result_evidence = study._result_evidence(artifact, completion_metadata)
    finally:
        os.environ.pop("AMB_VARIANCE_HINDSIGHT_TRACE_PATH", None)
        stopped_profile = close_and_verify_hindsight_daemon(provider)
        if stopped_profile is not None:
            print(
                f"NONDETERMINISM_REPAIR4_DAEMON_STOPPED profile={stopped_profile}",
                flush=True,
            )

    if summary is None or stopped_profile != expected_profile:
        raise RuntimeError("repair-4 did not complete with a verified daemon shutdown")
    _validate_original_plan_artifacts(plan)
    for path, expected, label in (
        (study.DEFAULT_SOURCE, study.EXPECTED_CANDIDATE_SHA256, "candidate"),
        (
            study.DEFAULT_REFERENCE,
            study.EXPECTED_REFERENCE_GZIP_SHA256,
            "reference gzip",
        ),
        (study.DEFAULT_PILOT_SUMMARY, study.EXPECTED_PILOT_SHA256, "pilot summary"),
        (study.DEFAULT_RESUME_LOG, study.EXPECTED_RESUME_LOG_SHA256, "resume-1 log"),
        (data_path, study.EXPECTED_DATASET_SHA256, "LongMemEval dataset"),
    ):
        study._validate_hash(path, expected, label)
    if _repair_code_hashes() != preflight["code_sha256"]:
        raise RuntimeError("repair-4 code changed during the paid run")
    if _git_head() != preflight["git_head"]:
        raise RuntimeError("repair-4 git HEAD changed during the paid run")
    attestation = _build_repair_attestation(
        analysis_root=args.analysis_root,
        study_id=args.study_id,
        plan=plan,
        preflight=preflight,
        artifact=artifact,
        confirmed_document_ids_sha256=provider.confirmed_document_ids_sha256,
        retain_summary=retain_summary,
        omb_summary=omb_summary,
        hindsight_summary=hindsight_summary,
        result_evidence=result_evidence,
    )
    validate_repair_attestation(attestation, plan)
    write_json_create_or_verify(attestation_path, attestation)
    write_json_create_or_verify(repair_result_path, artifact)
    print(
        f"NONDETERMINISM_REPAIR4_RUN_OK correct={artifact['correct']}/4 "
        f"output={repair_result_path.resolve()} sha256={_sha256(repair_result_path)}",
        flush=True,
    )
    return repair_result_path


def _validate_repair_envelope(
    analysis_root: Path,
    study_id: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    plan_path = _plan_path(analysis_root, study_id)
    preflight_path = _repair_preflight_path(analysis_root, study_id)
    attestation_path = _repair_attestation_path(analysis_root, study_id)
    plan = study._load_json_object(plan_path)
    preflight = study._load_json_object(preflight_path)
    attestation = study._load_json_object(attestation_path)
    _validate_original_plan_artifacts(plan)
    validate_repair_attestation(attestation, plan)
    validate_recorded_repair_code_provenance(plan, preflight, attestation)
    if (
        attestation.get("plan_sha256") != _sha256(plan_path)
        or attestation.get("preflight_sha256") != _sha256(preflight_path)
        or preflight.get("plan_sha256") != _sha256(plan_path)
        or preflight.get("code_sha256") != attestation.get("code_sha256")
        or preflight.get("git_head") != attestation.get("git_head")
        or preflight.get("profile") != attestation.get("profile")
        or attestation.get("source_sha256") != plan.get("source_sha256")
    ):
        raise ValueError("repair-4 component hash or identity drifted")
    study.validate_live_model_probes(preflight.get("live_model_probes"))
    _validate_repair_canary(preflight)
    for query_id, path_value in attestation["repair_journal_paths"].items():
        if _sha256(Path(path_value)) != attestation["repair_journal_sha256"][query_id]:
            raise ValueError(f"repair-4 journal hash drifted: {query_id}")
    for trace_field in (
        "retain_batch_trace",
        "omb_completion_trace",
        "hindsight_completion_trace",
    ):
        trace = attestation[trace_field]
        if _sha256(Path(trace["path"])) != trace["sha256"]:
            raise ValueError(f"repair-4 {trace_field} hash drifted")
    return plan, preflight, attestation


def _validate_repair_component(
    analysis_root: Path,
    study_id: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    plan, preflight, attestation = _validate_repair_envelope(
        analysis_root,
        study_id,
    )
    result_path = _repair_result_path(analysis_root, study_id)
    artifact = load_amb_result(result_path)
    if attestation.get("result_file_sha256") != _sha256(result_path):
        raise ValueError("repair-4 result hash drifted")
    result_by_id = study._result_by_query_id(artifact)
    if tuple(result_by_id) != REPAIR_QUERY_IDS:
        raise ValueError("repair-4 result order or membership drifted")
    return plan, preflight, attestation, artifact


def _publish_repair_result_if_needed(analysis_root: Path, study_id: str) -> None:
    result_path = _repair_result_path(analysis_root, study_id)
    if result_path.exists():
        return
    plan, _, attestation = _validate_repair_envelope(
        analysis_root,
        study_id,
    )
    repair_results = {
        query_id: study._load_json_object(Path(path_value))
        for query_id, path_value in attestation["repair_journal_paths"].items()
    }
    artifact = _build_repair_artifact(repair_results, study_id)
    if study._json_file_sha256(artifact) != attestation["result_file_sha256"]:
        raise ValueError("repair-4 reconstructed result hash drifted")
    write_json_create_or_verify(result_path, artifact)


def _seal_repair_if_needed(analysis_root: Path, study_id: str) -> None:
    attestation_path = _repair_attestation_path(analysis_root, study_id)
    if attestation_path.exists():
        return
    plan_path = _plan_path(analysis_root, study_id)
    preflight_path = _repair_preflight_path(analysis_root, study_id)
    plan = study._load_json_object(plan_path)
    preflight = study._load_json_object(preflight_path)
    validate_recorded_repair_code_provenance(
        plan,
        preflight,
        require_current_code_hashes=True,
    )
    if (
        preflight.get("plan_sha256") != _sha256(plan_path)
        or preflight.get("profile") != plan.get("repair_profile")
        or preflight.get("daemon_idle_timeout_seconds") != 0
    ):
        raise ValueError("repair-4 offline seal identity drifted")
    _validate_original_plan_artifacts(plan)
    for path, expected, label in (
        (study.DEFAULT_SOURCE, study.EXPECTED_CANDIDATE_SHA256, "candidate"),
        (
            study.DEFAULT_REFERENCE,
            study.EXPECTED_REFERENCE_GZIP_SHA256,
            "reference gzip",
        ),
        (study.DEFAULT_PILOT_SUMMARY, study.EXPECTED_PILOT_SHA256, "pilot summary"),
        (study.DEFAULT_RESUME_LOG, study.EXPECTED_RESUME_LOG_SHA256, "resume-1 log"),
        (
            Path(plan["dataset_path"]),
            study.EXPECTED_DATASET_SHA256,
            "LongMemEval dataset",
        ),
    ):
        study._validate_hash(path, expected, label)
    study.validate_live_model_probes(preflight.get("live_model_probes"))
    _validate_repair_canary(preflight)
    if _profile_is_running(plan["repair_profile"]):
        raise RuntimeError("repair-4 paid daemon is still running; refuse offline seal")

    repair_root = _repair_root(analysis_root, study_id)
    journal_dir = repair_root / "journal"
    run_dir = repair_root / "run"
    repair_results, missing = load_partial_journal(journal_dir, REPAIR_QUERY_IDS)
    if missing:
        raise RuntimeError(f"repair-4 cannot seal an incomplete journal: {missing}")
    _, documents, ordered_ids, id_sets = _load_dataset(
        REPAIR_QUERY_IDS,
        Path(plan["dataset_path"]),
    )
    if len(documents) != REPAIR_DOCUMENT_COUNT:
        raise ValueError("repair-4 offline seal document count drifted")
    for query_id in REPAIR_QUERY_IDS:
        study.validate_retrieval_isolation(
            repair_results[query_id],
            expected_document_ids=id_sets[query_id],
        )
    expected_batch_ids = _expected_batch_ids(
        REPAIR_NAMESPACE,
        ordered_ids,
        REPAIR_QUERY_IDS,
    )
    retain_summary = study.summarize_retain_receipts(
        _load_jsonl(run_dir / "retain-batches.jsonl"),
        expected_batch_ids,
    )
    omb_events = _load_jsonl(run_dir / "omb-completions.jsonl")
    hindsight_events = _load_jsonl(run_dir / "hindsight-completions.jsonl")
    omb_summary = summarize_completion_events(
        omb_events,
        {"answer": len(REPAIR_QUERY_IDS), "judge": len(REPAIR_QUERY_IDS)},
        EXPECTED_RESOLVED_MODEL_BY_ROLE,
    )
    hindsight_summary = summarize_completion_events(
        hindsight_events,
        {
            "hindsight_extraction": REPAIR_RETAIN_BATCH_COUNT,
            "hindsight_verification": 1,
        },
        EXPECTED_RESOLVED_MODEL_BY_ROLE,
    )
    completion_metadata = study._validate_scoped_completion_pairs(
        omb_events,
        REPAIR_QUERY_IDS,
    )
    artifact = _build_repair_artifact(repair_results, study_id)
    result_evidence = study._result_evidence(artifact, completion_metadata)
    confirmed_document_ids_sha256 = {
        query_id: study._identifier_set_sha256(id_sets[query_id])
        for query_id in REPAIR_QUERY_IDS
    }
    attestation = _build_repair_attestation(
        analysis_root=analysis_root,
        study_id=study_id,
        plan=plan,
        preflight=preflight,
        artifact=artifact,
        confirmed_document_ids_sha256=confirmed_document_ids_sha256,
        retain_summary=retain_summary,
        omb_summary=omb_summary,
        hindsight_summary=hindsight_summary,
        result_evidence=result_evidence,
    )
    validate_repair_attestation(attestation, plan)
    write_json_create_or_verify(attestation_path, attestation)
    print(
        "NONDETERMINISM_REPAIR4_OFFLINE_SEAL_OK source=immutable-journals-and-traces",
        flush=True,
    )


def _original_hindsight_failure_provenance(plan: dict[str, Any]) -> dict[str, int]:
    events = _load_jsonl(Path(plan["original_trace_paths"]["hindsight"]))
    attempts = {
        event["call_id"] for event in events if event.get("event_type") == "attempt"
    }
    terminals = {
        event["call_id"]
        for event in events
        if event.get("event_type") in {"success", "failure"}
    }
    return {
        "attempts": len(attempts),
        "terminals": len(terminals),
        "unpaired_attempts": len(attempts - terminals),
    }


def command_finalize(args: argparse.Namespace) -> Path:
    _seal_repair_if_needed(args.analysis_root, args.study_id)
    _publish_repair_result_if_needed(args.analysis_root, args.study_id)
    plan, preflight, repair_attestation, repair_artifact = _validate_repair_component(
        args.analysis_root,
        args.study_id,
    )
    selected = tuple(plan["selected_query_ids"])
    original_results = {
        query_id: study._load_json_object(Path(path_value))
        for query_id, path_value in plan["original_journal_paths"].items()
    }
    repair_results = study._result_by_query_id(repair_artifact)
    artifact = build_merged_artifact(
        original_results=original_results,
        repair_results=repair_results,
        selected_query_ids=selected,
        repair_query_ids=tuple(plan["repair_query_ids"]),
        document_count=study.EXPECTED_DOCUMENT_COUNT,
        study_id=args.study_id,
    )
    result_evidence = {
        **plan["original_result_evidence"],
        **repair_attestation["result_evidence"],
    }
    canonical_journal_paths = {
        query_id: study._study_root(args.analysis_root, args.study_id)
        / "journal"
        / f"{ordinal:03d}-{query_id}.json"
        for query_id, ordinal in zip(REPAIR_QUERY_IDS, REPAIR_QUERY_ORDINALS)
    }
    canonical_journal_sha256 = {
        query_id: study._json_file_sha256(repair_results[query_id])
        for query_id in REPAIR_QUERY_IDS
    }
    merged_attestation = {
        "schema_version": 2,
        "study_id": args.study_id,
        "selection_sha256": plan["selection_sha256"],
        "merge_mode": "original-51-plus-repair-4",
        "query_ids": list(selected),
        "profiles": {
            "original": plan["original_profile"],
            "repair": repair_attestation["profile"],
        },
        "daemon_idle_timeout_seconds": {
            "original": 300,
            "repair": REPAIR_DAEMON_IDLE_TIMEOUT_SECONDS,
        },
        "document_evidence": {
            "original_completed_questions": ORIGINAL_COMPLETED_QUERY_COUNT,
            "original_completed_documents": ORIGINAL_COMPLETED_DOCUMENT_COUNT,
            "repair_questions": len(REPAIR_QUERY_IDS),
            "repair_documents": REPAIR_DOCUMENT_COUNT,
            "total_documents": study.EXPECTED_DOCUMENT_COUNT,
        },
        "retain_evidence": {
            "original_complete_batches": ORIGINAL_COMPLETED_RETAIN_BATCH_COUNT,
            "repair_batches": REPAIR_RETAIN_BATCH_COUNT,
            "total_batches": study.EXPECTED_RETAIN_BATCH_COUNT,
        },
        "canary_evidence": {
            "original_preflight_sha256": plan["original_preflight_sha256"],
            "original_retain_trace_sha256": plan["original_canary"][
                "retain_batch_trace"
            ]["sha256"],
            "original_hindsight_trace_sha256": plan["original_canary"][
                "hindsight_completion_trace"
            ]["sha256"],
            "repair_preflight_sha256": _sha256(
                _repair_preflight_path(args.analysis_root, args.study_id)
            ),
            "repair_retain_trace_sha256": preflight["hindsight_canary"][
                "retain_batch_trace"
            ]["sha256"],
            "repair_hindsight_trace_sha256": preflight["hindsight_canary"][
                "hindsight_completion_trace"
            ]["sha256"],
        },
        "original_trace_sha256": plan["original_trace_sha256"],
        "original_hindsight_failure_provenance": (
            _original_hindsight_failure_provenance(plan)
        ),
        "repair_trace_sha256": {
            "retain": repair_attestation["retain_batch_trace"]["sha256"],
            "omb": repair_attestation["omb_completion_trace"]["sha256"],
            "hindsight": repair_attestation["hindsight_completion_trace"]["sha256"],
        },
        "result_evidence": result_evidence,
        "original_journal_sha256": plan["original_journal_sha256"],
        "repair_journal_sha256": canonical_journal_sha256,
        "repair_source_journal_sha256": repair_attestation["repair_journal_sha256"],
        "canonical_repair_journal_paths": {
            query_id: str(path.resolve())
            for query_id, path in canonical_journal_paths.items()
        },
        "result_file_sha256": study._json_file_sha256(artifact),
        "repair_attestation_sha256": _sha256(
            _repair_attestation_path(args.analysis_root, args.study_id)
        ),
        "plan_sha256": _sha256(_plan_path(args.analysis_root, args.study_id)),
        "source_sha256": plan["source_sha256"],
        "original_code_sha256": plan["original_code_sha256"],
        "repair_code_sha256": repair_attestation["code_sha256"],
        "original_git_head": plan["original_git_head"],
        "repair_git_head": repair_attestation["git_head"],
    }
    validate_merged_attestation(merged_attestation, plan, repair_attestation)
    composite_attestation_path = _composite_attestation_path(
        args.analysis_root,
        args.study_id,
    )
    write_json_create_or_verify(composite_attestation_path, merged_attestation)
    for query_id, path in canonical_journal_paths.items():
        write_json_create_or_verify(path, repair_results[query_id])
    canonical_results, missing = load_partial_journal(
        study._study_root(args.analysis_root, args.study_id) / "journal",
        selected,
    )
    if missing or canonical_results != {
        **original_results,
        **repair_results,
    }:
        raise RuntimeError("canonical 55-question journal failed finalization")
    final_path = study._final_result_path(args.analysis_root, args.study_id)
    write_json_create_or_verify(final_path, artifact)
    if _sha256(final_path) != merged_attestation["result_file_sha256"]:
        raise RuntimeError("composite result hash differs from its attestation")
    print(
        f"NONDETERMINISM_REPAIR4_FINALIZE_OK correct={artifact['correct']}/55 "
        f"output={final_path.resolve()} sha256={_sha256(final_path)}",
        flush=True,
    )
    return final_path


def _validate_composite(
    analysis_root: Path,
    study_id: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    plan, _, repair_attestation, _ = _validate_repair_component(
        analysis_root,
        study_id,
    )
    attestation_path = _composite_attestation_path(analysis_root, study_id)
    result_path = study._final_result_path(analysis_root, study_id)
    attestation = study._load_json_object(attestation_path)
    result = load_amb_result(result_path)
    validate_merged_attestation(attestation, plan, repair_attestation)
    if (
        _sha256(result_path) != attestation["result_file_sha256"]
        or _sha256(_plan_path(analysis_root, study_id)) != attestation["plan_sha256"]
        or _sha256(_repair_attestation_path(analysis_root, study_id))
        != attestation["repair_attestation_sha256"]
    ):
        raise ValueError("composite result hash differs from its attestation")
    selected = tuple(plan["selected_query_ids"])
    if tuple(study._result_by_query_id(result)) != selected:
        raise ValueError("composite result order or membership drifted")
    expected_result_evidence = {
        **plan["original_result_evidence"],
        **repair_attestation["result_evidence"],
    }
    if attestation.get("result_evidence") != expected_result_evidence:
        raise ValueError("composite result evidence differs from its sources")
    for query_id, path_value in attestation["canonical_repair_journal_paths"].items():
        if _sha256(Path(path_value)) != attestation["repair_journal_sha256"][query_id]:
            raise ValueError(f"canonical repair journal hash drifted: {query_id}")
    return plan, repair_attestation, attestation, result


def command_analyze(args: argparse.Namespace) -> Path:
    plan, _, attestation, rerun = _validate_composite(
        args.analysis_root,
        args.study_id,
    )
    manifest = _frozen_manifest(args.analysis_root, args.study_id)
    candidate = load_amb_result(study.DEFAULT_SOURCE)
    pilot_summary = study._load_json_object(study.DEFAULT_PILOT_SUMMARY)
    analysis = study.analyze_study(
        candidate,
        pilot_summary,
        rerun,
        manifest,
        population_by_type=study.POPULATION_BY_TYPE,
    )
    final_path = study._final_result_path(args.analysis_root, args.study_id)
    attestation_path = _composite_attestation_path(args.analysis_root, args.study_id)
    analysis.update(
        {
            "run_result_sha256": _sha256(final_path),
            "run_attestation_sha256": _sha256(attestation_path),
            "run_composition": {
                "original_questions": len(plan["completed_query_ids"]),
                "repair_questions": len(plan["repair_query_ids"]),
                "merge_mode": attestation["merge_mode"],
            },
        }
    )
    root = study._study_root(args.analysis_root, args.study_id)
    analysis_path = root / "analysis.json"
    markdown_path = root / "analysis.md"
    write_json_create_or_verify(analysis_path, analysis)
    write_text_create_or_verify(markdown_path, study._analysis_markdown(analysis))
    print("NONDETERMINISM_REPAIR4_ANALYSIS_OK", flush=True)
    print(f"analysis={analysis_path.resolve()}", flush=True)
    print(f"report={markdown_path.resolve()}", flush=True)
    return analysis_path


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--study-id", default=study.DEFAULT_STUDY_ID)
    parser.add_argument(
        "--analysis-root",
        type=Path,
        default=study.DEFAULT_ANALYSIS_ROOT,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    preflight_parser = commands.add_parser("preflight")
    _add_common_arguments(preflight_parser)
    preflight_parser.add_argument("--live", action="store_true")
    preflight_parser.add_argument("--confirm-supplier-versions", action="store_true")
    preflight_parser.set_defaults(handler=command_preflight)
    for name, handler in (
        ("run", command_run),
        ("finalize", command_finalize),
        ("analyze", command_analyze),
    ):
        command = commands.add_parser(name)
        _add_common_arguments(command)
        command.set_defaults(handler=handler)
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
