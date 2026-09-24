import json
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import Mock

from research_acquisition import SearchError, collect_candidates, parse_search_response, search_public_web


PAYLOAD = {
    "content": [{"type": "text", "text": """Title: Official A
URL: https://example.com/a
Published: 2026-01-01
Author: Team A
Highlights:
first highlight
...

---

Title: Official A duplicate
URL: https://example.com/a
Published: N/A
Author: N/A
Highlights:
duplicate

Title: Official B
URL: http://example.org/b
Published: N/A
Author: Team B
Highlights:
second highlight"""}]
}


class AcquisitionAdapterTests(unittest.TestCase):
    def test_parse_deduplicates_and_keeps_metadata(self):
        results = parse_search_response(PAYLOAD, 3)
        self.assertEqual([item["url"] for item in results], ["https://example.com/a", "http://example.org/b"])
        self.assertEqual(results[0]["title"], "Official A")
        self.assertIn("first highlight", results[0]["highlights"])

    def test_parse_rejects_missing_candidates(self):
        with self.assertRaises(SearchError) as context:
            parse_search_response({"content": [{"type": "text", "text": "no URL here"}]})
        self.assertEqual(context.exception.kind, "no_results")

    def test_search_uses_argument_list_and_no_shell(self):
        runner = Mock(return_value=CompletedProcess([], 0, json.dumps(PAYLOAD), ""))
        result = search_public_web("elevator; touch /tmp/no", "find official pages",
                                  runner=runner, root=Path.cwd())
        args, kwargs = runner.call_args
        self.assertFalse(kwargs["shell"])
        self.assertIn("elevator; touch /tmp/no", args[0][args[0].index("--args") + 1])
        self.assertIsInstance(args[0], list)
        self.assertEqual(result["results"][0]["url"], "https://example.com/a")

    def test_search_failure_does_not_expose_stderr(self):
        runner = Mock(return_value=CompletedProcess([], 1, "", "secret-token=abc"))
        with self.assertRaises(SearchError) as context:
            search_public_web("question", "objective", runner=runner, root=Path.cwd())
        self.assertEqual(context.exception.kind, "search_failed")
        self.assertNotIn("secret-token", str(context.exception))

    def test_collect_candidates_writes_manifest_and_records(self):
        collector = Mock(side_effect=lambda url, max_retries: {
            "url": url, "status": "fetched_unverified", "error": None,
            "fetched_at": "2026-01-01T00:00:00+00:00", "content_sha256": "abc"})
        search = {"status": "searched", "backend": "Exa via mcporter", "query": "q",
                  "objective": "o", "searched_at": "now", "results": parse_search_response(PAYLOAD)}
        with tempfile.TemporaryDirectory() as tmp:
            manifest = collect_candidates(search, Path(tmp) / "sources", collector=collector)
            self.assertEqual(manifest["status"], "collected")
            self.assertEqual(collector.call_count, 2)
            self.assertTrue((Path(tmp) / "sources" / "source-1.json").exists())
            saved = json.loads((Path(tmp) / "sources" / "manifest.json").read_text())
            self.assertEqual(saved["records"][0]["search_rank"], 1)

    def test_collect_does_not_overwrite_output(self):
        search = {"status": "searched", "results": [{"url": "https://example.com"}]}
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "sources"
            target.mkdir()
            with self.assertRaises(FileExistsError):
                collect_candidates(search, target, collector=Mock())


if __name__ == "__main__":
    unittest.main()
