import gzip
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

EVAL_ANALYSIS_DIR = Path(__file__).resolve().parents[1]
SCRIPT = EVAL_ANALYSIS_DIR / "amb_report.py"
FIXTURES = Path(__file__).parent / "fixtures"


class InspectCommandTests(unittest.TestCase):
    def test_inspect_recomputes_metrics_from_query_results(self):
        completed = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "inspect",
                str(FIXTURES / "candidate.json"),
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        summary = json.loads(completed.stdout)
        self.assertEqual(
            summary,
            {
                "accuracy": 2 / 3,
                "categories": [
                    {"accuracy": 1.0, "correct": 2, "name": "alpha", "total": 2},
                    {"accuracy": 0.0, "correct": 0, "name": "beta", "total": 1},
                ],
                "context_tokens_mean": 200.0,
                "correct": 2,
                "dataset": "demo",
                "ingest_ms_per_doc": 200.0,
                "memory_provider": "candidate-memory",
                "recall_ms_p50": 200.0,
                "run_name": "candidate-memory",
                "split": "s",
                "total_queries": 3,
            },
        )

    def test_inspect_accepts_gzip_compressed_results(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            compressed_path = Path(temporary_directory) / "candidate.json.gz"
            compressed_path.write_bytes(
                gzip.compress((FIXTURES / "candidate.json").read_bytes())
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "inspect",
                    str(compressed_path),
                    "--json",
                ],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["total_queries"], 3)

    def test_inspect_rejects_aggregates_that_disagree_with_results(self):
        invalid_cases = [
            ("total_queries", 4, "total_queries=4 does not match results value 3"),
            ("correct", 1, "correct=1 does not match results value 2"),
            ("accuracy", 0.5, "accuracy=0.5 does not match results value"),
        ]
        for field, invalid_value, expected_error in invalid_cases:
            with (
                self.subTest(field=field),
                tempfile.TemporaryDirectory() as temporary_directory,
            ):
                invalid_result = json.loads((FIXTURES / "candidate.json").read_text())
                invalid_result[field] = invalid_value
                invalid_path = Path(temporary_directory) / "invalid.json"
                invalid_path.write_text(json.dumps(invalid_result))
                completed = subprocess.run(
                    [
                        sys.executable,
                        str(SCRIPT),
                        "inspect",
                        str(invalid_path),
                        "--json",
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn(expected_error, completed.stderr)
            self.assertNotIn("Traceback", completed.stderr)

    def test_inspect_rejects_duplicate_query_ids(self):
        invalid_result = json.loads((FIXTURES / "candidate.json").read_text())
        invalid_result["results"][2]["query_id"] = "q1"
        with tempfile.TemporaryDirectory() as temporary_directory:
            invalid_path = Path(temporary_directory) / "invalid.json"
            invalid_path.write_text(json.dumps(invalid_result))
            completed = subprocess.run(
                [sys.executable, str(SCRIPT), "inspect", str(invalid_path), "--json"],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("duplicate query_id", completed.stderr)
        self.assertNotIn("Traceback", completed.stderr)


class CompareCommandTests(unittest.TestCase):
    def test_compare_pairs_verdicts_by_query_id_and_selects_candidate_errors(self):
        completed = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "compare",
                str(FIXTURES / "candidate.json"),
                str(FIXTURES / "reference.json"),
                "--sample-errors",
                "1",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        comparison = json.loads(completed.stdout)
        self.assertEqual(comparison["matched_queries"], 3)
        self.assertEqual(comparison["candidate_only_query_ids"], [])
        self.assertEqual(comparison["reference_only_query_ids"], [])
        self.assertEqual(
            comparison["verdict_transitions"],
            {
                "both_fail": 0,
                "both_pass": 1,
                "reference_fail_candidate_pass": 1,
                "reference_pass_candidate_fail": 1,
            },
        )
        self.assertEqual(comparison["accuracy_delta"], 0.0)
        self.assertIn("input_mismatches", comparison)
        self.assertEqual(
            comparison["input_mismatches"], {"gold_answers": 0, "meta": 0, "query": 0}
        )
        self.assertEqual(
            comparison["verdict_transition_query_ids"],
            {
                "both_fail": [],
                "both_pass": ["q1"],
                "reference_fail_candidate_pass": ["q3"],
                "reference_pass_candidate_fail": ["q2"],
            },
        )
        self.assertEqual(comparison["candidate_summary"]["context_tokens_mean"], 200.0)
        self.assertEqual(comparison["reference_summary"]["context_tokens_mean"], 190.0)
        self.assertEqual(
            comparison["category_deltas"],
            [
                {
                    "candidate_accuracy": 1.0,
                    "candidate_correct": 2,
                    "delta": 0.5,
                    "name": "alpha",
                    "reference_accuracy": 0.5,
                    "reference_correct": 1,
                    "total": 2,
                },
                {
                    "candidate_accuracy": 0.0,
                    "candidate_correct": 0,
                    "delta": -1.0,
                    "name": "beta",
                    "reference_accuracy": 1.0,
                    "reference_correct": 1,
                    "total": 1,
                },
            ],
        )
        self.assertEqual(
            comparison["sample_errors"],
            [
                {
                    "category": "beta",
                    "query": "Question two?",
                    "query_id": "q2",
                    "reference_correct": True,
                }
            ],
        )

    def test_compare_error_sample_round_robins_across_categories(self):
        candidate = json.loads((FIXTURES / "candidate.json").read_text())
        reference = json.loads((FIXTURES / "reference.json").read_text())
        candidate["results"][0]["correct"] = False
        candidate["results"][2]["correct"] = False
        candidate_four = dict(candidate["results"][0])
        candidate_four.update({"query_id": "q4", "query": "Question four?"})
        candidate["results"].append(candidate_four)
        candidate.update({"total_queries": 4, "correct": 0, "accuracy": 0.0})

        reference_four = dict(reference["results"][0])
        reference_four.update({"query_id": "q4", "query": "Question four?"})
        reference["results"].append(reference_four)
        reference.update({"total_queries": 4, "correct": 3, "accuracy": 0.75})

        with tempfile.TemporaryDirectory() as temporary_directory:
            candidate_path = Path(temporary_directory) / "candidate.json"
            reference_path = Path(temporary_directory) / "reference.json"
            candidate_path.write_text(json.dumps(candidate))
            reference_path.write_text(json.dumps(reference))
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "compare",
                    str(candidate_path),
                    str(reference_path),
                    "--sample-errors",
                    "3",
                    "--json",
                ],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            [row["query_id"] for row in json.loads(completed.stdout)["sample_errors"]],
            ["q1", "q2", "q4"],
        )

    def test_compare_rejects_different_query_populations(self):
        candidate = json.loads((FIXTURES / "candidate.json").read_text())
        candidate["results"].pop()
        candidate.update({"total_queries": 2, "correct": 1, "accuracy": 0.5})

        with tempfile.TemporaryDirectory() as temporary_directory:
            candidate_path = Path(temporary_directory) / "candidate.json"
            candidate_path.write_text(json.dumps(candidate))
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "compare",
                    str(candidate_path),
                    str(FIXTURES / "reference.json"),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(completed.returncode, 2)
        self.assertIn("query-ID sets differ", completed.stderr)
        self.assertNotIn("accuracy_delta", completed.stdout)

    def test_compare_rejects_paired_input_drift(self):
        candidate = json.loads((FIXTURES / "candidate.json").read_text())
        candidate["results"][0]["query"] = "Changed question"

        with tempfile.TemporaryDirectory() as temporary_directory:
            candidate_path = Path(temporary_directory) / "candidate.json"
            candidate_path.write_text(json.dumps(candidate))
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "compare",
                    str(candidate_path),
                    str(FIXTURES / "reference.json"),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(completed.returncode, 2)
        self.assertIn("paired inputs differ", completed.stderr)
        self.assertNotIn("accuracy_delta", completed.stdout)


class ServeCommandTests(unittest.TestCase):
    def test_serve_exposes_the_official_report_ui_and_only_the_selected_result(self):
        process = subprocess.Popen(
            [
                sys.executable,
                str(SCRIPT),
                "serve",
                str(FIXTURES / "candidate.json"),
                "--host",
                "127.0.0.1",
                "--port",
                "0",
                "--no-open",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            self.assertIsNotNone(process.stdout)
            ready_line = process.stdout.readline().strip()
            self.assertTrue(ready_line.startswith("Serving "), ready_line)
            base_url = ready_line.removeprefix("Serving ").removesuffix("/")

            with urllib.request.urlopen(
                f"{base_url}/api/run-url?file=report%2Fdata.json", timeout=5
            ) as response:
                self.assertEqual(json.load(response), {"url": "/report/data.json"})

            with urllib.request.urlopen(
                f"{base_url}/report/data.json", timeout=5
            ) as response:
                self.assertEqual(json.load(response)["run_name"], "candidate-memory")

            with urllib.request.urlopen(
                f"{base_url}/run/report%2Fdata.json?id=q2", timeout=5
            ) as response:
                index_html = response.read().decode()
            self.assertIn('<meta name="color-scheme" content="light dark">', index_html)
            self.assertNotIn("fonts.googleapis.com", index_html)
            self.assertNotIn("analytics.hindsight.vectorize.io", index_html)

            with urllib.request.urlopen(
                f"{base_url}/assets/index-Btp5tVca.js", timeout=5
            ) as response:
                javascript = response.read()
            self.assertEqual(
                hashlib.sha256(javascript).hexdigest(),
                "2928b3fa778333c933659d582df3f5dbb8aac294a146832d7fdc3a264d4a24e6",
            )

            with urllib.request.urlopen(
                f"{base_url}/assets/offline-theme.css", timeout=5
            ) as response:
                offline_theme = response.read().decode()
            self.assertIn("--verdict-pass-background:", offline_theme)
            self.assertIn("--verdict-fail-background:", offline_theme)
            self.assertIn(
                "background-color: var(--verdict-pass-background)", offline_theme
            )
            self.assertIn(
                "background-color: var(--verdict-fail-background)", offline_theme
            )

            with self.assertRaises(urllib.error.HTTPError) as blocked_request:
                urllib.request.urlopen(
                    f"{base_url}/api/run-url?file=..%2F..%2F.env", timeout=5
                )
            self.assertEqual(blocked_request.exception.code, 404)
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            if process.stdout:
                process.stdout.close()
            if process.stderr:
                process.stderr.close()

    def test_serve_marks_compressed_results_for_browser_decompression(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            compressed_path = Path(temporary_directory) / "candidate.json.gz"
            original_bytes = (FIXTURES / "candidate.json").read_bytes()
            compressed_path.write_bytes(gzip.compress(original_bytes))
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(SCRIPT),
                    "serve",
                    str(compressed_path),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "0",
                    "--no-open",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                self.assertIsNotNone(process.stdout)
                ready_line = process.stdout.readline().strip()
                self.assertTrue(ready_line.startswith("Serving "), ready_line)
                base_url = ready_line.removeprefix("Serving ").removesuffix("/")
                request = urllib.request.Request(
                    f"{base_url}/report/data.json",
                    headers={"Accept-Encoding": "identity"},
                )
                with urllib.request.urlopen(request, timeout=5) as response:
                    self.assertEqual(response.headers["Content-Encoding"], "gzip")
                    self.assertEqual(gzip.decompress(response.read()), original_bytes)
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                if process.stdout:
                    process.stdout.close()
                if process.stderr:
                    process.stderr.close()

    def test_serve_treats_a_cancelled_large_download_as_a_normal_client_disconnect(
        self,
    ):
        sys.path.insert(0, str(EVAL_ANALYSIS_DIR))
        try:
            import amb_report
        finally:
            sys.path.pop(0)

        handler_class = amb_report._report_handler(
            FIXTURES / "candidate.json",
            {},
            amb_report.UI_DIRECTORY,
        )
        handler = handler_class.__new__(handler_class)
        handler.send_response = lambda *_: None
        handler.send_header = lambda *_: None
        handler.end_headers = lambda: None

        class DisconnectingWriter:
            def write(self, _: bytes) -> None:
                raise BrokenPipeError("client cancelled the download")

        handler.wfile = DisconnectingWriter()
        try:
            handler._send_file(FIXTURES / "candidate.json")
        except BrokenPipeError as error:
            self.fail(f"client disconnect escaped the report handler: {error}")


if __name__ == "__main__":
    unittest.main()
