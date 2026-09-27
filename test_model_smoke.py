import json
import io
from pathlib import Path
import tempfile
import unittest
from urllib.request import Request

from configure_model import MODELS, save_new
from model_smoke import NoRedirect, load_config, run, read_event_stream, transport


class StreamTests(unittest.TestCase):
    def chunks(self):
        return [{'choices':[{'index':0,'delta':{'content':t},'finish_reason':None}]} for t in ('测','试')] + [
            {'choices':[{'index':0,'delta':{},'finish_reason':'stop'}]},
            {'choices':[],'usage':{'prompt_tokens':10,'completion_tokens':2,'total_tokens':12}}]

    def read(self,chunks,done=True,limit=10000,audit=None):
        raw=''.join('data: '+json.dumps(c,ensure_ascii=False)+'\r\n\r\n' for c in chunks)
        if done:raw+='data: [DONE]\r\n\r\n'
        response=io.BytesIO(raw.encode());response.status=200
        return read_event_stream(response,limit,5,audit)

    def test_complete_stream_reconstructs_content_and_usage_without_audit_text(self):
        audit=[];result=self.read(self.chunks(),audit=audit.append)
        self.assertEqual(result['choices'][0]['message']['content'],'测试')
        self.assertEqual(result['usage']['total_tokens'],12)
        self.assertTrue(audit[-1]['stream_complete'])
        self.assertNotIn('测试',json.dumps(audit,ensure_ascii=False))

    def test_thinking_stream_counts_usage_without_rendering_reasoning(self):
        chunks=self.chunks()
        chunks.insert(0,{'choices':[{'index':0,'delta':{'reasoning_content':'private reasoning'}}]})
        chunks[-1]['usage'].update(prompt_tokens=10,completion_tokens=1002,total_tokens=1012,
                                  completion_tokens_details={'reasoning_tokens':1000})
        result=self.read(chunks)
        self.assertEqual(result['choices'][0]['message']['content'],'测试')
        self.assertNotIn('private reasoning',json.dumps(result))
        self.assertEqual(result['usage']['completion_tokens_details']['reasoning_tokens'],1000)

    def test_partial_missing_usage_or_finish_and_duplicate_usage_fail_closed(self):
        chunks=self.chunks()
        for events,done in [(chunks,False),(chunks[:-1],True),(chunks[:2]+chunks[3:],True),(chunks+[chunks[-1]],True)]:
            with self.subTest(events=events,done=done),self.assertRaises(ValueError):self.read(events,done)

    def test_size_tools_malformed_and_content_after_finish_rejected(self):
        with self.assertRaises(ValueError):self.read(self.chunks(),limit=20)
        for chunk in [None,{'choices':[{'index':0,'delta':{'tool_calls':[{}]}}]},
                      {'choices':[{'index':1,'delta':{}}]}]:
            with self.assertRaises(ValueError):self.read([chunk])
        chunks=self.chunks();chunks.insert(3,chunks[0])
        with self.assertRaises(ValueError):self.read(chunks)

    def test_real_local_http_disconnect_truncated_stream_and_complete_response(self):
        from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
        from http.client import RemoteDisconnected
        import socket,threading
        calls=[];chunks=self.chunks()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']));calls.append(self.path)
                if self.path.startswith('/disconnect/'):
                    self.connection.shutdown(socket.SHUT_RDWR);self.connection.close();return
                raw=''.join('data: '+json.dumps(c)+'\n\n' for c in chunks)
                if self.path.startswith('/complete/'):raw+='data: [DONE]\n\n'
                raw=raw.encode();self.send_response(200);self.send_header('Content-Type','text/event-stream')
                self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        try:
            for index,route in enumerate(('disconnect','partial','complete'),1):
                config={'base_url':f'http://127.0.0.1:{server.server_port}/{route}','api_key':'fake-local-test-only'}
                if route=='complete':
                    self.assertEqual(transport(config,{'stream':True},timeout=2)['choices'][0]['message']['content'],'测试')
                else:
                    with self.assertRaises(RemoteDisconnected if route=='disconnect' else ValueError):transport(config,{'stream':True},timeout=2)
                self.assertEqual(len(calls),index)  # 每次失败只发一次，没有隐式重发。
        finally:server.shutdown();server.server_close();worker.join(timeout=2)


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
