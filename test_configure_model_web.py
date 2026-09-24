"""本机配置入口安全行为；仅使用虚构密钥和临时目录。"""
import contextlib
import http.client
import io
import json
from pathlib import Path
import stat
import tempfile
import threading
import unittest
from urllib.parse import urlencode

from configure_model_web import make_server


class ConfigPageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'private' / 'credentials.json'
        self.server = make_server(self.path)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.key = 'sk-fictional-unit-test-not-an-api-key'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join()
        self.temp.cleanup()

    def request(self, method, path=None, fields=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        body = None if fields is None else urlencode(fields)
        base = {'Origin': self.server.origin, 'Content-Type': 'application/x-www-form-urlencoded'}
        base.update(headers or {})
        conn.request(method, path or self.server.form_path, body, base)
        res = conn.getresponse()
        result = (res.status, dict(res.getheaders()), res.read().decode())
        conn.close()
        return result

    def fields(self):
        return {'csrf': self.server.csrf, 'api_key': self.key, 'workspace': '', 'free_only': 'yes'}

    def test_page_private_headers_and_hidden_key(self):
        code, headers, body = self.request('GET')
        self.assertEqual(code, 200)
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(headers['Referrer-Policy'], 'same-origin')
        self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])
        self.assertIn('type="password"', body)
        self.assertNotIn(self.key, body)

    def test_save_and_never_echo_or_overwrite(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = self.request('POST', '/save', self.fields())
        self.assertEqual(result[0], 303)
        self.assertEqual(json.loads(self.path.read_text())['api_key'], self.key)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.path.parent.stat().st_mode), 0o700)
        self.assertNotIn(self.key, output.getvalue())
        self.assertNotIn(self.key, self.request('GET', '/saved')[2])
        self.assertEqual(self.request('POST', '/save', self.fields())[0], 409)

    def test_cross_site_and_host_rejected(self):
        for headers in ({'Origin': 'https://other.example'}, {'Origin': 'null'}, {'Host': 'other.example'},
                        {'Sec-Fetch-Site': 'cross-site'}):
            self.assertEqual(self.request('POST', '/save', self.fields(), headers)[0], 403)
        self.assertFalse(self.path.exists())

    def test_bad_token_and_no_protection_confirmation_rejected(self):
        for name, value in [('csrf', 'bad'), ('free_only', 'no'), ('workspace', 'https://other.example')]:
            fields = self.fields()
            fields[name] = value
            self.assertEqual(self.request('POST', '/save', fields)[0], 400)
        self.assertFalse(self.path.exists())

    def test_existing_credentials_preserved(self):
        self.path.parent.mkdir()
        self.path.write_text('existing')
        self.assertEqual(self.request('GET')[0], 409)
        self.assertEqual(self.request('POST', '/save', self.fields())[0], 400)
        self.assertEqual(self.path.read_text(), 'existing')

    def test_expired_session_rejected(self):
        self.server.deadline = 0
        self.assertEqual(self.request('GET')[0], 403)
        self.assertEqual(self.request('POST', '/save', self.fields())[0], 403)
        self.assertFalse(self.path.exists())


if __name__ == '__main__':
    unittest.main()
