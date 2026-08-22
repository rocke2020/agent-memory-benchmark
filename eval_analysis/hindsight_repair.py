#!/usr/bin/env python3
"""Safely re-run and merge the three incomplete LongMemEval question units."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import tempfile
import urllib.request
from importlib.metadata import version
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

from dotenv import load_dotenv

from memory_bench.memory.hindsight import HindsightMemoryProvider, _HindsightBase

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = REPOSITORY_ROOT / "outputs/longmemeval/hindsight-deepseek/rag/s.json"
DEFAULT_REPAIR_ROOT = Path(__file__).resolve().parent / "repair-results"
EXPECTED_HINDSIGHT_EMBED_VERSION = "0.4.17"
EXPECTED_LONGMEMEVAL_DATA_SHA256 = (
    "d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442"
)
EXPECTED_DEEPSEEK_ENDPOINT = "https://api.deepseek.com"
EXPECTED_RUNTIME_CONFIG = {
    "answer_provider": "openai",
    "answer_model": "deepseek-v4-pro",
    "judge_provider": "openai",
    "judge_model": "deepseek-v4-flash",
    "extraction_provider": "openai",
    "extraction_model": "deepseek-v4-flash",
    "embedding_provider": "local",
    "embedding_model": "BAAI/bge-small-en-v1.5",
    "reranker_provider": "local",
    "reranker_model": "cross-encoder/ms-marco-MiniLM-L-6-v2",
    "reranker_max_candidates": "300",
    "temperature": 0.0,
}
EXPECTED_SOURCE_QUERY_COUNT = 500
EXPECTED_UNCHANGED_QUERY_COUNT = 497
REPAIR_QUESTION_DOC_COUNTS = {
    "4f54b7c9": 45,
    "gpt4_2312f94c": 45,
    "gpt4_78cf46a3": 49,
}
REPAIR_QUERY_IDS = frozenset(REPAIR_QUESTION_DOC_COUNTS)
UNICODE_ENTITY_FIX_COMMIT = "438ce98b4"
SPECIAL_TOKEN_FIX_COMMIT = "4bc7013e4"
RETAIN_FAILURE_MARKERS = (
    "skipping batch",
    "skipped batch",
)


class RetainCompletenessError(RuntimeError):
    """Raised when Hindsight does not confirm every retain batch."""


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]", "-", value).strip("-")
    if not slug or slug != value:
        raise ValueError(
            "namespace must contain only letters, numbers, underscores, and hyphens"
        )
    return slug


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _identifier_set_sha256(values: set[str]) -> str:
    return hashlib.sha256("\n".join(sorted(values)).encode()).hexdigest()


def _retain_attestation_path(repair_path: Path) -> Path:
    return repair_path.with_name(f"{repair_path.stem}.retain-attestation.json")


def _write_retain_attestation(
    repair_path: Path,
    *,
    query_id: str,
    namespace: str,
    profile: str,
    bank_id: str,
    expected_document_ids: set[str],
    actual_document_ids: set[str],
) -> Path:
    if actual_document_ids != expected_document_ids:
        raise RetainCompletenessError(
            f"{query_id} cannot be attested because its document sets differ"
        )
    attestation_path = _retain_attestation_path(repair_path)
    payload = {
        "query_id": query_id,
        "repair_namespace": namespace,
        "repair_profile": profile,
        "bank_id": bank_id,
        "confirmed_document_count": len(actual_document_ids),
        "expected_document_ids_sha256": _identifier_set_sha256(expected_document_ids),
        "actual_document_ids_sha256": _identifier_set_sha256(actual_document_ids),
        "repair_result_sha256": _sha256(repair_path),
    }
    attestation_path.parent.mkdir(parents=True, exist_ok=True)
    with attestation_path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
    return attestation_path


def _load_retain_attestation(repair_path: Path) -> dict[str, Any]:
    attestation_path = _retain_attestation_path(repair_path)
    try:
        payload = json.loads(attestation_path.read_text())
    except FileNotFoundError as error:
        raise ValueError(
            f"retain attestation is missing for repair file: {repair_path}"
        ) from error
    if not isinstance(payload, dict):
        raise ValueError(f"invalid retain attestation: {attestation_path}")  # noqa: TRY004
    return payload


def _load_result(path: Path) -> dict[str, Any]:
    from amb_report import load_amb_result

    return load_amb_result(path)


class _RetainWarningRecorder(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.failures: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if any(marker in message for marker in RETAIN_FAILURE_MARKERS):
            self.failures.append(message)


def _repair_daemon_manager_class():
    from memory_bench.memory.hindsight import _HindsightDaemonManager

    class RepairDaemonManager(_HindsightDaemonManager):
        def _find_api_command(self) -> list[str]:
            command = super()._find_api_command()
            if command[0] != "uvx":
                raise RuntimeError(
                    "repair requires the isolated uvx Hindsight daemon; "
                    f"got command {command!r}"
                )
            expected_launcher = str(
                Path(__file__).resolve().parent.parent
                / "src/memory_bench/memory/_hindsight_daemon.py"
            )
            if len(command) < 3 or command[-2] != expected_launcher:
                raise RuntimeError(
                    "Hindsight daemon launch command changed; refusing an unverified repair"
                )
            command[-2] = str(
                Path(__file__).resolve().with_name("hindsight_repair_daemon.py")
            )
            return command

    return RepairDaemonManager


def _repair_profile(bank_id: str) -> str:
    from memory_bench.memory.hindsight import (
        _hindsight_llm_config,
        _hindsight_profile,
    )

    return _hindsight_profile(bank_id, _hindsight_llm_config())


def _repair_profile_paths(profile: str) -> tuple[Path, Path]:
    daemon_log = Path.home() / ".hindsight" / "profiles" / f"{profile}.log"
    database = Path.home() / ".pg0" / "instances" / f"hindsight-embed-{profile}"
    return daemon_log, database


def _redacted_runtime_config() -> dict[str, Any]:
    from memory_bench.llm import get_answer_llm, get_judge_llm
    from memory_bench.memory.hindsight import _hindsight_llm_config

    answer = get_answer_llm()
    judge = get_judge_llm()
    extraction = _hindsight_llm_config()
    return {
        "answer": {
            "model_id": answer.model_id,
            "endpoint_host": answer._client.base_url.host,
            "key_fingerprint": hashlib.sha256(
                answer._client.api_key.encode()
            ).hexdigest()[:12],
        },
        "judge": {
            "model_id": judge.model_id,
            "endpoint_host": judge._client.base_url.host,
            "key_fingerprint": hashlib.sha256(
                judge._client.api_key.encode()
            ).hexdigest()[:12],
        },
        "hindsight_extraction": {
            "provider": extraction["llm_provider"],
            "model": extraction["llm_model"],
            "endpoint_host": urlparse(extraction["llm_base_url"]).netloc,
            "key_fingerprint": hashlib.sha256(
                extraction["llm_api_key"].encode()
            ).hexdigest()[:12],
        },
        "hindsight": {
            "embed_version": os.environ.get("HINDSIGHT_EMBED_API_VERSION"),
            "installed_embed_version": version("hindsight-embed"),
            "embedding_provider": os.environ.get("HINDSIGHT_API_EMBEDDINGS_PROVIDER"),
            "embedding_model": os.environ.get("HINDSIGHT_API_EMBEDDINGS_LOCAL_MODEL"),
            "reranker_provider": os.environ.get("HINDSIGHT_API_RERANKER_PROVIDER"),
            "reranker_model": os.environ.get("HINDSIGHT_API_RERANKER_LOCAL_MODEL"),
            "reranker_max_candidates": os.environ.get(
                "HINDSIGHT_API_RERANKER_MAX_CANDIDATES"
            ),
        },
    }


def _load_environment() -> None:
    load_dotenv(REPOSITORY_ROOT / ".env", override=True)


def _expected_document_ids_by_query() -> dict[str, set[str]]:
    from memory_bench.dataset import get_dataset

    dataset = get_dataset("longmemeval")
    documents = dataset.load_documents("s", user_ids=set(REPAIR_QUERY_IDS))
    document_ids_by_query = {query_id: set() for query_id in REPAIR_QUERY_IDS}
    for document in documents:
        if document.user_id in document_ids_by_query:
            document_ids_by_query[document.user_id].add(document.id)
    for query_id, expected_count in REPAIR_QUESTION_DOC_COUNTS.items():
        actual_count = len(document_ids_by_query[query_id])
        if actual_count != expected_count:
            raise ValueError(
                f"{query_id} expected {expected_count} unique dataset documents, "
                f"got {actual_count}"
            )
    return document_ids_by_query


def _validate_expected_runtime_configuration() -> Path:
    from memory_bench.llm import get_answer_llm, get_judge_llm
    from memory_bench.llm.base import EVALUATION_TEMPERATURE
    from memory_bench.memory.hindsight import _hindsight_llm_config

    if os.environ.get("HINDSIGHT_EMBED_API_DATABASE_URL"):
        raise ValueError(
            "HINDSIGHT_EMBED_API_DATABASE_URL must be unset so the repair profile "
            "cannot point at an existing database"
        )

    answer = get_answer_llm()
    judge = get_judge_llm()
    extraction = _hindsight_llm_config()
    checks = {
        "answer_provider": os.environ.get("OMB_ANSWER_LLM"),
        "answer_model": os.environ.get("OMB_ANSWER_MODEL"),
        "judge_provider": os.environ.get("OMB_JUDGE_LLM"),
        "judge_model": os.environ.get("OMB_JUDGE_MODEL"),
        "extraction_provider": extraction["llm_provider"],
        "extraction_model": extraction["llm_model"],
        "embedding_provider": os.environ.get("HINDSIGHT_API_EMBEDDINGS_PROVIDER"),
        "embedding_model": os.environ.get("HINDSIGHT_API_EMBEDDINGS_LOCAL_MODEL"),
        "reranker_provider": os.environ.get("HINDSIGHT_API_RERANKER_PROVIDER"),
        "reranker_model": os.environ.get("HINDSIGHT_API_RERANKER_LOCAL_MODEL"),
        "reranker_max_candidates": os.environ.get(
            "HINDSIGHT_API_RERANKER_MAX_CANDIDATES"
        ),
        "temperature": EVALUATION_TEMPERATURE,
    }
    if checks != EXPECTED_RUNTIME_CONFIG:
        raise ValueError(
            "runtime configuration does not match the frozen repair configuration: "
            f"{checks!r}"
        )
    if answer.model_id != "openai:deepseek-v4-pro":
        raise ValueError(f"unexpected answer constructor: {answer.model_id}")
    if judge.model_id != "openai:deepseek-v4-flash":
        raise ValueError(f"unexpected judge constructor: {judge.model_id}")

    answer_endpoint = str(answer._client.base_url).rstrip("/")
    judge_endpoint = str(judge._client.base_url).rstrip("/")
    extraction_endpoint = extraction.get("llm_base_url", "").rstrip("/")
    if {
        answer_endpoint,
        judge_endpoint,
        extraction_endpoint,
    } != {EXPECTED_DEEPSEEK_ENDPOINT}:
        raise ValueError(
            "answer, judge, and extraction must all resolve to the official "
            f"DeepSeek endpoint; got {answer_endpoint!r}, {judge_endpoint!r}, "
            f"{extraction_endpoint!r}"
        )
    if not answer._client.api_key or not judge._client.api_key:
        raise ValueError("answer and judge API keys must be non-empty")
    if not extraction.get("llm_api_key"):
        raise ValueError("Hindsight extraction API key must be non-empty")
    if not (
        answer._client.api_key == judge._client.api_key == extraction["llm_api_key"]
    ):
        raise ValueError(
            "answer, judge, and extraction must use the same DeepSeek credential"
        )

    configured_data_path = os.environ.get("LONGMEMEVAL_DATA_PATH")
    if not configured_data_path:
        raise ValueError("LONGMEMEVAL_DATA_PATH must be set for the repair")
    data_path = Path(configured_data_path).resolve()
    if not data_path.is_file():
        raise ValueError(f"LongMemEval data file does not exist: {data_path}")
    data_hash = _sha256(data_path)
    if data_hash != EXPECTED_LONGMEMEVAL_DATA_SHA256:
        raise ValueError(
            "LongMemEval data SHA-256 changed: "
            f"expected {EXPECTED_LONGMEMEVAL_DATA_SHA256}, got {data_hash}"
        )
    return data_path


class RepairHindsightMemoryProvider(HindsightMemoryProvider):
    """Hindsight provider with isolated, create-only, fail-closed retain semantics."""

    def __init__(self, namespace: str) -> None:
        super().__init__()
        self.repair_namespace = _slug(namespace)
        self.repair_profile: str | None = None

    def prepare(
        self,
        store_dir: Path,
        unit_ids: set[str] | None = None,
        reset: bool = True,
    ) -> None:
        from hindsight import HindsightEmbedded

        from memory_bench.memory.hindsight import (
            _hindsight_llm_config,
            _hindsight_profile,
        )

        _HindsightBase.prepare(self, store_dir, unit_ids)
        self._bank_id = f"{self._bank_id}-repair-{self.repair_namespace}"
        llm_config = _hindsight_llm_config()
        self.repair_profile = _hindsight_profile(self._bank_id, llm_config)
        self._client = HindsightEmbedded(
            profile=self.repair_profile,
            **llm_config,
        )
        self._client._manager = _repair_daemon_manager_class()()
        _ = self._client.url

    async def _acreate_bank(self, client, bank_id: str) -> None:
        await client.acreate_bank(
            bank_id=bank_id,
            name=f"Benchmark Repair Bank ({bank_id})",
            **self._bank_kwargs(),
        )

    async def async_ingest(self, documents) -> None:
        recorder = _RetainWarningRecorder()
        logger = logging.getLogger("memory_bench.memory.hindsight")
        logger.addHandler(recorder)
        try:
            await super().async_ingest(documents)
        finally:
            logger.removeHandler(recorder)
        if recorder.failures:
            raise RetainCompletenessError(
                "Hindsight did not confirm every retain batch: "
                + " | ".join(recorder.failures)
            )

    def document_ids(self, user_id: str) -> set[str]:
        bank_id = self._bank_id_for(user_id)
        url = (
            f"{self._client.url}/v1/default/banks/{quote(bank_id, safe='')}/documents"
            "?limit=1000&offset=0"
        )
        with urllib.request.urlopen(url, timeout=30) as response:
            payload = json.load(response)
        items = payload.get("items")
        total = payload.get("total")
        if not isinstance(items, list) or not isinstance(total, int):
            raise RuntimeError(  # noqa: TRY004
                "Hindsight list_documents returned an invalid response"
            )
        document_ids = {item.get("id") for item in items if isinstance(item, dict)}
        if None in document_ids or len(document_ids) != total or len(items) != total:
            raise RuntimeError(
                "Hindsight list_documents returned duplicate or missing document IDs"
            )
        return document_ids


def _validate_preflight(source_path: Path, namespace: str, output_root: Path) -> str:
    _load_environment()
    _validate_expected_runtime_configuration()
    if (
        os.environ.get("HINDSIGHT_EMBED_API_VERSION")
        != EXPECTED_HINDSIGHT_EMBED_VERSION
    ):
        raise ValueError(
            "HINDSIGHT_EMBED_API_VERSION must remain 0.4.17 for this repair"
        )
    if version("hindsight-embed") != EXPECTED_HINDSIGHT_EMBED_VERSION:
        raise ValueError("installed hindsight-embed version must remain 0.4.17")

    source = _load_result(source_path)
    if source.get("dataset") != "longmemeval" or source.get("split") != "s":
        raise ValueError("repair source must be the LongMemEval s split")
    if source["total_queries"] != EXPECTED_SOURCE_QUERY_COUNT:
        raise ValueError(
            f"repair source must contain {EXPECTED_SOURCE_QUERY_COUNT} queries"
        )
    source_ids = {result["query_id"] for result in source["results"]}
    if len(source["results"]) != EXPECTED_SOURCE_QUERY_COUNT:
        raise ValueError("repair source results list must contain exactly 500 entries")
    if len(source_ids) != EXPECTED_SOURCE_QUERY_COUNT:
        raise ValueError("repair source must contain exactly 500 unique query IDs")
    if not REPAIR_QUERY_IDS <= source_ids:
        raise ValueError("repair source does not contain all three target query IDs")

    bank_id = f"longmemeval-s-repair-{_slug(namespace)}"
    profile = _repair_profile(bank_id)
    for path in _repair_profile_paths(profile):
        if path.exists():
            raise FileExistsError(
                f"repair profile already has state at {path}; choose a new namespace"
            )
    if output_root.exists():
        raise FileExistsError(
            f"repair output root already exists: {output_root}; choose a new namespace"
        )
    return profile


def _repair_result_path(output_root: Path, namespace: str, query_id: str) -> Path:
    run_name = f"repair-{namespace}-{query_id}"
    return output_root / "longmemeval" / run_name / "rag" / "s.json"


def run_repairs(source_path: Path, repair_root: Path, namespace: str) -> Path:
    namespace = _slug(namespace)
    output_root = repair_root / namespace
    profile = _validate_preflight(source_path, namespace, output_root)

    print("REPAIR_PREFLIGHT_OK", flush=True)
    print(f"source={source_path.resolve()}", flush=True)
    print(f"source_sha256={_sha256(source_path)}", flush=True)
    print(f"repair_namespace={namespace}", flush=True)
    print(f"repair_profile={profile}", flush=True)
    print(
        f"upstream_backports={UNICODE_ENTITY_FIX_COMMIT},{SPECIAL_TOKEN_FIX_COMMIT}",
        flush=True,
    )
    print(
        "runtime_config=" + json.dumps(_redacted_runtime_config(), sort_keys=True),
        flush=True,
    )
    print(
        f"longmemeval_data_sha256={EXPECTED_LONGMEMEVAL_DATA_SHA256}",
        flush=True,
    )

    from memory_bench.dataset import get_dataset
    from memory_bench.llm import get_answer_llm
    from memory_bench.modes import get_mode
    from memory_bench.runner import EvalRunner

    dataset = get_dataset("longmemeval")
    queries = {query.id: query for query in dataset.load_queries("s", limit=500)}
    repair_paths = []
    expected_document_ids_by_query = {}
    for query_id, expected_docs in REPAIR_QUESTION_DOC_COUNTS.items():
        query = queries.get(query_id)
        if query is None or query.user_id is None:
            raise ValueError(f"dataset does not contain target query {query_id}")
        documents = dataset.load_documents("s", user_ids={query.user_id})
        if len(documents) != expected_docs:
            raise ValueError(
                f"{query_id} expected {expected_docs} documents, got {len(documents)}"
            )

        print(
            f"REPAIR_QUERY_START query_id={query_id} expected_docs={expected_docs}",
            flush=True,
        )
        provider = RepairHindsightMemoryProvider(namespace)
        run_name = f"repair-{namespace}-{query_id}"
        summary = EvalRunner(output_dir=output_root).run(
            dataset=dataset,
            split="s",
            memory=provider,
            mode=get_mode("rag", llm=get_answer_llm()),
            query_limit=500,
            query_id=query_id,
            run_name=run_name,
            description=(
                "Strict repair of one retain-incomplete question; "
                f"repair_namespace={namespace}; repair_profile={profile}; "
                f"backports={UNICODE_ENTITY_FIX_COMMIT},{SPECIAL_TOKEN_FIX_COMMIT}; "
                f"dataset_sha256={EXPECTED_LONGMEMEVAL_DATA_SHA256}."
            ),
        )
        if summary.total_queries != 1:
            raise RuntimeError(
                f"{query_id} produced {summary.total_queries} results instead of one"
            )
        if summary.ingested_docs != expected_docs:
            raise RuntimeError(
                f"{query_id} reported {summary.ingested_docs} ingested docs; "
                f"expected {expected_docs}"
            )
        expected_document_ids = {document.id for document in documents}
        expected_document_ids_by_query[query_id] = expected_document_ids
        actual_document_ids = provider.document_ids(query.user_id)
        if actual_document_ids != expected_document_ids:
            missing = sorted(expected_document_ids - actual_document_ids)
            unexpected = sorted(actual_document_ids - expected_document_ids)
            raise RetainCompletenessError(
                f"{query_id} Hindsight document set mismatch: "
                f"missing={missing} unexpected={unexpected}"
            )
        repair_path = _repair_result_path(output_root, namespace, query_id)
        repair = _load_result(repair_path)
        if repair["results"][0]["query_id"] != query_id:
            raise RuntimeError(f"repair output query ID mismatch for {query_id}")
        if not repair["results"][0].get("answer"):
            raise RuntimeError(f"repair output has an empty answer for {query_id}")
        if not repair["results"][0].get("context"):
            raise RuntimeError(f"repair output has an empty context for {query_id}")
        bank_id = provider._bank_id_for(query.user_id)
        attestation_path = _write_retain_attestation(
            repair_path,
            query_id=query_id,
            namespace=namespace,
            profile=profile,
            bank_id=bank_id,
            expected_document_ids=expected_document_ids,
            actual_document_ids=actual_document_ids,
        )
        repair_paths.append(repair_path)
        print(
            f"REPAIR_QUERY_OK query_id={query_id} ingested_docs={expected_docs} "
            f"confirmed_documents={len(actual_document_ids)} "
            f"document_ids_sha256={_identifier_set_sha256(actual_document_ids)} "
            f"attestation={attestation_path} correct={summary.results[0].correct}",
            flush=True,
        )

    merged_path = output_root / "merged" / "s.json"
    merge_repair_results(
        source_path,
        repair_paths,
        merged_path,
        REPAIR_QUERY_IDS,
        expected_profile=profile,
        expected_document_ids_by_query=expected_document_ids_by_query,
    )
    print(f"REPAIR_MERGE_OK output={merged_path.resolve()}", flush=True)
    print(f"output_sha256={_sha256(merged_path)}", flush=True)
    print(f"source_sha256_after={_sha256(source_path)}", flush=True)
    return merged_path


def merge_repair_results(
    source_path: Path,
    repair_paths: list[Path],
    output_path: Path,
    expected_query_ids: set[str] | frozenset[str],
    *,
    expected_profile: str | None = None,
    expected_document_ids_by_query: dict[str, set[str]] | None = None,
) -> None:
    if output_path.exists():
        raise FileExistsError(f"refusing to overwrite existing output: {output_path}")

    source_hash = _sha256(source_path)
    source = _load_result(source_path)
    if source["total_queries"] != EXPECTED_SOURCE_QUERY_COUNT:
        raise ValueError(
            f"source must contain exactly {EXPECTED_SOURCE_QUERY_COUNT} queries"
        )
    source_results = source["results"]
    source_result_ids = [result["query_id"] for result in source_results]
    if len(source_results) != EXPECTED_SOURCE_QUERY_COUNT:
        raise ValueError("source results list must contain exactly 500 entries")
    if len(set(source_result_ids)) != EXPECTED_SOURCE_QUERY_COUNT:
        raise ValueError("source results must contain exactly 500 unique query IDs")
    if source.get("dataset") != "longmemeval" or source.get("split") != "s":
        raise ValueError("source must be the LongMemEval s split")
    if source.get("memory_provider") != "hindsight" or source.get("mode") != "rag":
        raise ValueError("source must be a Hindsight RAG result")
    if source.get("answer_llm") != "openai:deepseek-v4-pro":
        raise ValueError("source answer model does not match the repair")
    if source.get("judge_llm") != "openai:deepseek-v4-flash":
        raise ValueError("source judge model does not match the repair")
    if set(expected_query_ids) != set(REPAIR_QUERY_IDS):
        raise ValueError(
            "merge authorization must be exactly the three repair query IDs"
        )
    if expected_document_ids_by_query is None:
        _load_environment()
        _validate_expected_runtime_configuration()
        expected_document_ids_by_query = _expected_document_ids_by_query()
    if set(expected_document_ids_by_query) != set(REPAIR_QUERY_IDS):
        raise ValueError("expected document sets must cover exactly the three repairs")

    repair_by_id = {}
    repair_hashes = {}
    repair_namespaces = set()
    repair_profiles = set()
    for repair_path in repair_paths:
        repair = _load_result(repair_path)
        if len(repair["results"]) != 1:
            raise ValueError(f"repair file must contain one result: {repair_path}")
        result = repair["results"][0]
        query_id = result["query_id"]
        expected_docs = REPAIR_QUESTION_DOC_COUNTS.get(query_id)
        if expected_docs is None:
            raise ValueError(f"unauthorized repair query ID: {query_id}")
        expected_metadata = {
            "dataset": "longmemeval",
            "split": "s",
            "memory_provider": "hindsight",
            "mode": "rag",
            "answer_llm": "openai:deepseek-v4-pro",
            "judge_llm": "openai:deepseek-v4-flash",
            "ingested_docs": expected_docs,
        }
        mismatches = {
            field: (repair.get(field), expected)
            for field, expected in expected_metadata.items()
            if repair.get(field) != expected
        }
        if mismatches:
            raise ValueError(f"repair metadata mismatch for {query_id}: {mismatches!r}")
        if not result.get("answer") or not result.get("context"):
            raise ValueError(f"repair answer/context is empty for {query_id}")
        run_name = repair.get("run_name", "")
        run_match = re.fullmatch(
            rf"repair-(?P<namespace>[a-zA-Z0-9_-]+)-{re.escape(query_id)}",
            run_name,
        )
        if run_match is None:
            raise ValueError(f"invalid repair run_name for {query_id}: {run_name!r}")
        namespace = run_match.group("namespace")
        description = repair.get("description") or ""
        profile_match = re.search(r"repair_profile=([^;]+)", description)
        required_attestations = (
            f"repair_namespace={namespace}",
            f"backports={UNICODE_ENTITY_FIX_COMMIT},{SPECIAL_TOKEN_FIX_COMMIT}",
            f"dataset_sha256={EXPECTED_LONGMEMEVAL_DATA_SHA256}",
        )
        if profile_match is None or any(
            attestation not in description for attestation in required_attestations
        ):
            raise ValueError(f"repair attestation is incomplete for {query_id}")
        profile = profile_match.group(1)
        retain_attestation = _load_retain_attestation(repair_path)
        expected_bank_id = f"longmemeval-s-repair-{namespace}-u{query_id}"
        expected_attestation = {
            "query_id": query_id,
            "repair_namespace": namespace,
            "repair_profile": profile,
            "bank_id": expected_bank_id,
            "confirmed_document_count": expected_docs,
            "repair_result_sha256": _sha256(repair_path),
        }
        attestation_mismatches = {
            field: (retain_attestation.get(field), expected)
            for field, expected in expected_attestation.items()
            if retain_attestation.get(field) != expected
        }
        expected_ids_hash = retain_attestation.get("expected_document_ids_sha256")
        actual_ids_hash = retain_attestation.get("actual_document_ids_sha256")
        frozen_dataset_ids_hash = _identifier_set_sha256(
            expected_document_ids_by_query[query_id]
        )
        if (
            attestation_mismatches
            or not isinstance(expected_ids_hash, str)
            or not re.fullmatch(r"[0-9a-f]{64}", expected_ids_hash)
            or expected_ids_hash != frozen_dataset_ids_hash
            or actual_ids_hash != expected_ids_hash
        ):
            raise ValueError(
                f"retain attestation mismatch for {query_id}: "
                f"{attestation_mismatches!r}"
            )
        repair_namespaces.add(namespace)
        repair_profiles.add(profile)
        if query_id in repair_by_id:
            raise ValueError(f"duplicate repair query ID: {query_id}")
        repair_by_id[query_id] = result
        repair_hashes[query_id] = _sha256(repair_path)

    actual_query_ids = set(repair_by_id)
    if actual_query_ids != set(expected_query_ids):
        raise ValueError(
            "repair IDs do not match authorization: "
            f"expected={sorted(expected_query_ids)} actual={sorted(actual_query_ids)}"
        )
    if len(repair_namespaces) != 1 or len(repair_profiles) != 1:
        raise ValueError("all three repairs must share one namespace and profile")
    namespace = next(iter(repair_namespaces))
    attested_profile = next(iter(repair_profiles))
    if expected_profile is None:
        _load_environment()
        _validate_expected_runtime_configuration()
        expected_profile = _repair_profile(f"longmemeval-s-repair-{namespace}")
    if attested_profile != expected_profile:
        raise ValueError(
            "repair profile does not match the isolated profile derived from its namespace"
        )

    source_by_id = {result["query_id"]: result for result in source["results"]}
    missing_ids = actual_query_ids - set(source_by_id)
    if missing_ids:
        raise ValueError(f"repair IDs missing from source: {sorted(missing_ids)}")

    merged_results = [
        repair_by_id.get(result["query_id"], result) for result in source["results"]
    ]
    merged_result_ids = [result["query_id"] for result in merged_results]
    if len(merged_results) != EXPECTED_SOURCE_QUERY_COUNT:
        raise AssertionError("merged result list does not contain exactly 500 entries")
    if len(set(merged_result_ids)) != EXPECTED_SOURCE_QUERY_COUNT:
        raise AssertionError("merged result list does not contain 500 unique query IDs")
    merged = dict(source)
    merged["run_name"] = f"{source.get('run_name')}-repaired-resume-4"
    merged["description"] = (
        "Composite result: original 500-result artifact with only the three "
        "retain-incomplete question units re-run end to end."
    )
    merged["results"] = merged_results
    merged["total_queries"] = len(merged_results)
    merged["correct"] = sum(result.get("correct") is True for result in merged_results)
    merged["accuracy"] = merged["correct"] / merged["total_queries"]
    retrieve_times = [
        result["retrieve_time_ms"]
        for result in merged_results
        if result.get("retrieve_time_ms") is not None
    ]
    context_tokens = [
        result["context_tokens"]
        for result in merged_results
        if result.get("context_tokens") is not None
    ]
    merged["avg_retrieve_time_ms"] = (
        round(sum(retrieve_times) / len(retrieve_times), 1) if retrieve_times else None
    )
    merged["avg_context_tokens"] = (
        round(sum(context_tokens) / len(context_tokens), 1) if context_tokens else None
    )
    merged["repair_manifest"] = {
        "source_sha256": source_hash,
        "replaced_query_ids": sorted(actual_query_ids),
        "repair_file_sha256": repair_hashes,
        "upstream_backports": [
            UNICODE_ENTITY_FIX_COMMIT,
            SPECIAL_TOKEN_FIX_COMMIT,
        ],
    }

    unchanged_ids = set(source_by_id) - actual_query_ids
    if len(unchanged_ids) != EXPECTED_UNCHANGED_QUERY_COUNT:
        raise ValueError(
            f"merge must preserve exactly {EXPECTED_UNCHANGED_QUERY_COUNT} query results"
        )
    merged_by_id = {result["query_id"]: result for result in merged_results}
    if any(
        merged_by_id[query_id] != source_by_id[query_id] for query_id in unchanged_ids
    ):
        raise AssertionError("a non-authorized result changed during merge")
    if _sha256(source_path) != source_hash:
        raise RuntimeError("source result changed during repair merge")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump(merged, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary_path, output_path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run and merge the three repairs")
    run_parser.add_argument("--namespace", required=True)
    run_parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    run_parser.add_argument("--repair-root", type=Path, default=DEFAULT_REPAIR_ROOT)

    merge_parser = subparsers.add_parser(
        "merge",
        help="Merge three already-completed repair files into a new result",
    )
    merge_parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    merge_parser.add_argument("--repair", type=Path, action="append", required=True)
    merge_parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    if args.command == "run":
        run_repairs(args.source, args.repair_root, args.namespace)
        return 0
    if args.command == "merge":
        merge_repair_results(args.source, args.repair, args.output, REPAIR_QUERY_IDS)
        return 0
    raise AssertionError(f"Unhandled command: {args.command}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        FileExistsError,
        OSError,
        RetainCompletenessError,
        RuntimeError,
        ValueError,
    ) as error:
        print(f"Error: {error}", file=os.sys.stderr)
        raise SystemExit(2) from None
