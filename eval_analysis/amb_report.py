#!/usr/bin/env python3
"""Inspect and locally serve Agent Memory Benchmark result files."""

from __future__ import annotations

import argparse
import gzip
import json
import mimetypes
import statistics
import sys
import webbrowser
from collections import defaultdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

VERDICT_TRANSITIONS = (
    "both_fail",
    "both_pass",
    "reference_fail_candidate_pass",
    "reference_pass_candidate_fail",
)
REPORT_DATA_ROUTE = "/report/data.json"
REPORT_FILE_ID = "report/data.json"
UI_DIRECTORY = Path(__file__).resolve().parent / "vendor" / "amb-ui"


def load_amb_result(path: Path) -> dict[str, Any]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as handle:
        data = json.load(handle)

    if not isinstance(data, dict):
        raise ValueError("AMB result root must be a JSON object")  # noqa: TRY004
    results = data.get("results")
    if not isinstance(results, list) or not results:
        raise ValueError("AMB result must contain a non-empty results list")
    if data.get("total_queries") != len(results):
        raise ValueError(
            f"total_queries={data.get('total_queries')!r} does not match results value {len(results)!r}"
        )
    query_ids = [
        result.get("query_id") for result in results if isinstance(result, dict)
    ]
    if len(query_ids) != len(results) or any(not query_id for query_id in query_ids):
        raise ValueError("Every result must contain a non-empty query_id")
    if len(set(query_ids)) != len(query_ids):
        raise ValueError("AMB result contains duplicate query_id values")
    actual_correct = sum(result.get("correct") is True for result in results)
    if data.get("correct") != actual_correct:
        raise ValueError(
            f"correct={data.get('correct')!r} does not match results value {actual_correct!r}"
        )
    actual_accuracy = actual_correct / len(results)
    try:
        stored_accuracy = float(data["accuracy"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("accuracy must be numeric") from error
    if abs(stored_accuracy - actual_accuracy) > 1e-12:
        raise ValueError(
            f"accuracy={data.get('accuracy')!r} does not match results value {actual_accuracy!r}"
        )

    return data


def _result_category(result: dict[str, Any]) -> str:
    axes = result.get("category_axes")
    if isinstance(axes, dict):
        for values in axes.values():
            if isinstance(values, list) and values:
                return str(values[0])
    meta = result.get("meta")
    if isinstance(meta, dict):
        for field in ("question_type", "category"):
            if meta.get(field):
                return str(meta[field])
    return "uncategorized"


def summarize_result(data: dict[str, Any]) -> dict[str, Any]:
    results = data["results"]
    category_results: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in results:
        category_results[_result_category(result)].append(result)

    context_tokens = [
        result["context_tokens"]
        for result in results
        if result.get("context_tokens") is not None
    ]
    recall_times = [
        result["retrieve_time_ms"]
        for result in results
        if result.get("retrieve_time_ms") is not None
    ]
    ingested_docs = data.get("ingested_docs") or 0
    ingestion_time_ms = data.get("ingestion_time_ms")

    return {
        "accuracy": data["accuracy"],
        "categories": [
            {
                "accuracy": sum(result.get("correct") is True for result in grouped)
                / len(grouped),
                "correct": sum(result.get("correct") is True for result in grouped),
                "name": name,
                "total": len(grouped),
            }
            for name, grouped in sorted(category_results.items())
        ],
        "context_tokens_mean": statistics.fmean(context_tokens)
        if context_tokens
        else None,
        "correct": data["correct"],
        "dataset": data.get("dataset"),
        "ingest_ms_per_doc": ingestion_time_ms / ingested_docs
        if ingestion_time_ms is not None and ingested_docs
        else None,
        "memory_provider": data.get("memory_provider"),
        "recall_ms_p50": statistics.median(recall_times) if recall_times else None,
        "run_name": data.get("run_name"),
        "split": data.get("split"),
        "total_queries": data["total_queries"],
    }


def _select_error_sample(
    candidate_by_id: dict[str, dict[str, Any]],
    reference_by_id: dict[str, dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    candidate_errors = [
        result
        for result in candidate_by_id.values()
        if result.get("correct") is not True
    ]
    errors_by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in candidate_errors:
        errors_by_category[_result_category(result)].append(result)
    for category_errors in errors_by_category.values():
        category_errors.sort(
            key=lambda result: (
                reference_by_id.get(result["query_id"], {}).get("correct") is not True,
                result["query_id"],
            )
        )
    ordered_categories = sorted(
        errors_by_category, key=lambda name: (-len(errors_by_category[name]), name)
    )
    selected: list[dict[str, Any]] = []
    while len(selected) < limit:
        added = False
        for name in ordered_categories:
            if errors_by_category[name]:
                selected.append(errors_by_category[name].pop(0))
                added = True
                if len(selected) == limit:
                    break
        if not added:
            break
    return [
        {
            "category": _result_category(result),
            "query": result.get("query", ""),
            "query_id": result["query_id"],
            "reference_correct": reference_by_id.get(result["query_id"], {}).get(
                "correct"
            )
            is True,
        }
        for result in selected
    ]


def compare_results(
    candidate: dict[str, Any], reference: dict[str, Any], sample_errors: int = 0
) -> dict[str, Any]:
    candidate_by_id = {result["query_id"]: result for result in candidate["results"]}
    reference_by_id = {result["query_id"]: result for result in reference["results"]}
    candidate_ids = set(candidate_by_id)
    reference_ids = set(reference_by_id)
    candidate_only_ids = sorted(candidate_ids - reference_ids)
    reference_only_ids = sorted(reference_ids - candidate_ids)
    if candidate_only_ids or reference_only_ids:
        raise ValueError(
            "candidate and reference query-ID sets differ: "
            f"candidate_only={candidate_only_ids} reference_only={reference_only_ids}"
        )
    matched_ids = sorted(candidate_ids & reference_ids)
    input_mismatches = {
        field: sum(
            candidate_by_id[query_id].get(field) != reference_by_id[query_id].get(field)
            for query_id in matched_ids
        )
        for field in ("gold_answers", "meta", "query")
    }
    if any(input_mismatches.values()):
        raise ValueError(
            f"candidate and reference paired inputs differ: {input_mismatches}"
        )

    transition_query_ids: dict[str, list[str]] = {
        name: [] for name in VERDICT_TRANSITIONS
    }
    for query_id in matched_ids:
        candidate_correct = candidate_by_id[query_id].get("correct") is True
        reference_correct = reference_by_id[query_id].get("correct") is True
        if candidate_correct and reference_correct:
            transition = "both_pass"
        elif candidate_correct:
            transition = "reference_fail_candidate_pass"
        elif reference_correct:
            transition = "reference_pass_candidate_fail"
        else:
            transition = "both_fail"
        transition_query_ids[transition].append(query_id)
    transitions = {
        name: len(query_ids) for name, query_ids in transition_query_ids.items()
    }

    candidate_categories = {
        row["name"]: row for row in summarize_result(candidate)["categories"]
    }
    reference_categories = {
        row["name"]: row for row in summarize_result(reference)["categories"]
    }
    category_deltas = []
    for name in sorted(candidate_categories.keys() & reference_categories.keys()):
        candidate_category = candidate_categories[name]
        reference_category = reference_categories[name]
        if candidate_category["total"] != reference_category["total"]:
            continue
        category_deltas.append(
            {
                "candidate_accuracy": candidate_category["accuracy"],
                "candidate_correct": candidate_category["correct"],
                "delta": candidate_category["accuracy"]
                - reference_category["accuracy"],
                "name": name,
                "reference_accuracy": reference_category["accuracy"],
                "reference_correct": reference_category["correct"],
                "total": candidate_category["total"],
            }
        )

    return {
        "accuracy_delta": candidate["accuracy"] - reference["accuracy"],
        "candidate_summary": summarize_result(candidate),
        "candidate_only_query_ids": candidate_only_ids,
        "category_deltas": category_deltas,
        "input_mismatches": input_mismatches,
        "matched_queries": len(matched_ids),
        "reference_summary": summarize_result(reference),
        "reference_only_query_ids": reference_only_ids,
        "sample_errors": _select_error_sample(
            candidate_by_id, reference_by_id, sample_errors
        ),
        "verdict_transition_query_ids": transition_query_ids,
        "verdict_transitions": transitions,
    }


def _catalog_for_result(data: dict[str, Any]) -> dict[str, Any]:
    dataset = str(data.get("dataset") or "unknown")
    split = str(data.get("split") or "unknown")
    provider = str(data.get("memory_provider") or data.get("run_name") or "unknown")
    mode = str(data.get("mode") or "unknown")
    return {
        "datasets": {
            dataset: {
                "description": data.get("description") or "Local AMB result",
                "task": "evaluated",
                "splits": [split],
            }
        },
        "providers": {
            provider: {
                "key": provider,
                "description": "Local AMB result",
                "kind": "local",
                "link": None,
                "logo": None,
            }
        },
        "modes": {mode: {"description": "Local AMB result mode"}},
    }


def _report_handler(
    result_path: Path, catalog: dict[str, Any], ui_directory: Path
) -> type[BaseHTTPRequestHandler]:
    class ReportRequestHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def _send_json(self, payload: Any) -> None:
            body = json.dumps(payload).encode()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_file(
            self,
            file_path: Path,
            content_type: str | None = None,
            content_encoding: str | None = None,
        ) -> None:
            if not file_path.is_file():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self.send_response(HTTPStatus.OK)
            self.send_header(
                "Content-Type",
                content_type
                or mimetypes.guess_type(file_path.name)[0]
                or "application/octet-stream",
            )
            if content_encoding:
                self.send_header("Content-Encoding", content_encoding)
            self.send_header("Content-Length", str(file_path.stat().st_size))
            self.end_headers()
            try:
                with file_path.open("rb") as handle:
                    while chunk := handle.read(1024 * 1024):
                        self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                return

        def do_GET(self) -> None:
            request = urlparse(self.path)
            if request.path == "/api/run-url":
                requested_file = parse_qs(request.query).get("file", [None])[0]
                if requested_file != REPORT_FILE_ID:
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                self._send_json({"url": REPORT_DATA_ROUTE})
                return
            if request.path == "/api/catalog":
                self._send_json(catalog)
                return
            if request.path == REPORT_DATA_ROUTE:
                self._send_file(
                    result_path,
                    content_type="application/json",
                    content_encoding="gzip" if result_path.suffix == ".gz" else None,
                )
                return
            if request.path.startswith("/assets/"):
                relative_asset = request.path.removeprefix("/")
                asset_path = (ui_directory / relative_asset).resolve()
                try:
                    asset_path.relative_to(ui_directory.resolve())
                except ValueError:
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                self._send_file(asset_path)
                return
            if request.path in {"/favicon.ico", "/favicon-32.png", "/favicon-180.png"}:
                self._send_file(ui_directory / request.path.removeprefix("/"))
                return
            if request.path == "/" or request.path.startswith("/run/"):
                self._send_file(
                    ui_directory / "index.html", content_type="text/html; charset=utf-8"
                )
                return
            self.send_error(HTTPStatus.NOT_FOUND)

    return ReportRequestHandler


def serve_result(
    result_path: Path,
    host: str,
    port: int,
    open_browser: bool,
    initial_query_id: str | None,
) -> None:
    result_path = result_path.resolve()
    result = load_amb_result(result_path)
    catalog = _catalog_for_result(result)
    del result

    handler = _report_handler(result_path, catalog, UI_DIRECTORY)
    server = ThreadingHTTPServer((host, port), handler)
    actual_host, actual_port = server.server_address[:2]
    origin = f"http://{actual_host}:{actual_port}"
    report_url = f"{origin}/run/{quote(REPORT_FILE_ID, safe='')}"
    if initial_query_id:
        report_url += f"?id={quote(initial_query_id, safe='')}"
    print(f"Serving {origin}/", flush=True)
    print(f"Report  {report_url}", flush=True)
    if open_browser:
        webbrowser.open(report_url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser(
        "inspect", help="Validate and summarize an AMB result"
    )
    inspect_parser.add_argument("result", type=Path)
    inspect_parser.add_argument(
        "--json", action="store_true", help="Print machine-readable JSON"
    )

    compare_parser = subparsers.add_parser(
        "compare", help="Compare candidate and reference AMB results"
    )
    compare_parser.add_argument("candidate", type=Path)
    compare_parser.add_argument("reference", type=Path)
    compare_parser.add_argument("--sample-errors", type=int, default=0)
    compare_parser.add_argument(
        "--json", action="store_true", help="Print machine-readable JSON"
    )

    serve_parser = subparsers.add_parser(
        "serve", help="Serve the official AMB report UI locally"
    )
    serve_parser.add_argument("result", type=Path)
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=7979)
    serve_parser.add_argument("--id", dest="query_id")
    serve_parser.add_argument("--no-open", action="store_true")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    if args.command == "inspect":
        summary = summarize_result(load_amb_result(args.result))
        if args.json:
            print(json.dumps(summary, indent=2, sort_keys=True))
        else:
            print(
                f"{summary['dataset']}/{summary['split']} {summary['run_name']}: "
                f"{summary['correct']}/{summary['total_queries']} "
                f"({summary['accuracy']:.1%})"
            )
        return 0
    if args.command == "serve":
        serve_result(
            args.result,
            host=args.host,
            port=args.port,
            open_browser=not args.no_open,
            initial_query_id=args.query_id,
        )
        return 0
    if args.command == "compare":
        comparison = compare_results(
            load_amb_result(args.candidate),
            load_amb_result(args.reference),
            sample_errors=args.sample_errors,
        )
        if args.json:
            print(json.dumps(comparison, indent=2, sort_keys=True))
        else:
            print(
                f"matched={comparison['matched_queries']} "
                f"accuracy_delta={comparison['accuracy_delta']:+.1%} "
                f"regressions={comparison['verdict_transitions']['reference_pass_candidate_fail']} "
                f"improvements={comparison['verdict_transitions']['reference_fail_candidate_pass']}"
            )
        return 0
    raise AssertionError(f"Unhandled command: {args.command}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(2) from None
