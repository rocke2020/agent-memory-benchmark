#!/usr/bin/env python3
"""Launch Hindsight 0.4.x with two narrowly scoped upstream bug backports."""

from __future__ import annotations

import inspect
import os
import sys
import textwrap
from functools import wraps
from importlib.metadata import version
from pathlib import Path

UNICODE_ENTITY_FIX_COMMIT = "438ce98b4"
SPECIAL_TOKEN_FIX_COMMIT = "4bc7013e4"
EXPECTED_HINDSIGHT_API_VERSION = "0.4.17"


class _SafeEncoding:
    """Make tiktoken count special-token literals as ordinary user text."""

    def __init__(self, encoding):
        self._encoding = encoding

    def encode(self, text: str, **kwargs) -> list[int]:
        kwargs.setdefault("disallowed_special", ())
        return self._encoding.encode(text, **kwargs)

    def decode(self, tokens: list[int]) -> str:
        return self._encoding.decode(tokens)

    def __getattr__(self, name: str):
        return getattr(self._encoding, name)


def _install_safe_tiktoken_patch() -> None:
    import tiktoken

    if getattr(tiktoken.get_encoding, "_amb_repair_patch", False):
        return

    original_get_encoding = tiktoken.get_encoding
    safe_encodings = {}

    def get_safe_encoding(name: str):
        if name not in safe_encodings:
            safe_encodings[name] = _SafeEncoding(original_get_encoding(name))
        return safe_encodings[name]

    get_safe_encoding._amb_repair_patch = True
    tiktoken.get_encoding = get_safe_encoding


_OLD_ENTITY_FALLBACK = '''\
            missing = [n for n, _ in sorted_groups if n not in id_by_name]
            if missing:
                existing_rows = await conn.fetch(
                    f"""
                    SELECT id, LOWER(canonical_name) AS name_lower
                    FROM {fq_table("entities")}
                    WHERE bank_id = $1 AND LOWER(canonical_name) = ANY($2::text[])
                    """,
                    bank_id,
                    missing,
                )
                for row in existing_rows:
                    id_by_name[row["name_lower"]] = row["id"]
'''

_NEW_ENTITY_FALLBACK = '''\
            missing_original = [
                group.name
                for name_lower, group in sorted_groups
                if name_lower not in id_by_name
            ]
            if missing_original:
                existing_rows = await conn.fetch(
                    f"""
                    SELECT e.id, LOWER(e.canonical_name) AS name_lower, inputs.input_name
                    FROM {fq_table("entities")} e
                    JOIN (
                        SELECT LOWER(n) AS input_name_lower, n AS input_name
                        FROM unnest($2::text[]) AS n
                    ) AS inputs ON LOWER(e.canonical_name) = inputs.input_name_lower
                    WHERE e.bank_id = $1
                    """,
                    bank_id,
                    missing_original,
                )
                for row in existing_rows:
                    id_by_name[row["name_lower"]] = row["id"]
                    id_by_name[row["input_name"].lower()] = row["id"]
'''


def _patched_entity_resolver_source(source: str) -> str:
    """Apply upstream commit 438ce98b4 to the 0.4.x resolver source."""
    if source.count(_OLD_ENTITY_FALLBACK) != 1:
        raise RuntimeError(
            "Hindsight entity resolver does not match the expected 0.4.x source; "
            "refusing to apply a guessed repair patch"
        )
    return source.replace(_OLD_ENTITY_FALLBACK, _NEW_ENTITY_FALLBACK)


def _install_unicode_entity_patch() -> None:
    from hindsight_api.engine.entity_resolver import EntityResolver

    original = EntityResolver._resolve_from_candidates
    if getattr(original, "_amb_repair_patch", False):
        return

    patched_source = textwrap.dedent(
        _patched_entity_resolver_source(inspect.getsource(original))
    )
    namespace = dict(original.__globals__)
    exec(  # noqa: S102 - exact source-shape gate precedes the narrow backport
        compile(patched_source, original.__code__.co_filename, "exec"),
        namespace,
    )
    patched = namespace[original.__name__]
    patched._amb_repair_patch = True
    EntityResolver._resolve_from_candidates = patched


def _validate_daemon_patch_state() -> None:
    from hindsight_api.engine.entity_resolver import EntityResolver

    actual_version = version("hindsight-api")
    if actual_version != EXPECTED_HINDSIGHT_API_VERSION:
        raise RuntimeError(
            f"repair daemon requires hindsight-api 0.4.17; got {actual_version}"
        )
    if not getattr(
        EntityResolver._resolve_from_candidates,
        "_amb_repair_patch",
        False,
    ):
        raise RuntimeError("Unicode entity resolver patch is not active")

    import tiktoken

    text = "literal <|endoftext|> text"
    encoding = tiktoken.get_encoding("cl100k_base")
    tokens = encoding.encode(text)
    if encoding.decode(tokens) != text or len(tokens) <= 3:
        raise RuntimeError("special-token encoding patch self-test failed")


def _hindsight_trace_role(call_params: dict[str, object]) -> str:
    if call_params.get("temperature") is None:
        return "hindsight_verification"
    return "hindsight_extraction"


def _install_variance_completion_trace() -> None:
    trace_path_value = os.environ.get("AMB_VARIANCE_HINDSIGHT_TRACE_PATH")
    if not trace_path_value:
        return

    from openai.resources.chat.completions.completions import AsyncCompletions
    from variance_trace import make_async_completion_wrapper

    AsyncCompletions.create = make_async_completion_wrapper(
        AsyncCompletions.create,
        Path(trace_path_value),
        _hindsight_trace_role,
    )
    print(
        f"HINDSIGHT_VARIANCE_TRACE_ACTIVE path={trace_path_value}",
        file=sys.stderr,
        flush=True,
    )


def _with_evaluation_temperature(llm_method, temperature: float):
    @wraps(llm_method)
    async def method_with_evaluation_temperature(self, *args, **kwargs):
        if len(args) > 3:
            args = (*args[:3], temperature, *args[4:])
            kwargs.pop("temperature", None)
        else:
            kwargs["temperature"] = temperature
        return await llm_method(self, *args, **kwargs)

    return method_with_evaluation_temperature


def _configure_evaluation_temperature(
    llm_provider_class: type,
    openai_compatible_class: type,
    temperature: float,
) -> None:
    original_call = llm_provider_class.call
    original_tool_call = llm_provider_class.call_with_tools
    original_reasoning_check = openai_compatible_class._supports_reasoning_model

    @wraps(original_reasoning_check)
    def supports_reasoning_model(self) -> bool:
        if "deepseek" in self.model.lower():
            return False
        return original_reasoning_check(self)

    llm_provider_class.call = _with_evaluation_temperature(original_call, temperature)
    llm_provider_class.call_with_tools = _with_evaluation_temperature(
        original_tool_call,
        temperature,
    )
    openai_compatible_class._supports_reasoning_model = supports_reasoning_model


def main() -> None:
    temperature = float(sys.argv.pop(1))
    _install_safe_tiktoken_patch()
    _install_unicode_entity_patch()
    _validate_daemon_patch_state()
    _install_variance_completion_trace()

    from hindsight_api.engine.llm_wrapper import LLMProvider
    from hindsight_api.engine.providers.openai_compatible_llm import (
        OpenAICompatibleLLM,
    )
    from hindsight_api.main import main as hindsight_main

    _configure_evaluation_temperature(
        LLMProvider,
        OpenAICompatibleLLM,
        temperature,
    )
    print(
        "HINDSIGHT_REPAIR_SELF_TEST_OK "
        f"api_version={EXPECTED_HINDSIGHT_API_VERSION} "
        f"unicode={UNICODE_ENTITY_FIX_COMMIT} special_token={SPECIAL_TOKEN_FIX_COMMIT}",
        file=sys.stderr,
        flush=True,
    )
    hindsight_main()


if __name__ == "__main__":
    main()
