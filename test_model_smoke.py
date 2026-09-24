import json
from pathlib import Path
import tempfile
import unittest
from urllib.request import Request

from configure_model import MODELS, save_new
from model_smoke import NoRedirect, load_config, run


class ModelSmokeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'private'
        self.config = self.root / 'credentials.json'
        self.ledger = self.root / 'smoke.jsonl'
        self.key = 'sk-fake-test-value-not-real-credential'
        self.data = {'api_key': self.key, 'base_url': 'https://dashscope.aliyuncs.com/compatible-mode/v1',
                     'models': MODELS, 'free_only_user_confirmed': True, 'paid_calls_authorized': False}
        save_new(self.config, self.data)

    def tearDown(self):
        self.temp.cleanup()

    def success(self, config, body):
        self.assertNotIn('api_key', body)
        events = [json.loads(s) for s in self.ledger.read_text().splitlines()]
        self.assertEqual(events[-1]['event'], 'started')
        self.assertEqual(body['max_tokens'], 64)
        self.assertFalse(body['enable_thinking'])
        return {'choices': [{'message': {'content': '连接成功'}, 'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': 15, 'completion_tokens': 3, 'total_tokens': 18}}

    def test_two_calls_recorded_and_never_repeated(self):
        result = run(self.config, self.ledger, self.success)
        self.assertEqual(len(result), 2)
        self.assertTrue(all(r['status'] == 'ok' for r in result))
        self.assertNotIn(self.key, self.ledger.read_text())
        self.assertEqual(run(self.config, self.ledger, self.success), [])

    def test_missing_usage_stops_next_call_and_future_runs(self):
        result = run(self.config, self.ledger, lambda *args: {'choices': [
            {'message': {'content': 'ok'}, 'finish_reason': 'stop'}]})
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['error'], 'usage_unknown')
        with self.assertRaises(ValueError):
            run(self.config, self.ledger, self.success)

    def test_exception_does_not_leak_key_or_retry(self):
        def fail(*args):
            raise RuntimeError(self.key)
        result = run(self.config, self.ledger, fail)
        self.assertEqual(len(result), 1)
        self.assertNotIn(self.key, self.ledger.read_text())

    def test_no_free_protection_no_network(self):
        self.data['free_only_user_confirmed'] = False
        self.config.write_text(json.dumps(self.data))
        with self.assertRaises(ValueError):
            run(self.config, self.ledger, lambda *args: self.fail('network attempted'))
        self.assertFalse(self.ledger.exists())

    def test_only_official_beijing_endpoint(self):
        for endpoint in ('https://evil.example/compatible-mode/v1',
                         'https://dashscope.aliyuncs.com.evil.example/compatible-mode/v1',
                         'http://dashscope.aliyuncs.com/compatible-mode/v1'):
            self.data['base_url'] = endpoint
            self.config.write_text(json.dumps(self.data))
            with self.assertRaises(ValueError):
                load_config(self.config)

    def test_redirects_disabled(self):
        request = Request(self.data['base_url'], headers={'Authorization': 'Bearer ' + self.key})
        self.assertIsNone(NoRedirect().redirect_request(request, None, 307, '', {}, 'https://evil.example'))


if __name__ == '__main__':
    unittest.main()
