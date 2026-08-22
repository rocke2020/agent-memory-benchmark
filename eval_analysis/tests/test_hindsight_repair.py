import asyncio
import hashlib
import inspect
import io
import json
import logging
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

EVAL_ANALYSIS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EVAL_ANALYSIS_DIR))


class HindsightDaemonPatchTests(unittest.TestCase):
    def test_safe_encoding_treats_special_token_literal_as_ordinary_text(self):
        import tiktoken
        from hindsight_repair_daemon import _SafeEncoding

        encoding = _SafeEncoding(tiktoken.get_encoding("cl100k_base"))

        tokens = encoding.encode("literal <|endoftext|> text")

        self.assertGreater(len(tokens), 3)
        self.assertEqual(encoding.decode(tokens), "literal <|endoftext|> text")

    def test_unicode_patch_transforms_the_exact_hindsight_0415_fallback(self):
        from hindsight_api.engine.entity_resolver import EntityResolver
        from hindsight_repair_daemon import _patched_entity_resolver_source

        original = inspect.getsource(EntityResolver._resolve_from_candidates)
        patched = _patched_entity_resolver_source(original)

        self.assertNotEqual(patched, original)
        self.assertIn("missing_original", patched)
        self.assertIn("inputs.input_name", patched)
        self.assertIn('id_by_name[row["input_name"].lower()]', patched)


class RepairProviderTests(unittest.TestCase):
    def test_preflight_rejects_extra_source_occurrences_before_paid_work(self):
        from hindsight_repair import REPAIR_QUERY_IDS, _validate_preflight

        source = {
            "dataset": "longmemeval",
            "split": "s",
            "total_queries": 500,
            "results": [
                {"query_id": query_id}
                for query_id in [*sorted(REPAIR_QUERY_IDS)]
                + [f"control-{index:03d}" for index in range(497)]
            ],
        }
        source["results"].append(source["results"][0])

        with (
            mock.patch("hindsight_repair._load_environment"),
            mock.patch("hindsight_repair._validate_expected_runtime_configuration"),
            mock.patch("hindsight_repair.version", return_value="0.4.17"),
            mock.patch(
                "hindsight_repair._load_result",
                return_value=source,
            ),
            self.assertRaisesRegex(ValueError, "exactly 500 entries"),
        ):
            _validate_preflight(
                Path("source.json"),
                "resume-4-test",
                Path("unused-output"),
            )

    def test_database_override_is_rejected_before_constructor_or_network_use(self):
        from hindsight_repair import _validate_expected_runtime_configuration

        with (
            mock.patch.dict(
                "os.environ",
                {"HINDSIGHT_EMBED_API_DATABASE_URL": "postgresql://existing/profile"},
            ),
            self.assertRaisesRegex(ValueError, "must be unset"),
        ):
            _validate_expected_runtime_configuration()

    def test_bank_creation_is_create_only(self):
        from hindsight_repair import RepairHindsightMemoryProvider

        class FakeClient:
            def __init__(self):
                self.created = []
                self.delete_calls = 0

            async def acreate_bank(self, **kwargs):
                self.created.append(kwargs)

            async def adelete_bank(self, **kwargs):
                self.delete_calls += 1

        provider = RepairHindsightMemoryProvider("resume-4-test")
        client = FakeClient()

        asyncio.run(provider._acreate_bank(client, "repair-bank"))

        self.assertEqual(client.delete_calls, 0)
        self.assertEqual(client.created[0]["bank_id"], "repair-bank")

    def test_any_retain_skip_warning_fails_the_question(self):
        from hindsight_repair import (
            RepairHindsightMemoryProvider,
            RetainCompletenessError,
        )

        provider = RepairHindsightMemoryProvider("resume-4-test")

        async def warn_and_return(_provider, _documents):
            logging.getLogger("memory_bench.memory.hindsight").warning(
                "aretain_batch unhandled error (skipping batch): HTTP 500"
            )

        with (
            mock.patch(
                "memory_bench.memory.hindsight.HindsightMemoryProvider.async_ingest",
                new=warn_and_return,
            ),
            self.assertRaises(RetainCompletenessError),
        ):
            asyncio.run(provider.async_ingest([]))

    def test_prepare_adds_repair_namespace_before_profile_start(self):
        from hindsight_repair import RepairHindsightMemoryProvider

        fake_client = SimpleNamespace(url="http://127.0.0.1:1", _manager=None)
        with mock.patch("hindsight.HindsightEmbedded", return_value=fake_client):
            provider = RepairHindsightMemoryProvider("resume-4-test")
            with mock.patch(
                "memory_bench.memory.hindsight._HindsightBase.prepare"
            ) as prepare:
                prepare.side_effect = lambda *_args, **_kwargs: setattr(
                    provider, "_bank_id", "longmemeval-s"
                )
                provider.prepare(
                    Path("outputs/longmemeval/run/_store/s/all"),
                    {"q1"},
                )

        self.assertEqual(provider._bank_id, "longmemeval-s-repair-resume-4-test")

    def test_document_postflight_returns_the_exact_hindsight_document_ids(self):
        from hindsight_repair import RepairHindsightMemoryProvider

        provider = RepairHindsightMemoryProvider("resume-4-test")
        provider._bank_id = "longmemeval-s-repair-resume-4-test"
        provider._per_unit = True
        provider._client = SimpleNamespace(url="http://127.0.0.1:9999")
        response = io.BytesIO(
            json.dumps(
                {
                    "items": [{"id": "doc-1"}, {"id": "doc-2"}],
                    "total": 2,
                    "limit": 1000,
                    "offset": 0,
                }
            ).encode()
        )

        with mock.patch("urllib.request.urlopen", return_value=response):
            document_ids = provider.document_ids("q1")

        self.assertEqual(document_ids, {"doc-1", "doc-2"})


class MergeRepairTests(unittest.TestCase):
    def test_merge_replaces_only_the_three_authorized_queries(self):
        from hindsight_repair import (
            EXPECTED_LONGMEMEVAL_DATA_SHA256,
            REPAIR_QUESTION_DOC_COUNTS,
            _write_retain_attestation,
            merge_repair_results,
        )

        repaired_ids = set(REPAIR_QUESTION_DOC_COUNTS)
        query_ids = [*sorted(repaired_ids)] + [
            f"control-{index:03d}" for index in range(497)
        ]
        source = {
            "dataset": "longmemeval",
            "split": "s",
            "run_name": "hindsight-deepseek",
            "memory_provider": "hindsight",
            "mode": "rag",
            "answer_llm": "openai:deepseek-v4-pro",
            "judge_llm": "openai:deepseek-v4-flash",
            "total_queries": 500,
            "correct": 0,
            "accuracy": 0.0,
            "ingestion_time_ms": 100.0,
            "ingested_docs": 100,
            "results": [
                {
                    "query_id": query_id,
                    "correct": False,
                    "context_tokens": 100 + index,
                    "retrieve_time_ms": 10 + index,
                    "answer": "old answer",
                    "context": "old context",
                }
                for index, query_id in enumerate(query_ids)
            ],
        }
        namespace = "resume-4-test"
        profile = "omb-longmemeval-s-repair-resume-4-test-123fdb68e70b"
        expected_document_ids_by_query = {}

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source_path = root / "source.json"
            source_path.write_text(json.dumps(source))
            source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
            repair_paths = []
            for query_id in sorted(repaired_ids):
                repair_path = root / f"{query_id}.json"
                repair_path.write_text(
                    json.dumps(
                        {
                            **source,
                            "run_name": f"repair-{namespace}-{query_id}",
                            "total_queries": 1,
                            "correct": 1,
                            "accuracy": 1.0,
                            "ingested_docs": REPAIR_QUESTION_DOC_COUNTS[query_id],
                            "description": (
                                "Strict repair; "
                                f"repair_namespace={namespace}; "
                                f"repair_profile={profile}; "
                                "backports=438ce98b4,4bc7013e4; "
                                f"dataset_sha256={EXPECTED_LONGMEMEVAL_DATA_SHA256}."
                            ),
                            "results": [
                                {
                                    "query_id": query_id,
                                    "correct": True,
                                    "context_tokens": 999,
                                    "retrieve_time_ms": 99,
                                    "answer": "repaired answer",
                                    "context": "repaired context",
                                }
                            ],
                        }
                    )
                )
                document_ids = {
                    f"{query_id}-doc-{index}"
                    for index in range(REPAIR_QUESTION_DOC_COUNTS[query_id])
                }
                expected_document_ids_by_query[query_id] = document_ids
                _write_retain_attestation(
                    repair_path,
                    query_id=query_id,
                    namespace=namespace,
                    profile=profile,
                    bank_id=f"longmemeval-s-repair-{namespace}-u{query_id}",
                    expected_document_ids=document_ids,
                    actual_document_ids=document_ids,
                )
                repair_paths.append(repair_path)
            output_path = root / "repaired.json"

            merge_repair_results(
                source_path,
                repair_paths,
                output_path,
                repaired_ids,
                expected_profile=profile,
                expected_document_ids_by_query=expected_document_ids_by_query,
            )

            merged = json.loads(output_path.read_text())
            self.assertEqual(
                hashlib.sha256(source_path.read_bytes()).hexdigest(), source_hash
            )

        self.assertEqual(merged["total_queries"], 500)
        self.assertEqual(merged["correct"], 3)
        self.assertEqual(merged["accuracy"], 3 / 500)
        self.assertEqual(
            {
                result["query_id"]
                for result in merged["results"]
                if result["context_tokens"] == 999
            },
            repaired_ids,
        )
        source_by_id = {result["query_id"]: result for result in source["results"]}
        merged_by_id = {result["query_id"]: result for result in merged["results"]}
        for query_id in set(query_ids) - repaired_ids:
            self.assertEqual(merged_by_id[query_id], source_by_id[query_id])

    def test_merge_rejects_wrong_repair_metadata(self):
        from hindsight_repair import REPAIR_QUERY_IDS, merge_repair_results

        source = {
            "dataset": "longmemeval",
            "split": "s",
            "run_name": "hindsight-deepseek",
            "memory_provider": "hindsight",
            "mode": "rag",
            "answer_llm": "openai:deepseek-v4-pro",
            "judge_llm": "openai:deepseek-v4-flash",
            "total_queries": 500,
            "correct": 0,
            "accuracy": 0.0,
            "results": [
                {"query_id": query_id, "correct": False}
                for query_id in [*sorted(REPAIR_QUERY_IDS)]
                + [f"control-{index:03d}" for index in range(497)]
            ],
        }
        bad_repair = {
            **source,
            "total_queries": 1,
            "correct": 0,
            "accuracy": 0.0,
            "ingested_docs": 999,
            "results": [{"query_id": min(REPAIR_QUERY_IDS), "correct": False}],
        }

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            with (
                mock.patch(
                    "hindsight_repair._load_result",
                    side_effect=[source, bad_repair],
                ),
                mock.patch("hindsight_repair._sha256", return_value="hash"),
                self.assertRaisesRegex(ValueError, "metadata mismatch"),
            ):
                merge_repair_results(
                    root / "source.json",
                    [root / "bad.json"],
                    root / "output.json",
                    REPAIR_QUERY_IDS,
                    expected_profile="repair-profile",
                    expected_document_ids_by_query={
                        query_id: set() for query_id in REPAIR_QUERY_IDS
                    },
                )

    def test_merge_rejects_more_than_500_source_result_occurrences(self):
        from hindsight_repair import REPAIR_QUERY_IDS, merge_repair_results

        source = {
            "dataset": "longmemeval",
            "split": "s",
            "run_name": "hindsight-deepseek",
            "memory_provider": "hindsight",
            "mode": "rag",
            "answer_llm": "openai:deepseek-v4-pro",
            "judge_llm": "openai:deepseek-v4-flash",
            "total_queries": 500,
            "correct": 0,
            "accuracy": 0.0,
            "results": [
                {"query_id": query_id, "correct": False}
                for query_id in [*sorted(REPAIR_QUERY_IDS)]
                + [f"control-{index:03d}" for index in range(497)]
            ],
        }
        source["results"].append(source["results"][0])

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            with (
                mock.patch("hindsight_repair._load_result", return_value=source),
                mock.patch(
                    "hindsight_repair._sha256",
                    return_value="hash",
                ),
                self.assertRaisesRegex(ValueError, "exactly 500 entries"),
            ):
                merge_repair_results(
                    root / "source.json",
                    [],
                    root / "output.json",
                    REPAIR_QUERY_IDS,
                    expected_profile="repair-profile",
                )

    def test_merge_refuses_to_overwrite_an_existing_output(self):
        from hindsight_repair import merge_repair_results

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            existing_output = root / "existing.json"
            existing_output.write_text("keep")

            with self.assertRaises(FileExistsError):
                merge_repair_results(
                    root / "source.json",
                    [],
                    existing_output,
                    set(),
                )


if __name__ == "__main__":
    unittest.main()
