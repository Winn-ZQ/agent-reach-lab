"""新增小红书网页分支，平台与模型网络均用测试响应替代。"""
import http.client
import json
import threading
import time
import unittest
from unittest.mock import Mock

from collect_xhs import verify_package
from research_flow import digest
from research_live import LiveStore
from research_web import make_server
import test_analyze_xhs as fixtures


class XhsWebTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.SavedXhsTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.web_search = Mock(side_effect=AssertionError('must not search the web'))
        self.store = LiveStore(self.f.root/'live',search=self.web_search,
            backend_factory=self.f.factory,budget=self.f.budget,xhs_enabled=True,xhs_client=self.f.client)
        self.sample = self.store.register_xhs_sample(self.f.source,'虚构已保存样本')
        self.f.acquisition_calls.clear()

    def payload(self, key='a', **changes):
        return {'question':'虚构 App 使用体验','region':'','period':'','sources':['xhs'],
                'required_sources':['xhs'],'request_id':key*32,'parent_task_id':None,**changes}

    def wait(self, task):
        end=time.monotonic()+4
        while self.store.active and time.monotonic()<end:time.sleep(.01)
        self.assertIsNone(self.store.active)
        return self.store.detail(task['id'])

    def test_saved_sample_http_result_exports_and_operation_status(self):
        from unittest.mock import patch
        with patch('research_web.LiveStore',return_value=self.store):
            server=make_server(self.f.root/'http-runs',0)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(lambda:(server.shutdown(),server.server_close(),thread.join(1)))
        conn=http.client.HTTPConnection(server.host);self.addCleanup(conn.close)
        h={'X-Session-Token':server.session}
        conn.request('GET','/api/live/bootstrap',headers=h);r=conn.getresponse();boot=json.loads(r.read())
        xhs=next(c for c in boot['capabilities'] if c['id']=='xhs')
        self.assertTrue(xhs['executable'])
        self.assertEqual(xhs['operations'][-1]['status'],'not_connected')
        self.assertNotIn('path',boot['xhs_samples'][0])
        headers={**h,'Origin':server.origin,'X-CSRF-Token':boot['csrf'],'Content-Type':'application/json'}
        body=self.payload(xhs_sample_id=self.sample)
        conn.request('POST','/api/live/tasks',json.dumps(body),headers)
        r=conn.getresponse();self.assertEqual(r.status,202);task=json.loads(r.read())
        result=self.wait(task)
        self.assertEqual(result['state']['review_status'],'passed')
        self.assertEqual(set(result['case']['questions']),{'Q1'})
        self.assertEqual(self.f.acquisition_calls,[])
        for file in ['report.md','materials.csv']:
            conn.request('GET',f'/api/live/tasks/{task["id"]}/{file}',headers=h)
            r=conn.getresponse();text=r.read().decode()
            self.assertEqual(r.status,200)
            self.assertIn('小红书',text);self.assertNotIn('fixture-access-only',text)
            self.assertNotIn(str(self.f.root),text)
        conn.request('POST','/api/live/tasks',json.dumps(body),headers)
        r=conn.getresponse();self.assertEqual(json.loads(r.read())['id'],task['id'])
        self.assertEqual(len(self.f.sent),2)

    def test_fresh_collection_and_quota_resume_reuse_evidence(self):
        self.f.budget.observe({'remaining_tokens':{'qwen3.8-flash':100,'deepseek-v4.1-flash':100},'stop_when_used_up':True})
        task=self.store.start(self.payload());result=self.wait(task)
        self.assertEqual(result['task']['reason'],'free_quota_insufficient')
        self.assertEqual(self.f.acquisition_calls.count('search_feeds'),2)
        before=list(self.f.acquisition_calls)
        sha=digest(result['case'])
        self.f.budget.observe({'remaining_tokens':{'qwen3.8-flash':1000000,'deepseek-v4.1-flash':1000000},'stop_when_used_up':True})
        result=self.wait(self.store.resume(task['id']))
        self.assertEqual(result['state']['review_status'],'passed')
        self.assertEqual(digest(result['case']),sha)
        self.assertEqual(self.f.acquisition_calls,before)
        self.web_search.assert_not_called()

    def test_login_resume_preserves_failed_attempt(self):
        original=self.store.xhs_client
        self.store.xhs_client=lambda n,a:{'content':[{'type':'text','text':'未登录'}]}
        task=self.store.start(self.payload());result=self.wait(task)
        self.assertEqual(result['task']['reason'],'xhs_login_required')
        from research_exports import export_markdown
        self.assertIn('小红书需要本机登录',export_markdown(result))
        self.assertNotIn('真实网页研究任务',export_markdown(result))
        self.assertFalse(self.f.sent)
        failed=self.store.directory/task['id']/'xhs-attempt-1/status.json'
        before=failed.read_bytes()
        self.store.xhs_client=original
        result=self.wait(self.store.resume(task['id']))
        self.assertEqual(result['state']['review_status'],'passed')
        self.assertEqual(failed.read_bytes(),before)
        self.assertTrue((failed.parent.parent/'xhs-attempt-2').is_dir())

    def test_unsupported_scope_and_unknown_saved_id_never_call(self):
        for key,changes,reason in [('a',{'sources':['web','xhs']},'mixed_sources_not_supported'),
            ('b',{'period':'最近一个月'},'xhs_scope_not_supported'),
            ('c',{'question':'只看评论'},'xhs_scope_not_supported')]:
            result=self.wait(self.store.start(self.payload(key,**changes)))
            self.assertEqual(result['task']['reason'],reason)
        with self.assertRaises(ValueError):self.store.start(self.payload('d',xhs_sample_id='/tmp/private'))
        self.assertFalse(self.f.sent);self.assertFalse(self.f.acquisition_calls)

    def test_tampered_registered_evidence_stops_before_model(self):
        path=self.f.source/'source-1.json'
        path.write_text(path.read_text().replace('训练提醒','篡改提醒'))
        result=self.wait(self.store.start(self.payload(xhs_sample_id=self.sample)))
        self.assertEqual(result['task']['reason'],'evidence_or_response_invalid')
        self.assertFalse(self.f.sent);self.assertFalse(self.f.acquisition_calls)

    def test_citation_retry_copies_xhs_snapshot_without_recollection(self):
        original=self.f.send
        broken=True
        def send(config,body):
            response=original(config,body)
            value=json.loads(response['choices'][0]['message']['content'])
            if broken and 'answers' in value:
                for answer in value['answers']:answer['refs']=[]
                response['choices'][0]['message']['content']=json.dumps(value)
            return response
        self.f.send=send
        first=self.wait(self.store.start(self.payload(xhs_sample_id=self.sample)))
        self.assertIn(first['task']['reason'],('format_revision_limit','no_change'))
        path=self.store.directory/first['task']['id']/'flow/state.json'
        before=path.read_bytes()
        broken=False
        child=self.wait(self.store.retry_citations(first['task']['id'],'b'*32))
        self.assertEqual(child['state']['review_status'],'passed')
        self.assertEqual(digest(first['case']),digest(child['case']))
        self.assertEqual(path.read_bytes(),before)
        self.assertFalse(self.f.acquisition_calls)

    def test_cancel_search_does_not_start_reading(self):
        entered,release=threading.Event(),threading.Event()
        original=self.store.xhs_client
        def client(n,a):
            if n=='search_feeds':entered.set();release.wait(2)
            return original(n,a)
        self.store.xhs_client=client
        task=self.store.start(self.payload());self.assertTrue(entered.wait(2))
        self.store.cancel(task['id']);release.set();result=self.wait(task)
        self.assertEqual(result['task']['status'],'cancelled')
        self.assertNotIn('get_feed_detail',self.f.acquisition_calls)
        self.assertFalse(self.f.sent)


if __name__=='__main__':unittest.main()
