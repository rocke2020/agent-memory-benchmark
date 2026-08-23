import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

EVAL_ANALYSIS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EVAL_ANALYSIS_DIR))


def _result(query_id, correct):
    return {
        "query_id": query_id,
        "query": f"Question {query_id}",
        "gold_answers": [f"Gold {query_id}"],
        "meta": {"question_type": "multi-session"},
        "correct": correct,
        "answer": f"Answer {query_id}",
        "reasoning": "reasoning",
        "context": f"Context {query_id}",
        "context_tokens": 10,
        "retrieve_time_ms": 1.0,
        "judge_reason": "judge",
        "raw_response": {"results": [], "chunks": {}},
        "category_axes": {"Question Type": ["multi-session"]},
    }


def _write_journal(path, result):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result))


class StrictPartialJournalTests(unittest.TestCase):
    def test_recorded_code_provenance_survives_a_post_run_commit(self):
        from deepseek_nondeterminism_repair4 import (
            validate_recorded_repair_code_provenance,
        )

        recorded = {
            "code_sha256": {"repair.py": "a" * 64},
            "git_head": "paid-run-head",
        }
        validate_recorded_repair_code_provenance(
            recorded,
            dict(recorded),
            dict(recorded),
        )
        with patch(
            "deepseek_nondeterminism_repair4._repair_code_hashes",
            return_value={"repair.py": "b" * 64},
        ):
            with self.assertRaisesRegex(ValueError, "current code"):
                validate_recorded_repair_code_provenance(
                    recorded,
                    dict(recorded),
                    require_current_code_hashes=True,
                )

        drifted = {**recorded, "git_head": "different-head"}
        with self.assertRaisesRegex(ValueError, "provenance"):
            validate_recorded_repair_code_provenance(
                recorded,
                dict(recorded),
                drifted,
            )

    def test_bound_dataset_path_avoids_default_cache_and_restores_environment(self):
        from deepseek_nondeterminism_repair4 import _bound_dataset_path

        original = os.environ.pop("LONGMEMEVAL_DATA_PATH", None)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                dataset_path = Path(tmp) / "longmemeval.json"
                dataset_path.write_text("[]")
                with _bound_dataset_path(dataset_path):
                    self.assertEqual(
                        os.environ["LONGMEMEVAL_DATA_PATH"],
                        str(dataset_path.resolve()),
                    )
                self.assertNotIn("LONGMEMEVAL_DATA_PATH", os.environ)
        finally:
            if original is not None:
                os.environ["LONGMEMEVAL_DATA_PATH"] = original

    def test_partial_journal_derives_missing_ids_from_frozen_ordinals(self):
        from deepseek_nondeterminism_repair4 import load_partial_journal

        selected = ("q1", "q2", "q3")
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp)
            _write_journal(journal / "001-q1.json", _result("q1", True))
            _write_journal(journal / "003-q3.json", _result("q3", False))

            results, missing = load_partial_journal(journal, selected)

        self.assertEqual(tuple(results), ("q1", "q3"))
        self.assertEqual(missing, ("q2",))

    def test_partial_journal_rejects_a_result_under_the_wrong_ordinal(self):
        from deepseek_nondeterminism_repair4 import load_partial_journal

        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp)
            _write_journal(journal / "001-q2.json", _result("q2", True))

            with self.assertRaisesRegex(ValueError, "journal filename drifted"):
                load_partial_journal(journal, ("q1", "q2"))


class RepairMergeTests(unittest.TestCase):
    def test_repair_result_publish_validates_envelope_before_writing(self):
        from deepseek_nondeterminism_repair4 import _publish_repair_result_if_needed

        with tempfile.TemporaryDirectory() as tmp:
            result_path = Path(tmp) / "run" / "s.json"
            with (
                patch(
                    "deepseek_nondeterminism_repair4._repair_result_path",
                    return_value=result_path,
                ),
                patch(
                    "deepseek_nondeterminism_repair4._validate_repair_envelope",
                    side_effect=ValueError("recorded code provenance drifted"),
                    create=True,
                ),
            ):
                with self.assertRaisesRegex(ValueError, "provenance"):
                    _publish_repair_result_if_needed(Path(tmp), "study")

            self.assertFalse(result_path.exists())

    def test_offline_finalize_reuses_identical_json_and_rejects_drift(self):
        from deepseek_nondeterminism_repair4 import write_json_create_or_verify

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "attestation.json"
            write_json_create_or_verify(path, {"value": 1})
            write_json_create_or_verify(path, {"value": 1})

            with self.assertRaisesRegex(RuntimeError, "immutable artifact drifted"):
                write_json_create_or_verify(path, {"value": 2})

        self.assertFalse(path.exists())

    def test_offline_analysis_reuses_identical_text_and_rejects_drift(self):
        from deepseek_nondeterminism_repair4 import write_text_create_or_verify

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "analysis.md"
            write_text_create_or_verify(path, "same\n")
            write_text_create_or_verify(path, "same\n")

            with self.assertRaisesRegex(RuntimeError, "immutable artifact drifted"):
                write_text_create_or_verify(path, "changed\n")

        self.assertFalse(path.exists())

    def test_merge_uses_original_and_repair_results_once_in_frozen_order(self):
        from deepseek_nondeterminism_repair4 import build_merged_artifact

        artifact = build_merged_artifact(
            original_results={"q1": _result("q1", True), "q3": _result("q3", False)},
            repair_results={"q2": _result("q2", True)},
            selected_query_ids=("q1", "q2", "q3"),
            repair_query_ids=("q2",),
            document_count=12,
            study_id="study",
        )

        self.assertEqual(
            [result["query_id"] for result in artifact["results"]],
            ["q1", "q2", "q3"],
        )
        self.assertEqual(artifact["correct"], 2)
        self.assertEqual(artifact["total_queries"], 3)
        self.assertEqual(artifact["ingested_docs"], 12)
        self.assertIsNone(artifact["ingestion_time_ms"])

    def test_merge_rejects_repair_replacement_of_a_completed_result(self):
        from deepseek_nondeterminism_repair4 import build_merged_artifact

        with self.assertRaisesRegex(ValueError, "repair result membership"):
            build_merged_artifact(
                original_results={"q1": _result("q1", True)},
                repair_results={"q1": _result("q1", False)},
                selected_query_ids=("q1", "q2"),
                repair_query_ids=("q2",),
                document_count=8,
                study_id="study",
            )


class RepairAttestationTests(unittest.TestCase):
    def _plan(self):
        return {
            "study_id": "study",
            "selection_sha256": "a" * 64,
            "selected_query_ids": ["q1", "q2", "q3"],
            "completed_query_ids": ["q1", "q3"],
            "repair_query_ids": ["q2"],
            "original_profile": "original-profile",
            "repair_profile": "fresh-profile",
            "original_preflight_sha256": "f" * 64,
            "original_completed_document_count": 8,
            "original_completed_retain_batch_count": 2,
            "original_journal_sha256": {"q1": "b" * 64, "q3": "c" * 64},
            "original_trace_sha256": {
                "retain": "d" * 64,
                "omb": "e" * 64,
                "hindsight": "f" * 64,
            },
            "repair_document_count": 4,
            "repair_retain_batch_count": 1,
            "source_sha256": {"dataset": "d" * 64},
        }

    def _repair_attestation(self):
        return {
            "study_id": "study",
            "repair_query_ids": ["q2"],
            "profile": "fresh-profile",
            "daemon_idle_timeout_seconds": 0,
            "daemon_running_postflight": False,
            "document_count": 4,
            "confirmed_document_ids_sha256": {"q2": "1" * 64},
            "retain_batch_trace": {
                "expected_batches": 1,
                "successful_batches": 1,
                "sha256": "2" * 64,
            },
            "omb_completion_trace": {
                "calls_by_role": {"answer": 1, "judge": 1},
                "sha256": "3" * 64,
            },
            "hindsight_completion_trace": {
                "calls_by_role": {
                    "hindsight_extraction": 1,
                    "hindsight_verification": 1,
                },
                "sha256": "4" * 64,
            },
            "result_evidence": {"q2": {}},
            "repair_journal_sha256": {"q2": "8" * 64},
            "result_file_sha256": "5" * 64,
            "preflight_sha256": "a" * 64,
            "code_sha256": {"repair.py": "6" * 64},
            "git_head": "deadbeef",
        }

    def test_repair_attestation_requires_fresh_profile_and_disabled_idle_exit(self):
        from deepseek_nondeterminism_repair4 import validate_repair_attestation

        plan = self._plan()
        attestation = self._repair_attestation()
        validate_repair_attestation(attestation, plan)

        attestation["daemon_idle_timeout_seconds"] = 300
        with self.assertRaisesRegex(ValueError, "idle timeout"):
            validate_repair_attestation(attestation, plan)

        attestation["daemon_idle_timeout_seconds"] = 0
        attestation["profile"] = "original-profile"
        with self.assertRaisesRegex(ValueError, "fresh profile"):
            validate_repair_attestation(attestation, plan)

    def test_merged_attestation_binds_every_result_to_one_source_journal(self):
        from deepseek_nondeterminism_repair4 import validate_merged_attestation

        plan = self._plan()
        repair = self._repair_attestation()
        merged = {
            "schema_version": 2,
            "study_id": "study",
            "selection_sha256": "a" * 64,
            "merge_mode": "original-51-plus-repair-4",
            "query_ids": ["q1", "q2", "q3"],
            "result_file_sha256": "7" * 64,
            "result_evidence": {"q1": {}, "q2": {}, "q3": {}},
            "original_journal_sha256": {"q1": "b" * 64, "q3": "c" * 64},
            "repair_journal_sha256": {"q2": "8" * 64},
            "repair_attestation_sha256": "9" * 64,
            "plan_sha256": "0" * 64,
            "profiles": {
                "original": "original-profile",
                "repair": "fresh-profile",
            },
            "document_evidence": {
                "original_completed_questions": 2,
                "original_completed_documents": 8,
                "repair_questions": 1,
                "repair_documents": 4,
                "total_documents": 12,
            },
            "retain_evidence": {
                "original_complete_batches": 2,
                "repair_batches": 1,
                "total_batches": 3,
            },
            "original_hindsight_failure_provenance": {
                "attempts": 5,
                "terminals": 4,
                "unpaired_attempts": 1,
            },
            "source_sha256": {"dataset": "d" * 64},
            "canary_evidence": {
                "original_preflight_sha256": "f" * 64,
                "original_retain_trace_sha256": "1" * 64,
                "original_hindsight_trace_sha256": "2" * 64,
                "repair_preflight_sha256": "a" * 64,
                "repair_retain_trace_sha256": "3" * 64,
                "repair_hindsight_trace_sha256": "4" * 64,
            },
        }
        validate_merged_attestation(merged, plan, repair)

        merged["result_evidence"].pop("q3")
        with self.assertRaisesRegex(ValueError, "merged attestation"):
            validate_merged_attestation(merged, plan, repair)

    def test_merged_attestation_requires_original_and_repair_canaries(self):
        from deepseek_nondeterminism_repair4 import validate_merged_attestation

        plan = self._plan()
        repair = self._repair_attestation()
        merged = {
            "schema_version": 2,
            "study_id": "study",
            "selection_sha256": "a" * 64,
            "merge_mode": "original-51-plus-repair-4",
            "query_ids": ["q1", "q2", "q3"],
            "result_file_sha256": "7" * 64,
            "result_evidence": {"q1": {}, "q2": {}, "q3": {}},
            "original_journal_sha256": {"q1": "b" * 64, "q3": "c" * 64},
            "repair_journal_sha256": {"q2": "8" * 64},
            "repair_attestation_sha256": "9" * 64,
            "plan_sha256": "0" * 64,
            "profiles": {
                "original": "original-profile",
                "repair": "fresh-profile",
            },
            "document_evidence": {
                "original_completed_questions": 2,
                "original_completed_documents": 8,
                "repair_questions": 1,
                "repair_documents": 4,
                "total_documents": 12,
            },
            "retain_evidence": {
                "original_complete_batches": 2,
                "repair_batches": 1,
                "total_batches": 3,
            },
            "original_hindsight_failure_provenance": {
                "attempts": 5,
                "terminals": 4,
                "unpaired_attempts": 1,
            },
            "source_sha256": {"dataset": "d" * 64},
            "canary_evidence": {
                "original_preflight_sha256": "f" * 64,
                "original_retain_trace_sha256": "1" * 64,
                "original_hindsight_trace_sha256": "2" * 64,
                "repair_preflight_sha256": "a" * 64,
                "repair_retain_trace_sha256": "3" * 64,
                "repair_hindsight_trace_sha256": "4" * 64,
            },
        }
        validate_merged_attestation(merged, plan, repair)

        merged["canary_evidence"].pop("repair_hindsight_trace_sha256")
        with self.assertRaisesRegex(ValueError, "canary evidence"):
            validate_merged_attestation(merged, plan, repair)

        merged["canary_evidence"]["repair_hindsight_trace_sha256"] = "4" * 64
        merged["retain_evidence"]["total_batches"] = 2
        with self.assertRaisesRegex(ValueError, "component evidence"):
            validate_merged_attestation(merged, plan, repair)


if __name__ == "__main__":
    unittest.main()
