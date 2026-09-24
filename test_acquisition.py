import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from urllib.error import HTTPError, URLError
from collect_page import collect
from bundle_evidence import bundle
from loop import initialize, save

GOOD = 'Title: Example\nMarkdown Content:\n正常资料，讨论 404 和登录问题并不代表这是错误页面。'

class AcquisitionTests(unittest.TestCase):
    def run_case(self, effects, retries=1):
        reader = Mock(side_effect=effects)
        result = collect('https://example.com', retries, reader, lambda _: None)
        return result, reader.call_count

    def test_timeout_then_success(self):
        result, calls = self.run_case([TimeoutError(), GOOD])
        self.assertEqual((result['status'], calls), ('fetched_unverified', 2))
        self.assertIsNone(result['error'])

    def test_timeout_limit(self):
        result, calls = self.run_case([TimeoutError()] * 3)
        self.assertEqual(calls, 2)
        self.assertEqual(result['error']['kind'], 'timeout')
        self.assertEqual(result['content'], '')
        self.assertNotIn('content_sha256', result)

    def test_http_codes(self):
        for code, kind, calls in [(404,'not_found',1),(410,'not_found',1),(401,'access_required',1),
                                  (403,'access_required',1),(429,'rate_limited',1),(503,'service_error',2)]:
            with self.subTest(code=code):
                result, count = self.run_case([HTTPError('https://example.com',code,'error',{},None)]*2)
                self.assertEqual((result['error']['kind'], count), (kind,calls))

    def test_wrapped_target_error(self):
        result, calls = self.run_case(['Title: Error\nWarning: Target URL returned error 404: Not Found\nMarkdown Content:\nmissing'])
        self.assertEqual((result['error']['kind'], calls), ('not_found', 1))

    def test_login_body(self):
        result, calls = self.run_case(['Title: Sign in\nMarkdown Content:\nPassword'])
        self.assertEqual((result['status'], calls), ('needs_user', 1))
        self.assertEqual(result['content'], '')

    def test_network_error_not_platform_judgment(self):
        result, calls = self.run_case([URLError('DNS failure')])
        self.assertEqual((result['error']['kind'],calls), ('network_error',1))

    def test_empty_and_unknown(self):
        for response, kind in [('', 'empty'), (RuntimeError('secret'), 'unexpected_error')]:
            result, _ = self.run_case([response])
            self.assertEqual(result['error']['kind'],kind)
            self.assertNotIn('secret', json.dumps(result))

    def test_failure_cannot_enter_analysis(self):
        record, _ = self.run_case([TimeoutError()]*2)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            save(root/'failed.json',record)
            (root/'draft.md').write_text('不得采信此稿')
            with self.assertRaises(ValueError): bundle([root/'failed.json'])
            with self.assertRaises(ValueError): initialize(root/'run',root/'draft.md',root/'failed.json')

if __name__ == '__main__': unittest.main()
