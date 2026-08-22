import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

EVAL_ANALYSIS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EVAL_ANALYSIS_DIR))


PILOT_IDS = (
    "0ddfec37_abs",
    "0a995998",
    "15745da0",
    "b0479f84",
    "gpt4_f420262d",
)


def _artifact(correct_by_id):
    return {
        "results": [
            {
                "query_id": query_id,
                "query": f"Question {query_id}",
                "gold_answers": [f"Gold {query_id}"],
                "meta": {"question_type": "demo"},
                "correct": correct_by_id[query_id],
                "answer": f"Answer {query_id}",
                "context": f"Context {query_id}",
                "context_tokens": 10,
                "raw_response": {"query_id": query_id},
            }
            for query_id in correct_by_id
        ]
    }


class PilotInputTests(unittest.TestCase):
    def test_validation_requires_local_errors_that_official_passed(self):
        from variance_probe import validate_pilot_inputs

        candidate = _artifact({query_id: False for query_id in PILOT_IDS})
        reference = _artifact({query_id: True for query_id in PILOT_IDS})

        selected = validate_pilot_inputs(candidate, reference, PILOT_IDS)

        self.assertEqual(selected, PILOT_IDS)

    def test_validation_rejects_a_candidate_question_that_already_passed(self):
        from variance_probe import validate_pilot_inputs

        candidate_outcomes = {query_id: False for query_id in PILOT_IDS}
        candidate_outcomes[PILOT_IDS[2]] = True
        candidate = _artifact(candidate_outcomes)
        reference = _artifact({query_id: True for query_id in PILOT_IDS})

        with self.assertRaisesRegex(ValueError, "candidate errors"):
            validate_pilot_inputs(candidate, reference, PILOT_IDS)

    def test_validation_rejects_input_drift_between_candidate_and_reference(self):
        from variance_probe import validate_pilot_inputs

        candidate = _artifact({query_id: False for query_id in PILOT_IDS})
        reference = _artifact({query_id: True for query_id in PILOT_IDS})
        reference["results"][0]["query"] = "Different question"

        with self.assertRaisesRegex(ValueError, "input mismatch"):
            validate_pilot_inputs(candidate, reference, PILOT_IDS)


class SelectedDatasetTests(unittest.TestCase):
    def test_selected_dataset_never_leaks_an_unselected_query(self):
        from variance_probe import SelectedLongMemEvalDataset

        all_queries = [
            SimpleNamespace(id=query_id) for query_id in (*PILOT_IDS, "other")
        ]
        dataset = SelectedLongMemEvalDataset(PILOT_IDS)

        with mock.patch(
            "memory_bench.dataset.longmemeval.LongMemEvalDataset.load_queries",
            return_value=all_queries,
        ):
            selected = dataset.load_queries("s", limit=500)

        self.assertEqual([query.id for query in selected], list(PILOT_IDS))


class ReplicaPlanTests(unittest.TestCase):
    def test_three_replicas_receive_distinct_create_only_namespaces(self):
        from variance_probe import replica_namespaces

        self.assertEqual(
            replica_namespaces("pilot5-20260822", 3),
            (
                "variance-pilot5-20260822-r1",
                "variance-pilot5-20260822-r2",
                "variance-pilot5-20260822-r3",
            ),
        )

    def test_one_replica_is_rejected_as_not_a_nondeterminism_probe(self):
        from variance_probe import replica_namespaces

        with self.assertRaisesRegex(ValueError, "at least two"):
            replica_namespaces("pilot5", 1)

    def test_expected_extraction_calls_cover_every_retain_batch(self):
        from variance_probe import expected_retain_batch_count

        document_ids_by_query = {
            "question-a": {f"a-{index}" for index in range(8)},
            "question-b": {f"b-{index}" for index in range(9)},
        }

        self.assertEqual(
            expected_retain_batch_count(document_ids_by_query, batch_size=8),
            3,
        )


class VarianceSummaryTests(unittest.TestCase):
    def test_summary_detects_mixed_verdicts_and_recoveries(self):
        from variance_probe import summarize_replicas

        candidate = _artifact({query_id: False for query_id in PILOT_IDS})
        replica_outcomes = (
            {PILOT_IDS[0]: False, PILOT_IDS[1]: True},
            {PILOT_IDS[0]: True, PILOT_IDS[1]: True},
            {PILOT_IDS[0]: False, PILOT_IDS[1]: True},
        )
        replicas = []
        for replica_index, outcomes in enumerate(replica_outcomes, start=1):
            artifact = _artifact(
                {query_id: outcomes.get(query_id, False) for query_id in PILOT_IDS}
            )
            for result in artifact["results"]:
                result["answer"] += f" replica-{replica_index}"
                result["context"] += f" replica-{replica_index}"
                chunk_id = f"profile-{replica_index}_{result['query_id']}_document_0"
                result["raw_response"] = {
                    "results": [
                        {
                            "id": f"uuid-{replica_index}",
                            "text": f"fact replica-{replica_index}",
                            "document_id": f"{result['query_id']}_document",
                            "chunk_id": chunk_id,
                        }
                    ],
                    "chunks": {
                        chunk_id: {
                            "id": chunk_id,
                            "text": f"source replica-{replica_index}",
                            "chunk_index": 0,
                        }
                    },
                }
            replicas.append(artifact)

        summary = summarize_replicas(candidate, replicas, PILOT_IDS)
        by_id = {row["query_id"]: row for row in summary["questions"]}

        self.assertEqual(summary["replica_correct"], [1, 2, 1])
        self.assertEqual(by_id[PILOT_IDS[0]]["pass_count"], 1)
        self.assertTrue(by_id[PILOT_IDS[0]]["mixed_replica_verdicts"])
        self.assertEqual(by_id[PILOT_IDS[1]]["pass_count"], 3)
        self.assertFalse(by_id[PILOT_IDS[1]]["mixed_replica_verdicts"])
        self.assertEqual(by_id[PILOT_IDS[0]]["unique_answer_hashes"], 3)
        self.assertEqual(by_id[PILOT_IDS[0]]["unique_normalized_answer_hashes"], 3)
        self.assertEqual(by_id[PILOT_IDS[0]]["unique_context_hashes"], 3)
        self.assertEqual(by_id[PILOT_IDS[0]]["unique_raw_response_hashes"], 3)
        self.assertEqual(by_id[PILOT_IDS[0]]["unique_semantic_raw_hashes"], 3)
        self.assertEqual(len(by_id[PILOT_IDS[0]]["answer_user_prompt_sha256"]), 3)
        self.assertEqual(len(by_id[PILOT_IDS[0]]["judge_user_prompt_sha256"]), 3)

    def test_summary_rejects_a_replica_missing_one_selected_question(self):
        from variance_probe import summarize_replicas

        candidate = _artifact({query_id: False for query_id in PILOT_IDS})
        incomplete = _artifact({query_id: False for query_id in PILOT_IDS[:-1]})

        with self.assertRaisesRegex(ValueError, "query IDs"):
            summarize_replicas(candidate, [incomplete, incomplete], PILOT_IDS)


class SemanticTraceTests(unittest.TestCase):
    def test_hindsight_trace_separates_connection_verification_from_extraction(self):
        from hindsight_repair_daemon import _hindsight_trace_role

        self.assertEqual(
            _hindsight_trace_role({"model": "deepseek-v4-flash", "temperature": 0.0}),
            "hindsight_extraction",
        )
        self.assertEqual(
            _hindsight_trace_role({"model": "deepseek-v4-flash"}),
            "hindsight_verification",
        )

    def test_semantic_raw_hash_ignores_generated_ids_and_profile_prefixes(self):
        from variance_trace import canonical_json_sha256, semantic_raw_projection

        def raw(memory_id, bank_prefix, trace_duration):
            chunk_id = f"{bank_prefix}_question_document_7"
            return {
                "results": [
                    {
                        "id": memory_id,
                        "text": "Stable fact",
                        "type": "world",
                        "document_id": "question_document",
                        "chunk_id": chunk_id,
                        "source_fact_ids": [memory_id],
                    }
                ],
                "chunks": {
                    chunk_id: {
                        "id": chunk_id,
                        "text": "Stable source",
                        "chunk_index": 7,
                        "truncated": False,
                    }
                },
                "trace": {"duration_ms": trace_duration},
                "entities": None,
                "source_facts": None,
            }

        first = raw("11111111-1111-1111-1111-111111111111", "profile-a", 10)
        second = raw("22222222-2222-2222-2222-222222222222", "profile-b", 99)

        self.assertNotEqual(canonical_json_sha256(first), canonical_json_sha256(second))
        self.assertEqual(
            semantic_raw_projection(first),
            semantic_raw_projection(second),
        )
        self.assertEqual(
            canonical_json_sha256(semantic_raw_projection(first)),
            canonical_json_sha256(semantic_raw_projection(second)),
        )

    def test_completion_event_records_resolved_model_thinking_and_prompt_hash(self):
        from variance_trace import canonical_json_sha256, completion_trace_event

        response = SimpleNamespace(
            id="request-123",
            model="deepseek-v4-pro-202608",
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(
                        content='{"answer":"ok"}',
                        reasoning_content="reasoning",
                    ),
                )
            ],
            usage=SimpleNamespace(
                prompt_tokens=100,
                completion_tokens=20,
                total_tokens=120,
            ),
        )
        messages = [{"role": "user", "content": "pilot prompt"}]

        event = completion_trace_event(
            {
                "model": "deepseek-v4-pro",
                "messages": messages,
                "temperature": 0.0,
                "extra_body": {"thinking": {"type": "disabled"}},
            },
            response,
            role="answer",
        )

        self.assertEqual(event["requested_model"], "deepseek-v4-pro")
        self.assertEqual(event["resolved_model"], "deepseek-v4-pro-202608")
        self.assertEqual(event["request_id"], "request-123")
        self.assertEqual(event["temperature"], 0.0)
        self.assertEqual(event["thinking_requested"], {"type": "disabled"})
        self.assertTrue(event["reasoning_content_present"])
        self.assertEqual(event["messages_sha256"], canonical_json_sha256(messages))
        self.assertEqual(event["usage"], {"input": 100, "output": 20, "total": 120})

    def test_sync_and_async_wrappers_persist_real_completion_metadata(self):
        from variance_trace import (
            make_async_completion_wrapper,
            make_sync_completion_wrapper,
        )

        response = SimpleNamespace(
            id="request-456",
            model="resolved-model",
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content="ok", reasoning_content=None),
                )
            ],
            usage=SimpleNamespace(
                prompt_tokens=5,
                completion_tokens=2,
                total_tokens=7,
            ),
        )

        def sync_create(_resource, **_kwargs):
            return response

        async def async_create(_resource, **_kwargs):
            return response

        resource = SimpleNamespace(_client=SimpleNamespace(max_retries=0))
        with tempfile.TemporaryDirectory() as temporary_directory:
            trace_path = Path(temporary_directory) / "calls.jsonl"
            sync_wrapper = make_sync_completion_wrapper(
                sync_create,
                trace_path,
                lambda _params: "answer",
            )
            async_wrapper = make_async_completion_wrapper(
                async_create,
                trace_path,
                lambda _params: "extraction",
            )

            self.assertIs(
                sync_wrapper(resource, model="requested", messages=[]), response
            )
            self.assertIs(
                asyncio.run(async_wrapper(resource, model="requested", messages=[])),
                response,
            )
            events = [json.loads(line) for line in trace_path.read_text().splitlines()]

        self.assertEqual(
            [event["event_type"] for event in events],
            ["attempt", "success", "attempt", "success"],
        )
        self.assertEqual(
            [event["role"] for event in events],
            ["answer", "answer", "extraction", "extraction"],
        )
        self.assertEqual(events[0]["call_id"], events[1]["call_id"])
        self.assertEqual(events[2]["call_id"], events[3]["call_id"])
        self.assertEqual(events[0]["sdk_max_retries"], 0)
        self.assertEqual(events[1]["request_id"], "request-456")

    def test_completion_wrapper_records_a_sanitized_failure_terminal(self):
        from variance_trace import make_sync_completion_wrapper

        def failing_create(_resource, **_kwargs):
            raise RuntimeError("sensitive upstream error text")

        resource = SimpleNamespace(_client=SimpleNamespace(max_retries=0))
        with tempfile.TemporaryDirectory() as temporary_directory:
            trace_path = Path(temporary_directory) / "calls.jsonl"
            wrapper = make_sync_completion_wrapper(
                failing_create,
                trace_path,
                lambda _params: "answer",
            )

            with self.assertRaises(RuntimeError):
                wrapper(resource, model="requested", messages=[])
            events = [json.loads(line) for line in trace_path.read_text().splitlines()]

        self.assertEqual(
            [event["event_type"] for event in events], ["attempt", "failure"]
        )
        self.assertEqual(events[0]["call_id"], events[1]["call_id"])
        self.assertEqual(events[1]["error_type"], "RuntimeError")
        self.assertNotIn("error_message", events[1])
        self.assertNotIn("sensitive upstream error text", json.dumps(events[1]))

    def test_trace_summary_requires_each_expected_runtime_role(self):
        from variance_probe import summarize_completion_events

        events = [
            {
                "event_type": "attempt",
                "call_id": "answer-call",
                "role": "answer",
                "requested_model": "deepseek-v4-pro-202608",
                "temperature": 0.0,
                "messages_sha256": "a" * 64,
                "sdk_max_retries": 0,
            },
            {
                "event_type": "success",
                "call_id": "answer-call",
                "role": "answer",
                "requested_model": "deepseek-v4-pro-202608",
                "resolved_model": "deepseek-v4-pro-202608",
                "request_id": "answer-request",
                "temperature": 0.0,
                "messages_sha256": "a" * 64,
                "thinking_requested": None,
                "reasoning_content_present": True,
                "usage": {"input": 100, "output": 20, "total": 120},
            },
            {
                "event_type": "attempt",
                "call_id": "judge-call",
                "role": "judge",
                "requested_model": "deepseek-v4-flash-202608",
                "temperature": 0.0,
                "messages_sha256": "b" * 64,
                "sdk_max_retries": 0,
            },
            {
                "event_type": "success",
                "call_id": "judge-call",
                "role": "judge",
                "requested_model": "deepseek-v4-flash-202608",
                "resolved_model": "deepseek-v4-flash-202608",
                "request_id": "judge-request",
                "temperature": 0.0,
                "messages_sha256": "b" * 64,
                "thinking_requested": None,
                "reasoning_content_present": False,
                "usage": {"input": 30, "output": 5, "total": 35},
            },
        ]

        expected_models = {
            "answer": "deepseek-v4-pro-202608",
            "judge": "deepseek-v4-flash-202608",
        }
        summary = summarize_completion_events(
            events,
            {"answer": 1, "judge": 1},
            expected_models,
        )

        self.assertEqual(summary["calls_by_role"], {"answer": 1, "judge": 1})
        self.assertEqual(summary["attempt_calls"], 2)
        self.assertEqual(summary["successful_calls"], 2)
        self.assertEqual(summary["failed_calls"], 0)
        self.assertEqual(
            summary["resolved_models"],
            ["deepseek-v4-flash-202608", "deepseek-v4-pro-202608"],
        )
        self.assertEqual(summary["reasoning_content_calls"], 1)

        with self.assertRaisesRegex(ValueError, "missing required calls"):
            summarize_completion_events(
                events[:2],
                {"answer": 1, "judge": 1},
                expected_models,
            )

    def test_trace_summary_rejects_resolved_model_drift(self):
        from variance_probe import summarize_completion_events

        event = {
            "event_type": "success",
            "call_id": "answer-call",
            "role": "answer",
            "requested_model": "deepseek-v4-pro",
            "resolved_model": "unexpected-model",
            "request_id": "answer-request",
            "temperature": 0.0,
            "messages_sha256": "a" * 64,
            "thinking_requested": None,
            "reasoning_content_present": False,
            "usage": {"input": 10, "output": 2, "total": 12},
        }

        with self.assertRaisesRegex(ValueError, "resolved model drift"):
            summarize_completion_events(
                [
                    {
                        "event_type": "attempt",
                        "call_id": "answer-call",
                        "role": "answer",
                        "requested_model": "deepseek-v4-pro",
                        "temperature": 0.0,
                        "messages_sha256": "a" * 64,
                        "sdk_max_retries": 0,
                    },
                    event,
                ],
                {"answer": 1},
                {"answer": "deepseek-v4-pro"},
            )

    def test_trace_summary_rejects_missing_usage(self):
        from variance_probe import summarize_completion_events

        event = {
            "event_type": "success",
            "call_id": "answer-call",
            "role": "answer",
            "requested_model": "deepseek-v4-pro",
            "resolved_model": "deepseek-v4-pro",
            "request_id": "answer-request",
            "temperature": 0.0,
            "messages_sha256": "a" * 64,
            "thinking_requested": None,
            "reasoning_content_present": False,
            "usage": {"input": 10, "output": None, "total": 12},
        }

        with self.assertRaisesRegex(ValueError, "invalid usage"):
            summarize_completion_events(
                [
                    {
                        "event_type": "attempt",
                        "call_id": "answer-call",
                        "role": "answer",
                        "requested_model": "deepseek-v4-pro",
                        "temperature": 0.0,
                        "messages_sha256": "a" * 64,
                        "sdk_max_retries": 0,
                    },
                    event,
                ],
                {"answer": 1},
                {"answer": "deepseek-v4-pro"},
            )

    def test_trace_summary_rejects_an_unmatched_attempt(self):
        from variance_probe import summarize_completion_events

        with self.assertRaisesRegex(ValueError, "terminal outcome"):
            summarize_completion_events(
                [
                    {
                        "event_type": "attempt",
                        "call_id": "unfinished-call",
                        "role": "answer",
                        "requested_model": "deepseek-v4-pro",
                        "temperature": 0.0,
                        "messages_sha256": "a" * 64,
                        "sdk_max_retries": 0,
                    }
                ],
                {"answer": 1},
                {"answer": "deepseek-v4-pro"},
            )

    def test_trace_summary_rejects_opaque_sdk_retries(self):
        from variance_probe import summarize_completion_events

        events = [
            {
                "event_type": "attempt",
                "call_id": "answer-call",
                "role": "answer",
                "requested_model": "deepseek-v4-pro",
                "temperature": 0.0,
                "messages_sha256": "a" * 64,
                "sdk_max_retries": 2,
            },
            {
                "event_type": "success",
                "call_id": "answer-call",
                "role": "answer",
                "resolved_model": "deepseek-v4-pro",
                "request_id": "answer-request",
                "messages_sha256": "a" * 64,
                "thinking_requested": None,
                "reasoning_content_present": False,
                "usage": {"input": 10, "output": 2, "total": 12},
            },
        ]

        with self.assertRaisesRegex(ValueError, "opaque SDK retries"):
            summarize_completion_events(
                events,
                {"answer": 1},
                {"answer": "deepseek-v4-pro"},
            )

    def test_trace_summary_rejects_a_failed_terminal_attempt(self):
        from variance_probe import summarize_completion_events

        events = [
            {
                "event_type": "attempt",
                "call_id": "answer-call",
                "role": "answer",
                "requested_model": "deepseek-v4-pro",
                "temperature": 0.0,
                "messages_sha256": "a" * 64,
                "sdk_max_retries": 0,
            },
            {
                "event_type": "failure",
                "call_id": "answer-call",
                "role": "answer",
                "error_type": "RateLimitError",
                "request_id": "failed-request",
                "status_code": 429,
            },
        ]

        with self.assertRaisesRegex(ValueError, "failed completion attempts"):
            summarize_completion_events(
                events,
                {},
                {"answer": "deepseek-v4-pro"},
            )

    def test_trace_summary_rejects_request_metadata_that_changes_at_success(self):
        from variance_probe import summarize_completion_events

        events = [
            {
                "event_type": "attempt",
                "call_id": "answer-call",
                "role": "answer",
                "requested_model": "deepseek-v4-pro",
                "temperature": 0.0,
                "thinking_requested": None,
                "messages_sha256": "a" * 64,
                "sdk_max_retries": 0,
            },
            {
                "event_type": "success",
                "call_id": "answer-call",
                "role": "answer",
                "requested_model": "deepseek-v4-flash",
                "resolved_model": "deepseek-v4-pro",
                "request_id": "answer-request",
                "temperature": 0.0,
                "thinking_requested": None,
                "messages_sha256": "a" * 64,
                "reasoning_content_present": False,
                "usage": {"input": 10, "output": 2, "total": 12},
            },
        ]

        with self.assertRaisesRegex(ValueError, "request metadata changed"):
            summarize_completion_events(
                events,
                {"answer": 1},
                {"answer": "deepseek-v4-pro"},
            )


class OpenAIRetryConfigurationTests(unittest.TestCase):
    def test_disables_opaque_sdk_retries_on_the_exact_llm_client(self):
        from variance_probe import disable_opaque_openai_retries

        class FakeClient:
            def __init__(self, max_retries):
                self.max_retries = max_retries

            def with_options(self, *, max_retries):
                return FakeClient(max_retries)

        llm = SimpleNamespace(_client=FakeClient(2))

        disable_opaque_openai_retries(llm)

        self.assertEqual(llm._client.max_retries, 0)


class DaemonShutdownTests(unittest.TestCase):
    def test_close_verifies_the_exact_profile_is_no_longer_running(self):
        from variance_probe import close_and_verify_hindsight_daemon

        manager = SimpleNamespace(is_running=mock.Mock(return_value=False))
        client = SimpleNamespace(
            _manager=manager,
            close=mock.Mock(),
        )
        provider = SimpleNamespace(
            _client=client,
            repair_profile="profile-r1",
        )

        close_and_verify_hindsight_daemon(provider)

        client.close.assert_called_once_with(stop_daemon=True)
        manager.is_running.assert_called_once_with("profile-r1")

    def test_close_fails_when_the_exact_profile_remains_running(self):
        from variance_probe import close_and_verify_hindsight_daemon

        manager = SimpleNamespace(is_running=mock.Mock(return_value=True))
        provider = SimpleNamespace(
            _client=SimpleNamespace(_manager=manager, close=mock.Mock()),
            repair_profile="profile-r1",
        )

        with self.assertRaisesRegex(RuntimeError, "still running"):
            close_and_verify_hindsight_daemon(provider)


class ExclusiveOutputTests(unittest.TestCase):
    def test_summary_writer_refuses_to_overwrite_an_existing_file(self):
        from variance_probe import write_json_exclusive

        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "summary.json"
            output.write_text(json.dumps({"keep": True}))

            with self.assertRaises(FileExistsError):
                write_json_exclusive(output, {"replace": True})

            self.assertEqual(json.loads(output.read_text()), {"keep": True})


if __name__ == "__main__":
    unittest.main()
