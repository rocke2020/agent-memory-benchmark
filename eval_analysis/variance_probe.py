#!/usr/bin/env python3
"""Run isolated full-pipeline replicas for a selected LongMemEval error pilot."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections.abc import Iterable
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from amb_report import load_amb_result
from hindsight_repair import (
    DEFAULT_SOURCE,
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
from variance_trace import (
    canonical_json_sha256,
    make_sync_completion_wrapper,
    semantic_raw_projection,
)

from memory_bench.dataset.longmemeval import LongMemEvalDataset

PILOT_ERROR_QUERY_IDS = (
    "0ddfec37_abs",
    "0a995998",
    "15745da0",
    "b0479f84",
    "gpt4_f420262d",
)
DEFAULT_REPLICA_COUNT = 3
MAX_REPLICA_COUNT = 5
DEFAULT_VARIANCE_ROOT = Path(__file__).resolve().parent / "variance-results"
EXPECTED_RESOLVED_MODEL_BY_ROLE = {
    "answer": "deepseek-v4-pro",
    "judge": "deepseek-v4-flash",
    "hindsight_extraction": "deepseek-v4-flash",
    "hindsight_verification": "deepseek-v4-flash",
}


def _by_query_id(artifact: dict[str, Any]) -> dict[str, dict[str, Any]]:
    results = artifact.get("results")
    if not isinstance(results, list):
        raise ValueError("artifact must contain a results list")  # noqa: TRY004
    by_id = {}
    for result in results:
        if not isinstance(result, dict) or not result.get("query_id"):
            raise ValueError("every result must contain a query_id")
        query_id = result["query_id"]
        if query_id in by_id:
            raise ValueError(f"duplicate query ID: {query_id}")
        by_id[query_id] = result
    return by_id


def validate_pilot_inputs(
    candidate: dict[str, Any],
    reference: dict[str, Any],
    query_ids: Iterable[str],
) -> tuple[str, ...]:
    selected = tuple(query_ids)
    if not selected or len(set(selected)) != len(selected):
        raise ValueError("pilot query IDs must be non-empty and unique")
    candidate_by_id = _by_query_id(candidate)
    reference_by_id = _by_query_id(reference)
    missing = set(selected) - (set(candidate_by_id) & set(reference_by_id))
    if missing:
        raise ValueError(f"pilot query IDs are missing: {sorted(missing)}")

    input_fields = ("query", "gold_answers", "meta")
    for query_id in selected:
        candidate_result = candidate_by_id[query_id]
        reference_result = reference_by_id[query_id]
        if any(
            candidate_result.get(field) != reference_result.get(field)
            for field in input_fields
        ):
            raise ValueError(f"input mismatch for pilot query {query_id}")
    if any(
        candidate_by_id[query_id].get("correct") is not False for query_id in selected
    ):
        raise ValueError("pilot must contain only candidate errors")
    if any(
        reference_by_id[query_id].get("correct") is not True for query_id in selected
    ):
        raise ValueError("pilot must contain only questions the reference passed")
    return selected


class SelectedLongMemEvalDataset(LongMemEvalDataset):
    """LongMemEval view that exposes only an explicit query-ID allowlist."""

    def __init__(self, query_ids: Iterable[str]) -> None:
        super().__init__()
        self.selected_query_ids = tuple(query_ids)
        if not self.selected_query_ids or len(set(self.selected_query_ids)) != len(
            self.selected_query_ids
        ):
            raise ValueError("selected query IDs must be non-empty and unique")

    def load_queries(
        self,
        split: str,
        category: str | None = None,
        limit: int | None = None,
    ):
        all_queries = super().load_queries(split, category=category, limit=None)
        by_id = {query.id: query for query in all_queries}
        selected = [
            by_id[query_id] for query_id in self.selected_query_ids if query_id in by_id
        ]
        return selected[:limit] if limit else selected


def replica_namespaces(experiment: str, replicas: int) -> tuple[str, ...]:
    experiment = _slug(experiment)
    if replicas < 2:
        raise ValueError("a nondeterminism probe requires at least two replicas")
    if replicas > MAX_REPLICA_COUNT:
        raise ValueError(f"replicas cannot exceed {MAX_REPLICA_COUNT}")
    return tuple(
        f"variance-{experiment}-r{replica_index}"
        for replica_index in range(1, replicas + 1)
    )


def expected_retain_batch_count(
    document_ids_by_query: dict[str, set[str]],
    batch_size: int,
) -> int:
    if batch_size < 1:
        raise ValueError("retain batch size must be positive")
    return sum(
        (len(document_ids) + batch_size - 1) // batch_size
        for document_ids in document_ids_by_query.values()
    )


def _text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _json_sha256(value: Any) -> str:
    return canonical_json_sha256(value)


def _normalized_answer_sha256(value: str) -> str:
    normalized = " ".join(value.casefold().split())
    return _text_sha256(normalized)


def _source_document_ids_sha256(raw_response: Any) -> str:
    document_ids = set()
    if isinstance(raw_response, dict):
        for result in raw_response.get("results") or []:
            if isinstance(result, dict) and result.get("document_id"):
                document_ids.add(result["document_id"])
    return _identifier_set_sha256(document_ids)


def _longmemeval_prompt_hashes(result: dict[str, Any]) -> tuple[str, str]:
    dataset = LongMemEvalDataset()
    meta = {
        **(result.get("meta") or {}),
        "_raw_response": result.get("raw_response"),
    }
    answer_prompt = dataset.build_rag_prompt(
        result.get("query") or "",
        result.get("context") or "",
        "open",
        "s",
        None,
        meta,
    )
    judge_prompt_fn = dataset.get_judge_prompt_fn(
        (result.get("meta") or {}).get("question_type"),
        meta=result.get("meta") or {},
    )
    judge_prompt = judge_prompt_fn(
        result.get("query") or "",
        result.get("gold_answers") or [],
        result.get("answer") or "",
    )
    return _text_sha256(answer_prompt), _text_sha256(judge_prompt)


def summarize_replicas(
    candidate: dict[str, Any],
    replicas: list[dict[str, Any]],
    query_ids: Iterable[str],
) -> dict[str, Any]:
    selected = tuple(query_ids)
    if len(replicas) < 2:
        raise ValueError("summary requires at least two replicas")
    candidate_by_id = _by_query_id(candidate)
    replica_maps = [_by_query_id(replica) for replica in replicas]
    selected_set = set(selected)
    for replica_by_id in replica_maps:
        if set(replica_by_id) != selected_set:
            raise ValueError("replica query IDs do not match the pilot query IDs")

    questions = []
    for query_id in selected:
        results = [replica_by_id[query_id] for replica_by_id in replica_maps]
        verdicts = [result.get("correct") is True for result in results]
        answer_hashes = [_text_sha256(result.get("answer") or "") for result in results]
        normalized_answer_hashes = [
            _normalized_answer_sha256(result.get("answer") or "") for result in results
        ]
        context_hashes = [
            _text_sha256(result.get("context") or "") for result in results
        ]
        raw_response_hashes = [
            _json_sha256(result.get("raw_response")) for result in results
        ]
        semantic_raw_hashes = [
            _json_sha256(semantic_raw_projection(result.get("raw_response")))
            for result in results
        ]
        source_document_hashes = [
            _source_document_ids_sha256(result.get("raw_response"))
            for result in results
        ]
        prompt_hashes = [_longmemeval_prompt_hashes(result) for result in results]
        questions.append(
            {
                "query_id": query_id,
                "original_correct": candidate_by_id[query_id].get("correct") is True,
                "replica_verdicts": verdicts,
                "pass_count": sum(verdicts),
                "mixed_replica_verdicts": len(set(verdicts)) > 1,
                "unique_answer_hashes": len(set(answer_hashes)),
                "unique_normalized_answer_hashes": len(set(normalized_answer_hashes)),
                "unique_context_hashes": len(set(context_hashes)),
                "unique_raw_response_hashes": len(set(raw_response_hashes)),
                "unique_semantic_raw_hashes": len(set(semantic_raw_hashes)),
                "unique_source_document_sets": len(set(source_document_hashes)),
                "answer_sha256": answer_hashes,
                "normalized_answer_sha256": normalized_answer_hashes,
                "context_sha256": context_hashes,
                "raw_response_sha256": raw_response_hashes,
                "semantic_raw_response_sha256": semantic_raw_hashes,
                "source_document_ids_sha256": source_document_hashes,
                "answer_user_prompt_sha256": [pair[0] for pair in prompt_hashes],
                "judge_user_prompt_sha256": [pair[1] for pair in prompt_hashes],
                "context_tokens": [result.get("context_tokens") for result in results],
                "judge_reasons": [result.get("judge_reason") for result in results],
            }
        )

    return {
        "replica_count": len(replicas),
        "replica_correct": [
            sum(replica_by_id[query_id].get("correct") is True for query_id in selected)
            for replica_by_id in replica_maps
        ],
        "questions": questions,
    }


def write_json_exclusive(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    events = []
    try:
        lines = path.read_text().splitlines()
    except FileNotFoundError as error:
        raise ValueError(f"completion trace is missing: {path}") from error
    for line_number, line in enumerate(lines, start=1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(
                f"invalid completion trace JSON at {path}:{line_number}"
            ) from error
        if not isinstance(event, dict):
            raise ValueError(  # noqa: TRY004
                f"invalid completion trace event at {path}:{line_number}"
            )
        events.append(event)
    return events


def summarize_completion_events(
    events: list[dict[str, Any]],
    minimum_calls_by_role: dict[str, int],
    expected_resolved_model_by_role: dict[str, str],
) -> dict[str, Any]:
    attempts = {}
    terminals = {}
    positions = {}
    for position, event in enumerate(events):
        event_type = event.get("event_type")
        call_id = event.get("call_id")
        role = event.get("role")
        if event_type not in {"attempt", "failure", "success"}:
            raise ValueError("completion trace event has an invalid event type")
        if not isinstance(call_id, str) or not call_id:
            raise ValueError("completion trace event is missing its call ID")
        if not isinstance(role, str) or not role:
            raise ValueError("completion trace event is missing its role")
        target = attempts if event_type == "attempt" else terminals
        if call_id in target:
            raise ValueError(f"completion trace has duplicate {event_type}: {call_id}")
        target[call_id] = event
        positions[(call_id, event_type)] = position

    missing_terminal = set(attempts) - set(terminals)
    missing_attempt = set(terminals) - set(attempts)
    if missing_terminal or missing_attempt:
        raise ValueError(
            "every completion attempt must have one terminal outcome: "
            f"missing_terminal={sorted(missing_terminal)} "
            f"missing_attempt={sorted(missing_attempt)}"
        )
    for call_id, attempt in attempts.items():
        terminal = terminals[call_id]
        if terminal["role"] != attempt["role"]:
            raise ValueError(f"completion trace role changed for call {call_id}")
        if (
            positions[(call_id, "attempt")]
            > positions[(call_id, terminal["event_type"])]
        ):
            raise ValueError(f"completion terminal preceded its attempt: {call_id}")
        messages_hash = attempt.get("messages_sha256")
        if not isinstance(messages_hash, str) or len(messages_hash) != 64:
            raise ValueError("completion attempt has an invalid messages hash")
        if attempt.get("sdk_max_retries") != 0:
            raise ValueError(
                "completion trace permits opaque SDK retries: "
                f"call_id={call_id} max_retries={attempt.get('sdk_max_retries')!r}"
            )
        if terminal["event_type"] == "success" and any(
            terminal.get(field) != attempt.get(field)
            for field in (
                "messages_sha256",
                "requested_model",
                "temperature",
                "thinking_requested",
            )
        ):
            raise ValueError(
                f"completion request metadata changed at success: {call_id}"
            )

    failures = [
        event for event in terminals.values() if event["event_type"] == "failure"
    ]
    if failures:
        raise ValueError(
            f"completion trace contains {len(failures)} failed completion attempts"
        )
    successful_events = [
        event for event in terminals.values() if event["event_type"] == "success"
    ]
    calls_by_role = {}
    for event in successful_events:
        role = event.get("role")
        if not event.get("resolved_model") or not event.get("request_id"):
            raise ValueError("completion trace event is missing response metadata")
        expected_model = expected_resolved_model_by_role.get(role)
        if expected_model is None or event["resolved_model"] != expected_model:
            raise ValueError(
                "completion trace resolved model drift: "
                f"role={role!r} expected={expected_model!r} "
                f"actual={event['resolved_model']!r}"
            )
        messages_hash = event.get("messages_sha256")
        if not isinstance(messages_hash, str) or len(messages_hash) != 64:
            raise ValueError("completion trace event has an invalid messages hash")
        usage = event.get("usage")
        if (
            not isinstance(usage, dict)
            or any(
                not isinstance(usage.get(field), int) or usage[field] < 0
                for field in ("input", "output", "total")
            )
            or usage["input"] + usage["output"] != usage["total"]
        ):
            raise ValueError("completion trace event has invalid usage")
        calls_by_role[role] = calls_by_role.get(role, 0) + 1
    missing = {
        role: minimum
        for role, minimum in minimum_calls_by_role.items()
        if calls_by_role.get(role, 0) < minimum
    }
    if missing:
        raise ValueError(f"completion trace is missing required calls: {missing}")
    return {
        "total_calls": len(successful_events),
        "attempt_calls": len(attempts),
        "successful_calls": len(successful_events),
        "failed_calls": len(failures),
        "calls_by_role": dict(sorted(calls_by_role.items())),
        "resolved_models": sorted(
            {event["resolved_model"] for event in successful_events}
        ),
        "thinking_requested": sorted(
            {
                json.dumps(event.get("thinking_requested"), sort_keys=True)
                for event in successful_events
            }
        ),
        "reasoning_content_calls": sum(
            event.get("reasoning_content_present") is True
            for event in successful_events
        ),
        "trace_messages_sha256": canonical_json_sha256(
            [event["messages_sha256"] for event in successful_events]
        ),
    }


def disable_opaque_openai_retries(llm: Any) -> None:
    client = getattr(llm, "_client", None)
    if client is None or not hasattr(client, "with_options"):
        raise RuntimeError("traced LLM does not expose an OpenAI client")
    llm._client = client.with_options(max_retries=0)
    if llm._client.max_retries != 0:
        raise RuntimeError("failed to disable opaque OpenAI SDK retries")


@contextmanager
def _trace_omb_completions(trace_path: Path):
    from openai.resources.chat.completions.completions import Completions

    original_create = Completions.create

    def role_for_params(params: dict[str, Any]) -> str:
        model = str(params.get("model") or "")
        return "answer" if model.endswith("v4-pro") else "judge"

    Completions.create = make_sync_completion_wrapper(
        original_create,
        trace_path,
        role_for_params,
    )
    try:
        yield
    finally:
        Completions.create = original_create


def _replica_result_path(
    experiment_root: Path,
    experiment: str,
    replica_index: int,
) -> Path:
    run_name = f"variance-{experiment}-r{replica_index}"
    return (
        experiment_root
        / f"r{replica_index}"
        / "longmemeval"
        / run_name
        / "rag"
        / "s.json"
    )


def _validate_new_replica_profiles(namespaces: tuple[str, ...]) -> dict[str, str]:
    profiles = {}
    for namespace in namespaces:
        profile = _repair_profile(f"longmemeval-s-repair-{namespace}")
        for path in _repair_profile_paths(profile):
            if path.exists():
                raise FileExistsError(
                    f"variance profile already has state at {path}; "
                    "choose a new experiment name"
                )
        profiles[namespace] = profile
    return profiles


def close_and_verify_hindsight_daemon(provider: Any) -> str | None:
    client = getattr(provider, "_client", None)
    if client is None:
        return None
    profile = getattr(provider, "repair_profile", None)
    if not profile:
        raise RuntimeError("cannot verify Hindsight daemon shutdown without a profile")
    manager = client._manager
    client.close(stop_daemon=True)
    if manager.is_running(profile):
        raise RuntimeError(f"Hindsight daemon is still running for profile {profile}")
    return profile


def run_full_pipeline_pilot(
    candidate_path: Path,
    reference_path: Path,
    variance_root: Path,
    experiment: str,
    replicas: int,
) -> Path:
    experiment = _slug(experiment)
    namespaces = replica_namespaces(experiment, replicas)
    experiment_root = variance_root / experiment
    if experiment_root.exists():
        raise FileExistsError(
            f"variance output already exists: {experiment_root}; "
            "choose a new experiment name"
        )

    _load_environment()
    data_path = _validate_expected_runtime_configuration()
    candidate = load_amb_result(candidate_path)
    reference = load_amb_result(reference_path)
    selected = validate_pilot_inputs(
        candidate,
        reference,
        PILOT_ERROR_QUERY_IDS,
    )
    profiles = _validate_new_replica_profiles(namespaces)

    dataset = SelectedLongMemEvalDataset(selected)
    queries = dataset.load_queries("s", limit=500)
    if {query.id for query in queries} != set(selected):
        raise ValueError("frozen LongMemEval dataset is missing a pilot query")
    documents = dataset.load_documents("s", user_ids=set(selected))
    document_ids_by_query = {query_id: set() for query_id in selected}
    for document in documents:
        if document.user_id in document_ids_by_query:
            document_ids_by_query[document.user_id].add(document.id)
    if any(not document_ids_by_query[query_id] for query_id in selected):
        raise ValueError("a pilot query has no LongMemEval documents")
    expected_extraction_calls = expected_retain_batch_count(
        document_ids_by_query,
        RepairHindsightMemoryProvider._ASYNC_BATCH_SIZE,
    )

    candidate_hash = _sha256(candidate_path)
    reference_hash = _sha256(reference_path)
    data_hash = _sha256(data_path)
    print("VARIANCE_PREFLIGHT_OK", flush=True)
    print(f"experiment={experiment} replicas={replicas}", flush=True)
    print(f"candidate_sha256={candidate_hash}", flush=True)
    print(f"reference_sha256={reference_hash}", flush=True)
    print(f"dataset_sha256={data_hash}", flush=True)
    print(f"pilot_query_ids={','.join(selected)}", flush=True)
    print(f"expected_retain_batches={expected_extraction_calls}", flush=True)
    print(
        "runtime_config=" + json.dumps(_redacted_runtime_config(), sort_keys=True),
        flush=True,
    )

    from memory_bench.llm import get_answer_llm
    from memory_bench.modes import get_mode
    from memory_bench.runner import EvalRunner

    replica_artifacts = []
    replica_manifest = []
    for replica_index, namespace in enumerate(namespaces, start=1):
        profile = profiles[namespace]
        run_name = f"variance-{experiment}-r{replica_index}"
        output_dir = experiment_root / f"r{replica_index}"
        provider = RepairHindsightMemoryProvider(namespace)
        omb_trace_path = output_dir / "omb-completions.jsonl"
        hindsight_trace_path = output_dir / "hindsight-completions.jsonl"
        print(
            f"VARIANCE_REPLICA_START replica={replica_index} "
            f"namespace={namespace} profile={profile}",
            flush=True,
        )
        try:
            os.environ["AMB_VARIANCE_HINDSIGHT_TRACE_PATH"] = str(
                hindsight_trace_path.resolve()
            )
            answer_llm = get_answer_llm()
            runner = EvalRunner(output_dir=output_dir)
            disable_opaque_openai_retries(answer_llm)
            disable_opaque_openai_retries(runner._judge._llm)
            with _trace_omb_completions(omb_trace_path):
                summary = runner.run(
                    dataset=dataset,
                    split="s",
                    memory=provider,
                    mode=get_mode("rag", llm=answer_llm),
                    query_limit=500,
                    run_name=run_name,
                    description=(
                        "Five-error full-pipeline nondeterminism pilot; "
                        f"experiment={experiment}; replica={replica_index}; "
                        f"profile={profile}; candidate_sha256={candidate_hash}; "
                        f"reference_sha256={reference_hash}; "
                        f"dataset_sha256={data_hash}."
                    ),
                )
            if summary.total_queries != len(selected):
                raise RuntimeError(
                    f"replica {replica_index} returned {summary.total_queries} results; "
                    f"expected {len(selected)}"
                )
            result_path = _replica_result_path(
                experiment_root,
                experiment,
                replica_index,
            )
            artifact = load_amb_result(result_path)
            if {result["query_id"] for result in artifact["results"]} != set(selected):
                raise RuntimeError(
                    f"replica {replica_index} output query IDs do not match the pilot"
                )

            confirmed_document_hashes = {}
            for query_id in selected:
                actual_ids = provider.document_ids(query_id)
                expected_ids = document_ids_by_query[query_id]
                if actual_ids != expected_ids:
                    raise RuntimeError(
                        f"replica {replica_index} document set mismatch for {query_id}"
                    )
                confirmed_document_hashes[query_id] = _identifier_set_sha256(actual_ids)
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
                    "hindsight_extraction": expected_extraction_calls,
                    "hindsight_verification": 1,
                },
                EXPECTED_RESOLVED_MODEL_BY_ROLE,
            )
            if (
                hindsight_trace_summary["calls_by_role"].get("hindsight_verification")
                != 1
            ):
                raise RuntimeError(
                    f"replica {replica_index} expected exactly one Hindsight "
                    "connection verification call"
                )
            if any(
                event.get("temperature") != 0.0
                or event.get("requested_model")
                not in {"deepseek-v4-pro", "deepseek-v4-flash"}
                for event in omb_events
            ):
                raise RuntimeError(
                    f"replica {replica_index} OMB completion configuration drifted"
                )
            if any(
                event.get("temperature") != 0.0
                or event.get("requested_model") != "deepseek-v4-flash"
                for event in hindsight_events
                if event.get("role") == "hindsight_extraction"
            ) or any(
                event.get("temperature") is not None
                or event.get("requested_model") != "deepseek-v4-flash"
                for event in hindsight_events
                if event.get("role") == "hindsight_verification"
            ):
                raise RuntimeError(
                    f"replica {replica_index} Hindsight completion configuration drifted"
                )
            attestation = {
                "experiment": experiment,
                "replica": replica_index,
                "namespace": namespace,
                "profile": profile,
                "query_ids": list(selected),
                "confirmed_document_ids_sha256": confirmed_document_hashes,
                "result_path": str(result_path.resolve()),
                "result_sha256": _sha256(result_path),
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
            }
            attestation_path = output_dir / "retain-attestation.json"
            write_json_exclusive(attestation_path, attestation)
            replica_artifacts.append(artifact)
            replica_manifest.append(
                {
                    **attestation,
                    "attestation_path": str(attestation_path.resolve()),
                    "correct": artifact["correct"],
                }
            )
            print(
                f"VARIANCE_REPLICA_OK replica={replica_index} "
                f"correct={artifact['correct']}/{len(selected)} "
                f"result_sha256={attestation['result_sha256']}",
                flush=True,
            )
        finally:
            os.environ.pop("AMB_VARIANCE_HINDSIGHT_TRACE_PATH", None)
            stopped_profile = close_and_verify_hindsight_daemon(provider)
            if stopped_profile is not None:
                print(
                    f"VARIANCE_DAEMON_STOPPED profile={stopped_profile}",
                    flush=True,
                )

    result = summarize_replicas(candidate, replica_artifacts, selected)
    result.update(
        {
            "experiment": experiment,
            "pilot_query_ids": list(selected),
            "candidate_path": str(candidate_path.resolve()),
            "candidate_sha256": candidate_hash,
            "reference_path": str(reference_path.resolve()),
            "reference_sha256": reference_hash,
            "dataset_sha256": data_hash,
            "runtime_config": _redacted_runtime_config(),
            "replicas": replica_manifest,
        }
    )
    if _sha256(candidate_path) != candidate_hash:
        raise RuntimeError("candidate artifact changed during the variance pilot")
    if _sha256(reference_path) != reference_hash:
        raise RuntimeError("reference artifact changed during the variance pilot")

    summary_path = experiment_root / "summary.json"
    write_json_exclusive(summary_path, result)
    print(f"VARIANCE_SUMMARY_OK output={summary_path.resolve()}", flush=True)
    return summary_path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--experiment", required=True)
    parser.add_argument("--replicas", type=int, default=DEFAULT_REPLICA_COUNT)
    parser.add_argument("--variance-root", type=Path, default=DEFAULT_VARIANCE_ROOT)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    run_full_pipeline_pilot(
        candidate_path=args.candidate,
        reference_path=args.reference,
        variance_root=args.variance_root,
        experiment=args.experiment,
        replicas=args.replicas,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileExistsError, OSError, RuntimeError, ValueError) as error:
        print(f"Error: {error}", file=os.sys.stderr)
        raise SystemExit(2) from None
