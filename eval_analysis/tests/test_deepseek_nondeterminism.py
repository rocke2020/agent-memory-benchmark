import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

EVAL_ANALYSIS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EVAL_ANALYSIS_DIR))


def _result(query_id, question_type, correct):
    return {
        "query_id": query_id,
        "query": f"Question {query_id}",
        "gold_answers": [f"Gold {query_id}"],
        "meta": {"question_type": question_type},
        "correct": correct,
        "answer": f"Answer {query_id}",
        "reasoning": "reasoning",
        "context": f"Context {query_id}",
        "context_tokens": 10,
        "retrieve_time_ms": 1.0,
        "judge_reason": "judge",
        "raw_response": {"results": []},
        "category_axes": {"Question Type": [question_type]},
    }


def _artifact(outcomes):
    return {
        "dataset": "longmemeval",
        "split": "s",
        "results": [
            _result(query_id, question_type, correct)
            for query_id, question_type, correct in outcomes
        ],
    }


class SelectionTests(unittest.TestCase):
    def test_selector_uses_status_type_exclusions_and_family_disjointness(self):
        from deepseek_nondeterminism import select_questions

        candidate = _artifact(
            [
                ("pilot-a", "type-a", False),
                ("error-a", "type-a", False),
                ("excluded-error", "type-a", False),
                ("error-b", "type-b", False),
                ("pilot-a_abs", "type-a", True),
                ("control-a", "type-a", True),
                ("control-a2", "type-a", True),
                ("control-b", "type-b", True),
            ]
        )

        selected = select_questions(
            candidate,
            study_id="study",
            pilot_query_ids=("pilot-a",),
            excluded_query_ids={"excluded-error"},
            quotas={"type-a": 2, "type-b": 1},
        )

        self.assertEqual(
            selected,
            {
                "pilot_query_ids": ["pilot-a"],
                "new_error_query_ids": ["error-a", "error-b"],
                "control_query_ids": ["control-a", "control-a2", "control-b"],
            },
        )

    def test_frozen_manifest_hash_fails_closed_on_status_drift(self):
        from deepseek_nondeterminism import (
            build_selection_manifest,
            selection_manifest_sha256,
        )

        candidate = _artifact(
            [
                ("pilot", "type-a", False),
                ("error", "type-a", False),
                ("control", "type-a", True),
                ("control-2", "type-a", True),
            ]
        )
        reference = _artifact(
            [
                ("pilot", "type-a", True),
                ("error", "type-a", True),
                ("control", "type-a", True),
                ("control-2", "type-a", True),
            ]
        )
        manifest = build_selection_manifest(
            candidate,
            reference,
            study_id="study",
            pilot_query_ids=("pilot",),
            excluded_query_ids=set(),
            quotas={"type-a": 2},
            analysis_frame={
                "questions": 4,
                "original_errors": 2,
                "original_correct": 2,
                "observed_gap_verdicts": 2,
            },
            exclusions={
                "resume_1_query_count": 0,
                "repair_query_ids": [],
                "gold_ambiguous_query_ids": [],
            },
        )
        frozen_hash = selection_manifest_sha256(manifest)
        candidate["results"][1]["correct"] = True

        with self.assertRaisesRegex(
            ValueError,
            "analysis frame drifted|selection manifest SHA-256",
        ):
            build_selection_manifest(
                candidate,
                reference,
                study_id="study",
                pilot_query_ids=("pilot",),
                excluded_query_ids=set(),
                quotas={"type-a": 2},
                analysis_frame={
                    "questions": 4,
                    "original_errors": 2,
                    "original_correct": 2,
                    "observed_gap_verdicts": 2,
                },
                exclusions={
                    "resume_1_query_count": 0,
                    "repair_query_ids": [],
                    "gold_ambiguous_query_ids": [],
                },
                expected_manifest_sha256=frozen_hash,
            )


class ImmutableJournalTests(unittest.TestCase):
    def test_runner_writes_immutable_per_question_checkpoints(self):
        from deepseek_nondeterminism import JournaledEvalRunner

        first = SimpleNamespace(**_result("q1", "type-a", True))
        second = SimpleNamespace(**_result("q2", "type-a", False))
        with tempfile.TemporaryDirectory() as tmp:
            journal_dir = Path(tmp) / "journal"
            runner = JournaledEvalRunner(
                output_dir=Path(tmp) / "work",
                selected_query_ids=("q1", "q2"),
                journal_dir=journal_dir,
                initialize_judge=False,
            )
            runner._save(SimpleNamespace(results=[first]))
            runner._save(SimpleNamespace(results=[first, second]))

            checkpoints = sorted(journal_dir.glob("*.json"))
            self.assertEqual(
                [path.name for path in checkpoints],
                ["001-q1.json", "002-q2.json"],
            )
            self.assertEqual(json.loads(checkpoints[0].read_text())["query_id"], "q1")

            changed = SimpleNamespace(
                **{**_result("q1", "type-a", True), "answer": "changed"}
            )
            with self.assertRaisesRegex(RuntimeError, "immutable checkpoint changed"):
                runner._save(SimpleNamespace(results=[changed]))

    def test_atomic_exclusive_json_never_overwrites(self):
        from deepseek_nondeterminism import write_json_atomic_exclusive

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "result.json"
            write_json_atomic_exclusive(path, {"value": 1})
            with self.assertRaises(FileExistsError):
                write_json_atomic_exclusive(path, {"value": 2})
            self.assertEqual(json.loads(path.read_text()), {"value": 1})

    def test_final_artifact_is_rebuilt_from_the_complete_journal(self):
        from deepseek_nondeterminism import build_final_artifact

        summary = SimpleNamespace(
            dataset="longmemeval",
            split="s",
            category=None,
            memory_provider="hindsight",
            run_name="study",
            mode="rag",
            oracle=False,
            total_queries=2,
            correct=1,
            accuracy=0.5,
            ingestion_time_ms=10.0,
            ingested_docs=4,
            description="study",
            answer_llm="openai:deepseek-v4-pro",
            judge_llm="openai:deepseek-v4-flash",
            results=[],
        )
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp)
            (journal / "001-q1.json").write_text(
                json.dumps(_result("q1", "type-a", True))
            )
            (journal / "002-q2.json").write_text(
                json.dumps(_result("q2", "type-a", False))
            )

            artifact = build_final_artifact(summary, journal, ("q1", "q2"))

        self.assertEqual(artifact["total_queries"], 2)
        self.assertEqual(artifact["correct"], 1)
        self.assertEqual(
            [result["query_id"] for result in artifact["results"]],
            ["q1", "q2"],
        )


class AnalysisTests(unittest.TestCase):
    def test_analysis_averages_pilot_replicas_before_population_weighting(self):
        from deepseek_nondeterminism import analyze_study

        candidate = _artifact(
            [
                ("pilot", "type-a", False),
                ("new-error", "type-a", False),
                ("control", "type-a", True),
            ]
        )
        manifest = {
            "study_id": "study",
            "analysis_frame": {
                "questions": 3,
                "original_errors": 2,
                "original_correct": 1,
                "observed_gap_verdicts": 1,
            },
            "pilot_query_ids": ["pilot"],
            "new_error_query_ids": ["new-error"],
            "control_query_ids": ["control"],
            "exclusions": {
                "resume_1_query_count": 0,
                "repair_query_ids": [],
                "gold_ambiguous_query_ids": [],
            },
        }
        pilot = {
            "questions": [
                {"query_id": "pilot", "replica_verdicts": [True, False, True]}
            ]
        }
        rerun = _artifact(
            [
                ("new-error", "type-a", True),
                ("control", "type-a", False),
            ]
        )

        analysis = analyze_study(
            candidate,
            pilot,
            rerun,
            manifest,
            population_by_type={"type-a": {"errors": 2, "correct": 1}},
        )

        self.assertEqual(
            analysis["raw_new_run"],
            {
                "recoveries": 1,
                "regressions": 1,
                "gross_flips": 2,
                "net_change": 0,
            },
        )
        self.assertAlmostEqual(
            analysis["by_question_type"]["type-a"]["recovery_rate"],
            5 / 6,
        )
        self.assertEqual(
            analysis["by_question_type"]["type-a"]["regression_rate"],
            1.0,
        )
        self.assertAlmostEqual(analysis["combined"]["net_verdicts"], 2 / 3)
        self.assertAlmostEqual(analysis["combined"]["gross_verdicts"], 8 / 3)
        self.assertEqual(
            [
                row["net_verdicts"]
                for row in analysis["pilot_replica_sensitivity"]
            ],
            [1.0, 0.0, 1.0],
        )


class PaidRunValidationTests(unittest.TestCase):
    def test_live_preflight_requires_both_thinking_model_probes(self):
        from deepseek_nondeterminism import validate_live_model_probes

        valid = [
            {
                "requested_model": model,
                "resolved_model": model,
                "request_id": f"request-{model}",
                "usage": {"input": 1, "output": 1, "total": 2},
                "reasoning_content_present": True,
            }
            for model in ("deepseek-v4-pro", "deepseek-v4-flash")
        ]
        validate_live_model_probes(valid)

        with self.assertRaisesRegex(ValueError, "live model probes"):
            validate_live_model_probes(valid[:1])

        valid[0]["reasoning_content_present"] = False
        with self.assertRaisesRegex(ValueError, "thinking evidence"):
            validate_live_model_probes(valid)

    def test_analysis_rejects_an_incomplete_run_attestation(self):
        from deepseek_nondeterminism import validate_run_attestation

        manifest = {
            "study_id": "study",
            "new_error_query_ids": ["error"],
            "control_query_ids": ["control"],
        }
        with self.assertRaisesRegex(ValueError, "run attestation is incomplete"):
            validate_run_attestation({}, manifest)

    def test_hindsight_canary_requires_stopped_daemon_and_extraction_evidence(self):
        from deepseek_nondeterminism import validate_hindsight_canary

        valid = {
            "profile": "canary-profile",
            "daemon_running_postflight": False,
            "confirmed_document_count": 1,
            "retain_batch_trace": {
                "expected_batches": 1,
                "successful_batches": 1,
                "path": "/tmp/retain.jsonl",
                "sha256": "a" * 64,
            },
            "hindsight_completion_trace": {
                "calls_by_role": {
                    "hindsight_extraction": 1,
                    "hindsight_verification": 1,
                },
                "path": "/tmp/completions.jsonl",
                "sha256": "b" * 64,
            },
        }
        validate_hindsight_canary(valid)

        valid["daemon_running_postflight"] = True
        with self.assertRaisesRegex(ValueError, "Hindsight canary"):
            validate_hindsight_canary(valid)

    def test_retrieval_isolation_rejects_a_foreign_document(self):
        from deepseek_nondeterminism import validate_retrieval_isolation

        result = _result("q1", "type-a", True)
        result["raw_response"] = {
            "results": [
                {
                    "document_id": "q2-document",
                    "chunk_id": "profile_q2-document_0",
                }
            ],
            "chunks": {
                "profile_q2-document_0": {"id": "profile_q2-document_0"}
            },
        }

        with self.assertRaisesRegex(RuntimeError, "foreign document"):
            validate_retrieval_isolation(
                result,
                expected_document_ids={"q1-document"},
            )

    def test_retrieval_isolation_rejects_an_orphan_foreign_chunk(self):
        from deepseek_nondeterminism import validate_retrieval_isolation

        result = _result("q1", "type-a", True)
        result["raw_response"] = {
            "results": [],
            "chunks": {
                "profile_q2-document_0": {"id": "profile_q2-document_0"}
            },
        }

        with self.assertRaisesRegex(RuntimeError, "foreign chunk"):
            validate_retrieval_isolation(
                result,
                expected_document_ids={"q1-document"},
            )

    def test_retain_receipts_require_one_success_for_every_frozen_batch(self):
        from deepseek_nondeterminism import summarize_retain_receipts

        events = [
            {
                "event_type": "attempt",
                "attempt_id": "attempt-a",
                "batch_id": "batch-a",
                "retain_async": False,
            },
            {
                "event_type": "success",
                "attempt_id": "attempt-a",
                "batch_id": "batch-a",
            },
        ]
        summary = summarize_retain_receipts(events, {"batch-a"})
        self.assertEqual(summary["successful_batches"], 1)

        with self.assertRaisesRegex(ValueError, "batch receipt mismatch"):
            summarize_retain_receipts(events, {"batch-a", "batch-b"})

    def test_completion_sidecar_requires_answer_and_judge_for_each_query(self):
        from deepseek_nondeterminism import completion_metadata_by_query

        def success(call_id, role, scope):
            return {
                "event_type": "success",
                "call_id": call_id,
                "role": role,
                "scope": scope,
                "request_id": f"request-{call_id}",
                "messages_sha256": "a" * 64,
                "usage": {"input": 1, "output": 1, "total": 2},
            }

        events = [success("a", "answer", "q1"), success("j", "judge", "q1")]
        metadata = completion_metadata_by_query(events, ("q1",))
        self.assertEqual(metadata["q1"]["answer"]["request_id"], "request-a")
        self.assertEqual(metadata["q1"]["judge"]["request_id"], "request-j")

        with self.assertRaisesRegex(ValueError, "missing scoped completion"):
            completion_metadata_by_query(events[:1], ("q1",))


if __name__ == "__main__":
    unittest.main()
