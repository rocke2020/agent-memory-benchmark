"""Stable projections and request metadata for variance experiments."""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any

_TRACE_WRITE_LOCK = threading.Lock()
_COMPLETION_SCOPE: ContextVar[str | None] = ContextVar(
    "variance_completion_scope",
    default=None,
)
_UNSTABLE_RAW_KEYS = frozenset(
    {
        "id",
        "request_id",
        "source_fact_ids",
        "trace",
    }
)


def canonical_json_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


@contextmanager
def completion_scope(scope: str):
    token = _COMPLETION_SCOPE.set(scope)
    try:
        yield
    finally:
        _COMPLETION_SCOPE.reset(token)


def bind_completion_scope(scope: str) -> None:
    """Bind a scope for the lifetime of the current asyncio task."""
    _COMPLETION_SCOPE.set(scope)


def _normalized_chunk_id(chunk_id: Any, document_id: Any) -> Any:
    if not isinstance(chunk_id, str) or not isinstance(document_id, str):
        return chunk_id
    position = chunk_id.find(document_id)
    return chunk_id[position:] if position >= 0 else chunk_id


def _strip_unstable_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _strip_unstable_fields(item)
            for key, item in sorted(value.items())
            if key not in _UNSTABLE_RAW_KEYS
        }
    if isinstance(value, list):
        return [_strip_unstable_fields(item) for item in value]
    return value


def semantic_raw_projection(raw_response: Any) -> Any:
    """Remove generated identities while preserving retrieval order and content."""
    if not isinstance(raw_response, dict):
        return _strip_unstable_fields(raw_response)

    projected_results = []
    for result in raw_response.get("results") or []:
        if not isinstance(result, dict):
            projected_results.append(_strip_unstable_fields(result))
            continue
        projected = {
            key: _strip_unstable_fields(value)
            for key, value in sorted(result.items())
            if key not in _UNSTABLE_RAW_KEYS and key != "chunk_id"
        }
        projected["chunk_id"] = _normalized_chunk_id(
            result.get("chunk_id"),
            result.get("document_id"),
        )
        projected_results.append(projected)

    projected_chunks = []
    chunks = raw_response.get("chunks") or {}
    if isinstance(chunks, dict):
        for chunk in chunks.values():
            if not isinstance(chunk, dict):
                projected_chunks.append(_strip_unstable_fields(chunk))
                continue
            projected_chunks.append(
                {
                    key: _strip_unstable_fields(value)
                    for key, value in sorted(chunk.items())
                    if key not in _UNSTABLE_RAW_KEYS
                }
            )
        projected_chunks.sort(key=canonical_json_sha256)

    return {
        "results": projected_results,
        "chunks": projected_chunks,
        "entities": _strip_unstable_fields(raw_response.get("entities")),
        "source_facts": _strip_unstable_fields(raw_response.get("source_facts")),
    }


def completion_trace_event(
    call_params: dict[str, Any],
    response: Any,
    *,
    role: str,
    scope: str | None = None,
    call_id: str | None = None,
) -> dict[str, Any]:
    choices = getattr(response, "choices", None) or []
    choice = choices[0] if choices else None
    message = getattr(choice, "message", None)
    reasoning_content = getattr(message, "reasoning_content", None)
    content = getattr(message, "content", None)
    usage = getattr(response, "usage", None)
    extra_body = call_params.get("extra_body") or {}
    thinking_requested = call_params.get("thinking")
    if thinking_requested is None and isinstance(extra_body, dict):
        thinking_requested = extra_body.get("thinking")

    event = {
        "event_type": "success",
        "role": role,
        "scope": scope,
        "requested_model": call_params.get("model"),
        "resolved_model": getattr(response, "model", None),
        "request_id": getattr(response, "id", None),
        "temperature": call_params.get("temperature"),
        "thinking_requested": thinking_requested,
        "reasoning_content_present": bool(reasoning_content),
        "messages_sha256": canonical_json_sha256(call_params.get("messages")),
        "response_content_sha256": hashlib.sha256((content or "").encode()).hexdigest(),
        "finish_reason": getattr(choice, "finish_reason", None),
        "usage": {
            "input": getattr(usage, "prompt_tokens", None),
            "output": getattr(usage, "completion_tokens", None),
            "total": getattr(usage, "total_tokens", None),
        },
    }
    if call_id is not None:
        event["call_id"] = call_id
    return event


def completion_attempt_event(
    call_params: dict[str, Any],
    *,
    role: str,
    call_id: str,
    sdk_max_retries: Any,
) -> dict[str, Any]:
    extra_body = call_params.get("extra_body") or {}
    thinking_requested = call_params.get("thinking")
    if thinking_requested is None and isinstance(extra_body, dict):
        thinking_requested = extra_body.get("thinking")
    return {
        "event_type": "attempt",
        "call_id": call_id,
        "role": role,
        "requested_model": call_params.get("model"),
        "temperature": call_params.get("temperature"),
        "thinking_requested": thinking_requested,
        "messages_sha256": canonical_json_sha256(call_params.get("messages")),
        "sdk_max_retries": sdk_max_retries,
        "scope": _COMPLETION_SCOPE.get(),
    }


def completion_failure_event(
    error: Exception,
    *,
    role: str,
    call_id: str,
) -> dict[str, Any]:
    return {
        "event_type": "failure",
        "call_id": call_id,
        "role": role,
        "error_type": type(error).__name__,
        "request_id": getattr(error, "request_id", None),
        "status_code": getattr(error, "status_code", None),
        "scope": _COMPLETION_SCOPE.get(),
    }


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    with _TRACE_WRITE_LOCK, path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def make_sync_completion_wrapper(
    original_create: Callable[..., Any],
    trace_path: Path,
    role_for_params: Callable[[dict[str, Any]], str],
):
    def traced_create(resource, *args, **kwargs):
        role = role_for_params(kwargs)
        call_id = uuid.uuid4().hex
        append_jsonl(
            trace_path,
            completion_attempt_event(
                kwargs,
                role=role,
                call_id=call_id,
                sdk_max_retries=getattr(
                    getattr(resource, "_client", None), "max_retries", None
                ),
            ),
        )
        try:
            response = original_create(resource, *args, **kwargs)
        except Exception as error:
            append_jsonl(
                trace_path,
                completion_failure_event(error, role=role, call_id=call_id),
            )
            raise
        append_jsonl(
            trace_path,
            completion_trace_event(
                kwargs,
                response,
                role=role,
                scope=_COMPLETION_SCOPE.get(),
                call_id=call_id,
            ),
        )
        return response

    return traced_create


def make_async_completion_wrapper(
    original_create: Callable[..., Any],
    trace_path: Path,
    role_for_params: Callable[[dict[str, Any]], str],
):
    async def traced_create(resource, *args, **kwargs):
        role = role_for_params(kwargs)
        call_id = uuid.uuid4().hex
        append_jsonl(
            trace_path,
            completion_attempt_event(
                kwargs,
                role=role,
                call_id=call_id,
                sdk_max_retries=getattr(
                    getattr(resource, "_client", None), "max_retries", None
                ),
            ),
        )
        try:
            response = await original_create(resource, *args, **kwargs)
        except Exception as error:
            append_jsonl(
                trace_path,
                completion_failure_event(error, role=role, call_id=call_id),
            )
            raise
        append_jsonl(
            trace_path,
            completion_trace_event(
                kwargs,
                response,
                role=role,
                scope=_COMPLETION_SCOPE.get(),
                call_id=call_id,
            ),
        )
        return response

    return traced_create
