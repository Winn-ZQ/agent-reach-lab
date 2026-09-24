import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from urllib.parse import urlsplit
from unittest.mock import patch

from research_web import make_server


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.server = make_server(Path(self.temp.name)/'runs', 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.base = f'http://{self.server.host}'
        c = http.client.HTTPConnection(self.server.host)
        c.request('GET', self.server.entry)
        r = c.getresponse(); self.assertEqual(r.status, 303)
        self.cookie = r.getheader('Set-Cookie').split(';',1)[0]
        self.session_token = self.server.session
        self.session = c
        self.headers = {'Cookie':self.cookie}

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(1); self.temp.cleanup()

    def get(self,path):
        self.session.request('GET',path,headers=self.headers); r=self.session.getresponse(); body=r.read(); return r,body

    def post(self,path,data,csrf,origin=True,token=True):
        h={**self.headers,'Content-Type':'application/json'}
        if origin:h['Origin']=self.base
        if token:h['X-CSRF-Token']=csrf
        self.session.request('POST',path,body=json.dumps(data),headers=h); r=self.session.getresponse(); return r,json.loads(r.read())

    def bootstrap(self):
        r,b=self.get('/api/bootstrap'); self.assertEqual(r.status,200); return json.loads(b)

    def test_entry_bootstrap_options_and_security_headers(self):
        r,b=self.get('/'); self.assertEqual(r.status,200); self.assertIn('资料助手',b.decode())
        self.assertEqual(r.getheader('Content-Security-Policy').split(';')[0],"default-src 'none'")
        boot=self.bootstrap(); self.assertFalse(boot['live_execution_enabled']); self.assertEqual(len(boot['scenarios']),4)
        self.assertEqual(boot['scenarios'][0]['sampling_facts']['known_unique_authors'],3)

    def test_entry_query_token_can_authorize_when_cookie_is_not_persisted(self):
        c=http.client.HTTPConnection(self.server.host)
        c.request('GET','/?session='+self.session_token)
        r=c.getresponse(); body=r.read().decode(); self.assertEqual(r.status,200); self.assertIn('data-session',body)
        c.request('GET','/api/bootstrap',headers={'X-Session-Token':self.session_token})
        r=c.getresponse(); self.assertEqual(r.status,200); self.assertIn('scenarios',r.read().decode())

    def test_post_requires_origin_csrf_and_json(self):
        boot=self.bootstrap(); data={'scenario_id':'quote-repair','request_id':'e'*32}
        r,b=self.post('/api/tasks',data,boot['csrf'],origin=False); self.assertEqual(r.status,403)
        r,b=self.post('/api/tasks',data,'bad'); self.assertEqual(r.status,403)
        h={**self.headers,'Origin':self.base,'X-CSRF-Token':boot['csrf'],'Content-Type':'text/plain'}
        self.session.request('POST','/api/tasks',body='x',headers=h); r=self.session.getresponse(); r.read(); self.assertEqual(r.status,403)

    def test_task_idempotency_busy_and_result_exports(self):
        boot=self.bootstrap(); data={'scenario_id':'quote-repair','request_id':'f'*32}
        r,task=self.post('/api/tasks',data,boot['csrf']); self.assertEqual(r.status,202)
        r,same=self.post('/api/tasks',data,boot['csrf']); self.assertEqual(r.status,202); self.assertEqual(same['id'],task['id'])
        # Worker is offline and should finish quickly.
        deadline=time.time()+3
        detail=None
        while time.time()<deadline:
            r,b=self.get('/api/tasks/'+task['id']); detail=json.loads(b)
            if detail['task']['status']!='running': break
            time.sleep(.02)
        self.assertEqual(detail['task']['status'],'completed'); self.assertEqual(detail['state']['review_status'],'passed')
        r,b=self.get('/api/tasks/'+task['id']+'/report.md'); self.assertEqual(r.status,200); self.assertIn('离线回放',b.decode())
        r,b=self.get('/api/tasks/'+task['id']+'/materials.csv'); self.assertEqual(r.status,200); self.assertIn('disclosure',b.decode())
        r,b=self.get('/api/tasks/'+'0'*32); self.assertEqual(r.status,404)

    def test_one_active_task_rejects_second_and_preflight_is_read_only(self):
        boot=self.bootstrap(); first={'scenario_id':'budget-stop','request_id':'a'*32}
        r,task=self.post('/api/tasks',first,boot['csrf']); self.assertEqual(r.status,202)
        # The worker usually completes immediately; idempotency remains the safe retry path.
        second={'scenario_id':'revision-limit','request_id':'b'*32}
        r,b=self.post('/api/tasks',second,boot['csrf'])
        self.assertIn(r.status,(202,409))
        r,result=self.post('/api/preflight',{'scenario_id':'quote-repair'},boot['csrf']); self.assertEqual(r.status,200)
        self.assertEqual(result['live_execution_enabled'],False); self.assertEqual(result['api_calls'],0)

    def test_plan_preview_returns_responsibilities_without_execution(self):
        boot=self.bootstrap()
        payload={'question':'比较三款跑步 App 的训练计划入口和反馈','region':'中国大陆','period':'2026-01 至 2026-06','sources':['web']}
        r,result=self.post('/api/plan-preview',payload,boot['csrf']); self.assertEqual(r.status,200)
        self.assertEqual((result['mode'],result['execution_status'],result['real_api_calls']),('offline_plan_preview','not_started',0))
        self.assertEqual([s['owner'] for s in result['steps']],['程序','Agent-Reach 获取层','分析角色','独立复核角色','程序'])
        self.assertIn('小红书',result['not_connected'])
        bad={**payload,'sources':['xhs']}; r,result=self.post('/api/plan-preview',bad,boot['csrf']); self.assertEqual(r.status,400)
        long={**payload,'question':'x'*601}; r,result=self.post('/api/plan-preview',long,boot['csrf']); self.assertEqual(r.status,400)

    @patch('research_web.collect_candidates')
    @patch('research_web.search_public_web')
    def test_confirmed_plan_runs_public_acquisition_without_model(self, search, collect):
        search.return_value={'status':'searched','backend':'Exa via mcporter','query':'q',
                             'objective':'o','searched_at':'now','results':[
                                 {'rank':1,'title':'Official page','url':'https://example.com/a',
                                  'published':None,'author':None,'highlights':'h'}]}
        collect.return_value={'status':'collected','records':[{'search_rank':1,'status':'fetched_unverified',
                            'url':'https://example.com/a','error':None}]}
        boot=self.bootstrap()
        payload={'question':'电梯预测性维保有哪些公开资料？','region':'中国大陆','period':'2026','sources':['web'],
                 'request_id':'c'*32}
        r,run=self.post('/api/acquisitions',payload,boot['csrf']); self.assertEqual(r.status,202)
        deadline=time.time()+3; detail=None
        while time.time()<deadline:
            r,b=self.get('/api/acquisitions/'+run['id']); detail=json.loads(b)
            if detail['run']['status']!='running': break
            time.sleep(.02)
        self.assertEqual(detail['run']['status'],'completed')
        self.assertEqual(detail['candidates'][0]['url'],'https://example.com/a')
        search.assert_called_once(); collect.assert_called_once()
        self.assertEqual(detail['run']['mode'],'public_web_acquisition')

    def test_acquisition_requires_explicit_request_fields(self):
        boot=self.bootstrap(); payload={'question':'q','region':'','period':'','sources':['web']}
        r,result=self.post('/api/acquisitions',payload,boot['csrf']); self.assertEqual(r.status,404)

    def test_host_and_cookie_isolation(self):
        c=http.client.HTTPConnection(self.server.host); c.request('GET','/api/bootstrap'); r=c.getresponse(); r.read(); self.assertEqual(r.status,403)
        self.session.request('GET','/api/bootstrap',headers={'Cookie':'research_session=wrong'}); r=self.session.getresponse(); r.read(); self.assertEqual(r.status,403)


if __name__=='__main__':unittest.main()
