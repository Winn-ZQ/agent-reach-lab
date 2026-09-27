"""自动网页流程的集成测试。所有外部搜索、正文及模型响应均为测试替身。"""
from datetime import datetime, timedelta, timezone
import hashlib
import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock

from configure_model import MODELS, save_new
from model_gateway import ModelResponses
from research_budget import WebBudget, DAILY_CALL_LIMIT
from research_exports import export_csv, export_markdown
from research_live import LiveStore
from research_web import make_server


class LiveTests(unittest.TestCase):
    def test_small_task_capacity_does_not_reset_quota(self):
        self.observation['remaining_tokens'] = {m:90000 for m in MODELS}
        self.budget.observe(self.observation)
        self.assertFalse(self.budget.status('qwen-review-thinking-json-v2')['ready'])
        self.assertTrue(self.budget.status('qwen-review-thinking-json-v2', 50000)['ready'])
        path = self.budget.issue('small-cap', 'a'*64, 'test', model_profile='qwen-review-thinking-json-v2', max_input_bytes=50000)
        self.assertEqual(json.loads(path.read_text())['max_input_bytes'], 50000)
        self.assertFalse(self.budget.status('qwen-review-thinking-json-v2', 50000)['ready'])
        self.budget.settle('small-cap', dict(api_calls=2, usage_unknown=False, known_tokens_by_model={MODELS[0]:10000}))
        self.assertEqual(self.budget.status('qwen-review-thinking-json-v2', 50000)['remaining_tokens'][MODELS[0]], 80000)

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.config=self.root/'config'/'key.json'
        save_new(self.config,{'api_key':'sk-fake-live-test-not-a-credential','base_url':'https://dashscope.aliyuncs.com/compatible-mode/v1',
                             'models':MODELS,'free_only_user_confirmed':True,'paid_calls_authorized':False})
        self.budget=WebBudget(self.root/'budget',self.config)
        self.observation={'remaining_tokens':{m:1000000 for m in MODELS},'stop_when_used_up':True}
        self.search=Mock(side_effect=self.search_result)
        self.collect=Mock(side_effect=self.source)
        self.sent=[]
        self.store=LiveStore(self.root/'live',search=self.search,collector=self.collect,execution_policy='legacy',xhs_enabled=False,
            backend_factory=lambda path,sha:ModelResponses(path,sha,self.root/'ledgers',self.config,send=self.send),budget=self.budget)

    def search_result(self,query,objective,**kw):
        return {'status':'searched','backend':'test','query':query,'objective':objective,'searched_at':datetime.now(timezone.utc).isoformat(),
                'results':[{'rank':1,'title':'虚构帮助页','url':'https://example.com/help'}]}

    def source(self,url,**kw):
        content='可调整每周训练日。'
        return {'url':url,'status':'fetched_unverified','content':content,'error':None,
                'content_sha256':hashlib.sha256(content.encode()).hexdigest(),'fetched_at':datetime.now(timezone.utc).isoformat()}

    def send(self,config,body):
        self.sent.append(body)
        payload=json.loads(body['messages'][1]['content'])
        if 'review_targets' in payload:
            content={'verdict':'pass','checks':[{'target_id':k,'claim':'测试判断','supported':True,'source_ids':['S1'],'reason':'预设测试响应'} for k in payload['review_targets']], 'issues':[],'limitations':['模拟']}
        else:
            case=payload.get('case',payload)
            content={'answers':[{'question_id':q,'status':'answered' if q=='Q1' else 'unknown','text':'可调整每周训练日。' if q=='Q1' else '范围未核实',
                     'refs':[{'source_id':'S1','quote':'可调整每周训练日。','locator':'S1 保存的页面正文'}] if q=='Q1' else []} for q in case['questions']],
                     'hypotheses':[],'limitations':['模拟']}
        return {'choices':[{'message':{'content':json.dumps(content,ensure_ascii=False)},'finish_reason':'stop'}],
                'usage':{'prompt_tokens':20,'completion_tokens':20,'total_tokens':40}}

    def payload(self,letter='a',**kw):
        return {'question':'训练计划功能','region':'','period':'','sources':['web'],'required_sources':['web'],
                'request_id':letter*32,'parent_task_id':None,**kw}

    def preview_send(self, config, body, repair=False):
        self.sent.append(body)
        payload=json.loads(body['messages'][1]['content']);case=payload.get('case',payload)
        sid=case['sources'][0]['segments'][0]['segment_id']
        self.assertEqual(body['model'],'qwen3.8-flash')
        self.assertTrue(body['stream'])
        if 'repair_plan' in payload:
            content={'changes':[{'target_id':'L:1','replacement':{'text':'来源未说明未来收费。'}}],
                     'resolutions':[{'issue_id':'I1','resolution':'changed','reason':'删除无依据承诺。','refs':[{'segment_id':sid}]}]}
        elif 'review_targets' in payload:
            self.assertTrue(body['enable_thinking'])
            content={'checks':[{'target_id':k,'supported':not(repair and k=='L:1' and 'revision_context' not in payload),
                'reason':'来源未承诺永久政策。' if k=='L:1' else '原文支持。','evidence_ids':[sid]} for k in payload['review_targets']]}
            if payload.get('revision_context'):
                content['repair_checks']=[{'issue_id':p['issue_id'],'resolved':True,'reason':'已删除无依据内容。','evidence_ids':[sid]} for p in payload['revision_context']['repair_plan']]
        else:
            self.assertFalse(body['enable_thinking'])
            content={'answers':[{'question_id':q,'status':'answered','text':'可调整每周训练日。','refs':[{'segment_id':sid}]} for q in case['questions']],
                     'hypotheses':[],'limitations':['永久免费。' if repair else '仅核对所给资料。']}
        return {'choices':[{'message':{'content':json.dumps(content,ensure_ascii=False)},'finish_reason':'stop'}],
                'usage':{'prompt_tokens':20,'completion_tokens':20,'total_tokens':40}}

    def use_preview(self,repair=False):
        self.store.execution_policy='preview-v1'
        self.store.backend_factory=lambda p,h:ModelResponses(p,h,self.root/'ledgers',self.config,
            send=lambda c,b:self.preview_send(c,b,repair))

    def test_preview_policy_flow_and_export_labels(self):
        self.use_preview();self.budget.observe(self.observation)
        result=self.wait(self.store.start(self.payload()))
        self.assertEqual(result['state']['review_status'],'passed')
        self.assertEqual(result['state']['reference_mode'],'compact-v2')
        self.assertEqual(result['task']['execution_policy'],'preview-v1')
        self.assertEqual(len(self.sent),2)
        self.assertEqual(self.store.budget_status()['models']['review'],'qwen3.8-flash')
        self.assertIn('本轮模型复核未发现问题',export_markdown(result))
        self.assertIn('limitation',export_csv(result))
        self.assertEqual(self.budget.status()['remaining_tokens'][MODELS[1]],1000000)

    def test_preview_limitation_repair_is_four_calls_and_retains_versions(self):
        self.use_preview(repair=True);self.budget.observe(self.observation)
        result=self.wait(self.store.start(self.payload()))
        self.assertEqual(result['state']['review_status'],'passed',result['state']['stop_reason'])
        self.assertEqual(result['state']['simulated_calls'],4)
        self.assertEqual(len(result['state']['versions']),2)
        self.assertEqual(result['state']['final_draft']['limitations'],['来源未说明未来收费。'])

    def test_preview_patch_format_repair_is_bounded_and_keeps_recheck_slot(self):
        self.use_preview(repair=True);self.budget.observe(self.observation)
        def malformed_once(c,b):
            response=self.preview_send(c,b,True)
            if len(self.sent)==3:
                raw=json.loads(response['choices'][0]['message']['content'])
                raw['resolutions'][0]['refs']=[raw['resolutions'][0]['refs'][0]['segment_id']]
                response['choices'][0]['message']['content']=json.dumps(raw)
            return response
        self.store.backend_factory=lambda p,h:ModelResponses(p,h,self.root/'ledgers',self.config,send=malformed_once)
        result=self.wait(self.store.start(self.payload()))
        self.assertEqual(result['state']['review_status'],'passed',result['state']['stop_reason'])
        self.assertEqual(result['state']['simulated_calls'],5)
        self.assertEqual(result['state']['format_repairs'],1)
        correction=json.loads(self.sent[3]['messages'][1]['content'])
        self.assertEqual(correction['patch_error'],'segment_ref_schema')
        self.assertIn('invalid_patch',correction)

    def test_preview_patch_format_failure_never_exceeds_five_calls(self):
        self.use_preview(repair=True);self.budget.observe(self.observation)
        def malformed(c,b):
            response=self.preview_send(c,b,True)
            raw=json.loads(response['choices'][0]['message']['content'])
            if 'resolutions' in raw:
                raw['resolutions'][0]['refs']=['fake']
                response['choices'][0]['message']['content']=json.dumps(raw)
            return response
        self.store.backend_factory=lambda p,h:ModelResponses(p,h,self.root/'ledgers',self.config,send=malformed)
        result=self.wait(self.store.start(self.payload()))
        self.assertEqual(result['state']['stop_reason'],'segment_ref_schema')
        self.assertEqual(result['state']['simulated_calls'],4)
        self.assertNotEqual(result['state']['review_status'],'passed')

    def test_preview_insufficient_quota_and_missing_platform_do_not_call_models(self):
        self.use_preview();self.observation['remaining_tokens']={m:100 for m in MODELS}
        self.budget.observe(self.observation)
        task=self.store.start(self.payload());result=self.wait(task)
        self.assertEqual(result['task']['reason'],'free_quota_insufficient');self.assertFalse(self.sent)
        self.assertTrue(result['case']['sources'])
        self.observation['remaining_tokens']={m:1000000 for m in MODELS};self.budget.observe(self.observation)
        self.store.resume(task['id']);result=self.wait(task)
        self.assertEqual(result['state']['review_status'],'passed');self.search.assert_called_once()
        count=len(self.sent)
        result=self.wait(self.store.start(self.payload(letter='b',sources=['web','xhs'],required_sources=['xhs'])))
        self.assertEqual(result['task']['reason'],'required_source_unavailable')
        self.assertEqual(len(self.sent),count)

    def test_waiting_legacy_task_without_policy_remains_legacy_after_default_change(self):
        task=self.store.start(self.payload());self.wait(task)
        self.store.tasks[task['id']].pop('execution_policy')
        from research_live import save
        path=self.store.directory/task['id']/'task.json';save(path,self.store.tasks[task['id']])
        before=path.read_bytes()
        self.store=LiveStore(self.root/'live',search=self.search,collector=self.collect,budget=self.budget,xhs_enabled=False,
            backend_factory=lambda p,h:ModelResponses(p,h,self.root/'ledgers',self.config,send=self.send))
        self.assertEqual(path.read_bytes(),before)
        self.budget.observe(self.observation);self.store.resume(task['id']);result=self.wait(task)
        self.assertEqual(result['state']['reference_mode'],'literal')
        self.assertEqual([r['model'] for r in self.sent],list(MODELS))
        self.search.assert_called_once()

    def wait(self,task):
        deadline=time.monotonic()+5
        while self.store.active and time.monotonic()<deadline: time.sleep(.01)
        self.assertIsNone(self.store.active)
        return self.store.detail(task['id'])

    def test_auto_acquire_analyze_review_exports_and_no_duplicate_calls(self):
        self.budget.observe(self.observation)
        task=self.store.start(self.payload()); result=self.wait(task)
        self.assertEqual(result['state']['review_status'],'passed')
        self.assertEqual(len(self.sent),2)
        self.assertEqual(result['state']['api_calls'],0)  # 明确为模拟传输
        self.assertEqual(result['state']['simulated_calls'],2)
        self.assertEqual(self.store.start(self.payload())['id'],task['id'])
        self.assertEqual(len(self.sent),2)
        review_payload=json.loads(self.sent[1]['messages'][1]['content'])
        self.assertEqual(set(review_payload),{'case','draft','review_targets'})
        text=export_markdown(result); csv=export_csv(result)
        self.assertIn('S1',text); self.assertIn('https://example.com/help',csv)
        self.assertNotIn('离线回放',text)
        self.assertEqual(self.budget.status()['remaining_tokens'][MODELS[0]],999960)

    def test_missing_quota_preserves_evidence_then_resumes_without_search(self):
        task=self.store.start(self.payload()); result=self.wait(task)
        self.assertEqual(result['task']['status'],'waiting_user')
        self.assertEqual(result['task']['reason'],'quota_observation_required')
        self.assertEqual(len(result['case']['sources']),1); self.assertFalse(self.sent)
        self.budget.observe(self.observation)
        self.store.resume(task['id']); result=self.wait(task)
        self.assertEqual(result['state']['review_status'],'passed')
        self.search.assert_called_once(); self.collect.assert_called_once()

    def test_verified_quote_location_is_bound_without_extra_model_request(self):
        self.budget.observe(self.observation)
        def misplaced(config,body):
            response=self.send(config,body)
            content=json.loads(response['choices'][0]['message']['content'])
            if 'answers' in content:
                content['answers'][0]['refs'][0]['locator']='功能介绍章节'
                response['choices'][0]['message']['content']=json.dumps(content)
            return response
        self.store.backend_factory=lambda p,h:ModelResponses(p,h,self.root/'ledgers',self.config,send=misplaced)
        result=self.wait(self.store.start(self.payload()))
        self.assertEqual(result['state']['review_status'],'passed')
        self.assertEqual(result['state']['simulated_calls'],2)
        self.assertEqual(len(result['state']['versions'][0]['location_bindings']),1)
        self.assertEqual(result['state']['format_repairs'],0)
        self.assertEqual(result['state']['final_draft']['answers'][0]['refs'][0]['locator'],'S1 保存的页面正文')

    def test_citation_retry_reuses_evidence_preserves_parent_and_is_bounded(self):
        self.budget.observe(self.observation)
        def broken(config, body):
            response=self.send(config,body)
            draft=json.loads(response['choices'][0]['message']['content'])
            draft['answers'][0]['refs'][0]['quote']='"published_at": null,'
            response['choices'][0]['message']['content']=json.dumps(draft)
            return response
        self.store.backend_factory=lambda p,h:ModelResponses(p,h,self.root/'ledgers',self.config,send=broken)
        parent=self.store.start(self.payload()); result=self.wait(parent)
        self.assertEqual(result['state']['review_status'],'blocked_by_validation')
        parent_dir=self.root/'live'/parent['id']
        snapshots={str(p.relative_to(parent_dir)):p.read_bytes() for p in parent_dir.rglob('*') if p.is_file()}
        self.store.backend_factory=lambda p,h:ModelResponses(p,h,self.root/'ledgers',self.config,send=self.send)
        child=self.store.retry_citations(parent['id'],'b'*32); result=self.wait(child)
        self.assertEqual(result['state']['review_status'],'passed')
        self.assertEqual(result['task']['retry_from'],parent['id'])
        self.assertEqual(result['state']['simulated_calls'],2)
        self.assertEqual(self.store.retry_citations(parent['id'],'b'*32)['id'],child['id'])
        with self.assertRaises(ValueError): self.store.retry_citations(parent['id'],'c'*32)
        with self.assertRaises(ValueError): self.store.retry_citations(child['id'],'c'*32)
        self.search.assert_called_once(); self.collect.assert_called_once()
        self.assertEqual(snapshots,{str(p.relative_to(parent_dir)):p.read_bytes() for p in parent_dir.rglob('*') if p.is_file()})
        self.assertEqual((parent_dir/'analysis-input.json').read_bytes(),(self.root/'live'/child['id']/'analysis-input.json').read_bytes())
        self.assertEqual(self.budget.status()['reason'],'ready')
        source=parent_dir/'sources'/'source-1.json'
        data=json.loads(source.read_text()); data['content']='篡改正文'; source.write_text(json.dumps(data))
        with self.assertRaises(ValueError): self.store.retry_citations(parent['id'],'d'*32)

    def test_required_missing_never_searches_or_uses_model(self):
        task=self.store.start(self.payload(sources=['web','xhs'],required_sources=['xhs']))
        result=self.wait(task)
        self.assertEqual(result['task']['reason'],'required_source_unavailable')
        self.assertFalse(self.sent); self.search.assert_not_called()
        self.assertIn('xhs',export_csv(result))
        child=self.store.start(self.payload('b',parent_task_id=task['id']))
        self.wait(child)
        self.assertEqual(self.store.detail(task['id'])['task']['status'],'waiting_user')

    def test_optional_missing_is_disclosed_in_exports(self):
        task=self.store.start(self.payload(sources=['web','xhs'],required_sources=['web']))
        result=self.wait(task)
        self.assertEqual(result['task']['skipped_sources'],['xhs'])
        self.assertIn('xhs',export_markdown(result)); self.assertIn('xhs',export_csv(result))

    def test_explicit_unselected_platform_not_silently_replaced(self):
        result=self.wait(self.store.start(self.payload(question='只看小红书评论')))
        self.assertEqual(result['task']['reason'],'required_source_unavailable'); self.search.assert_not_called()

    def test_cancel_during_search_drains_before_next_task(self):
        entered=threading.Event(); release=threading.Event()
        def waiting(*args,**kw):
            entered.set(); release.wait(3); return self.search_result(*args,**kw)
        self.search.side_effect=waiting
        task=self.store.start(self.payload()); self.assertTrue(entered.wait(2))
        self.store.cancel(task['id'])
        with self.assertRaises(RuntimeError): self.store.start(self.payload('b'))
        with self.assertRaises(RuntimeError): self.store.observe_quota(self.observation)
        release.set(); result=self.wait(task)
        self.assertEqual(result['task']['status'],'cancelled'); self.collect.assert_not_called(); self.assertFalse(self.sent)

    def test_cancel_inflight_model_records_usage_but_no_review(self):
        self.budget.observe(self.observation); entered=threading.Event(); release=threading.Event()
        original=self.send
        def waiting(c,b): entered.set(); release.wait(3); return original(c,b)
        self.store.backend_factory=lambda p,h:ModelResponses(p,h,self.root/'ledgers',self.config,send=waiting)
        task=self.store.start(self.payload()); self.assertTrue(entered.wait(2)); self.store.cancel(task['id']); release.set()
        result=self.wait(task)
        self.assertEqual(result['task']['status'],'cancelled'); self.assertEqual(len(self.sent),1)
        self.assertEqual(self.budget.status()['remaining_tokens'][MODELS[0]],999960)
        self.assertNotEqual(result['state']['review_status'],'passed')

    def test_quota_refresh_cannot_reset_daily_allocations(self):
        self.budget.observe(self.observation)
        for i in range((DAILY_CALL_LIMIT-4)//2): self.wait(self.store.start(self.payload('a',request_id=f'{i:032x}')))
        # 不拆散需要5次容量的新任务；余下4次留给短流程。
        self.assertEqual(self.budget.status()['occupied_calls_today'],DAILY_CALL_LIMIT-4)
        path=self.budget.issue('z'*32,'a'*64,None,requested_calls=4)
        self.budget.settle('z'*32,{'api_calls':4,'usage_unknown':False,'known_tokens_by_model':{}})
        self.budget.observe(self.observation)
        self.assertEqual(self.budget.status()['reason'],'daily_call_limit')
        result=self.wait(self.store.start(self.payload('1',request_id='1'*31+'a')))
        self.assertEqual(result['task']['reason'],'daily_call_limit'); self.assertEqual(len(self.sent),DAILY_CALL_LIMIT-4)

    def test_unsettled_unknown_and_legacy_calls_remain_reserved(self):
        self.budget.observe(self.observation)
        for letter in 'ab': self.budget.issue(letter*32,'a'*64,None)
        self.assertEqual(self.budget.status()['occupied_calls_today'],8)

        self.budget.settle('a'*32,{'api_calls':1,'usage_unknown':True})
        self.assertEqual(self.budget.status()['occupied_calls_today'],8)
        self.budget.settle('b'*32,{'api_calls':1,'usage_unknown':False,'known_tokens_by_model':{}})
        self.assertEqual(self.budget.status()['occupied_calls_today'],5)
        p=self.budget.root/('allocation-'+'b'*32+'.json')
        old=json.loads(p.read_text());old.pop('charged_calls');p.write_text(json.dumps(old))
        self.assertEqual(self.budget.status()['occupied_calls_today'],8)

    def test_review_continuation_is_bound_and_does_not_search_again(self):
        self.budget.observe(self.observation)
        def parent_send(config,body):
            response=self.send(config,body);content=json.loads(response['choices'][0]['message']['content'])
            if len(self.sent)==1:content['answers'][0]['refs'][0]['quote']='虚构错误引用'
            if 'verdict' in content:
                content['verdict']='revise';content['issues']=[{'severity':'minor','description':'明确虚构范围','action':'rewrite'}]
            response['choices'][0]['message']['content']=json.dumps(content);return response
        self.store.backend_factory=lambda p,h:ModelResponses(p,h,self.root/'ledgers',self.config,send=parent_send)
        original_issue=self.budget.issue
        self.budget.issue=lambda tid,h,p,requested_calls=4,**kw:original_issue(tid,h,p,requested_calls=min(4,requested_calls),**kw)
        parent=self.store.start(self.payload());result=self.wait(parent)
        self.assertEqual(result['state']['stop_reason'],'budget_exhausted')
        def child_send(config,body):
            response=self.send(config,body);content=json.loads(response['choices'][0]['message']['content'])
            if 'answers' in content:content['answers'][0]['text']+='（依据虚构说明）'
            response['choices'][0]['message']['content']=json.dumps(content);return response
        self.store.backend_factory=lambda p,h:ModelResponses(p,h,self.root/'ledgers',self.config,send=child_send)
        child=self.store.continue_review(parent['id'],'b'*32);result=self.wait(child)
        self.assertEqual(result['state']['review_status'],'passed');self.assertEqual(result['state']['simulated_calls'],2)
        self.assertEqual(self.store.continue_review(parent['id'],'b'*32)['id'],child['id'])
        with self.assertRaises(ValueError):self.store.continue_review(parent['id'],'c'*32)
        self.search.assert_called_once();self.collect.assert_called_once()

    def test_five_stage_task_finishes_without_manual_continuation(self):
        self.budget.observe(self.observation)
        def five_stages(config,body):
            response=self.send(config,body)
            content=json.loads(response['choices'][0]['message']['content'])
            if len(self.sent)==1: content['answers'][0]['refs'][0]['quote']='错误引文'
            if len(self.sent)==3:
                content['verdict']='revise'
                content['issues']=[{'severity':'minor','description':'明确测试范围','action':'rewrite'}]
            if len(self.sent)==4: content['answers'][0]['text']+='（仅限测试范围）'
            response['choices'][0]['message']['content']=json.dumps(content)
            return response
        self.store.backend_factory=lambda p,h:ModelResponses(p,h,self.root/'ledgers',self.config,send=five_stages)
        result=self.wait(self.store.start(self.payload()))
        self.assertEqual(result['state']['review_status'],'passed')
        self.assertEqual(result['state']['simulated_calls'],5)
        self.assertEqual(result['state']['revision'],1)
        self.assertEqual(result['state']['format_repairs'],1)
        self.assertEqual(result['task']['limits']['max_model_calls'],5)
        self.assertEqual(len(self.store.listing()),1)
        self.search.assert_called_once()

    def test_daily_budget_expands_persistently_without_resetting_usage(self):
        self.budget.observe(self.observation)
        for letter in 'abc':
            self.budget.issue(letter*32,'a'*64,None)
            self.budget.settle(letter*32,{'api_calls':4,'usage_unknown':False,'known_tokens_by_model':{}})
        self.assertTrue(self.budget.status()['ready'])
        self.assertEqual(self.budget.status()['current_daily_call_limit'],12)
        snapshots={p.name:p.read_bytes() for p in self.budget.root.glob('allocation-*.json')}
        self.budget.issue('d'*32,'a'*64,None,requested_calls=5)
        self.assertEqual(self.budget.status()['current_daily_call_limit'],20)
        self.assertEqual(self.budget.status()['occupied_calls_today'],17)
        for name,content in snapshots.items():self.assertEqual((self.budget.root/name).read_bytes(),content)
        self.budget.settle('d'*32,{'api_calls':1,'usage_unknown':True})
        again=WebBudget(self.budget.root,self.config)
        self.assertEqual(again.status()['occupied_calls_today'],17)
        self.assertEqual(again.status()['current_daily_call_limit'],20)
        with self.assertRaises(ValueError):again.issue('e'*32,'a'*64,None,requested_calls=6)
        self.observation['remaining_tokens']={m:200000 for m in MODELS}
        again.observe(self.observation)
        self.assertEqual(again.status()['reason'],'free_quota_insufficient')

    def test_stale_observation_no_call(self):
        self.budget.observe(self.observation)
        data=json.loads(self.budget.path.read_text()); data['observed_at']=(datetime.now(timezone.utc)-timedelta(days=2)).isoformat()
        self.budget.path.write_text(json.dumps(data))
        result=self.wait(self.store.start(self.payload()))
        self.assertEqual(result['task']['reason'],'quota_observation_stale'); self.assertFalse(self.sent)

    def test_candidate_profile_binds_roles_and_settles_selected_models(self):
        self.budget.observe(self.observation)
        path=self.budget.issue('p'*32,'a'*64,None,requested_calls=2,model_profile='qwen-review-thinking-v1')
        grant=json.loads(path.read_text())
        self.assertEqual(set(grant['roles'].values()),{'qwen3.8-flash'})
        self.assertTrue(grant['role_options']['review']['enable_thinking'])
        self.assertFalse(grant['role_options']['analysis']['enable_thinking'])
        self.budget.settle('p'*32,{'api_calls':1,'usage_unknown':False,
                                'known_tokens_by_model':{'qwen3.8-flash':5000}})
        saved=json.loads((self.budget.root/('allocation-'+'p'*32+'.json')).read_text())
        self.assertEqual(saved['charged_tokens'],{'qwen3.8-flash':5000})
        self.assertEqual(self.budget.status()['remaining_tokens']['deepseek-v4.1-flash'],1000000)
        self.assertEqual(self.budget.status('max-review-thinking-v1')['reason'],'free_quota_insufficient')
        self.observation['remaining_tokens']={m:250000 for m in MODELS}
        self.budget.observe(self.observation)
        path=self.budget.issue('q'*32,'a'*64,None,requested_calls=2,model_profile='qwen-review-thinking-v1')
        self.assertEqual(json.loads(path.read_text())['token_limits'],{'qwen3.8-flash':250000})

    def test_connection_recovery_preserves_unknown_budget_and_cannot_repeat(self):
        self.budget.observe(self.observation)
        def parent_send(config,body):
            response=self.send(config,body);content=json.loads(response['choices'][0]['message']['content'])
            if len(self.sent)==1:content['answers'][0]['refs'][0]['quote']='错误引文'
            if 'verdict' in content:
                content['verdict']='revise';content['issues']=[{'severity':'minor','description':'注明虚构','action':'rewrite'}]
            response['choices'][0]['message']['content']=json.dumps(content);return response
        self.store.backend_factory=lambda p,h:ModelResponses(p,h,self.root/'ledgers',self.config,send=parent_send)
        original_issue=self.budget.issue
        self.budget.issue=lambda tid,h,p,requested_calls=4,**kw:original_issue(tid,h,p,requested_calls=min(4,requested_calls),**kw)
        parent=self.store.start(self.payload());self.wait(parent)
        def broken(*args):raise OSError('never expose this secret')
        self.store.backend_factory=lambda p,h:ModelResponses(p,h,self.root/'ledgers',self.config,send=broken)
        failed=self.store.continue_review(parent['id'],'b'*32);result=self.wait(failed)
        self.assertEqual(result['task']['reason'],'transport_or_usage_error')
        self.assertTrue(result['state']['usage_unknown'])
        allocation=self.budget.root/f'allocation-{failed["id"]}.json';before=allocation.read_bytes()
        self.assertEqual(self.budget.status()['occupied_calls_today'],5)
        def recovered(config,body):
            response=self.send(config,body);content=json.loads(response['choices'][0]['message']['content'])
            if 'answers' in content:content['answers'][0]['text']+='（虚构测试）'
            response['choices'][0]['message']['content']=json.dumps(content);return response
        self.store.backend_factory=lambda p,h:ModelResponses(p,h,self.root/'ledgers',self.config,send=recovered)
        child=self.store.retry_connection(failed['id'],'c'*32);result=self.wait(child)
        self.assertEqual(result['state']['review_status'],'passed')
        self.assertEqual(result['state']['simulated_calls'],2)
        self.assertEqual(allocation.read_bytes(),before)
        self.assertEqual(self.store.retry_connection(failed['id'],'c'*32)['id'],child['id'])
        with self.assertRaises(ValueError):self.store.retry_connection(failed['id'],'d'*32)
        with self.assertRaises(ValueError):self.store.retry_connection(child['id'],'e'*32)
        self.search.assert_called_once();self.collect.assert_called_once()

    def failed_review_task(self):
        self.budget.observe(self.observation)
        def failed(config,body):
            response=self.send(config,body)
            if body['model']==MODELS[1]: raise ConnectionResetError('synthetic disconnect')
            return response
        self.store.backend_factory=lambda p,h:ModelResponses(p,h,self.root/'ledgers',self.config,send=failed)
        parent=self.store.start(self.payload());result=self.wait(parent)
        self.assertEqual(result['task']['reason'],'review_transport_or_usage_error')
        return parent

    def test_failed_review_recheck_uses_saved_draft_and_keeps_unknown_reservation(self):
        parent=self.failed_review_task()
        allocation=self.budget.root/f'allocation-{parent["id"]}.json';before=allocation.read_bytes()
        self.store.backend_factory=lambda p,h:ModelResponses(p,h,self.root/'ledgers',self.config,send=self.send)
        child=self.store.retry_review(parent['id'],'b'*32);result=self.wait(child)
        self.assertEqual(result['state']['review_status'],'passed')
        self.assertEqual(result['state']['simulated_calls'],1)
        self.assertEqual(result['task']['limits']['max_model_calls'],3)
        self.assertEqual(allocation.read_bytes(),before)
        self.assertEqual(self.store.retry_review(parent['id'],'b'*32)['id'],child['id'])
        with self.assertRaises(ValueError):self.store.retry_review(parent['id'],'c'*32)
        with self.assertRaises(ValueError):self.store.retry_review(child['id'],'c'*32)
        self.search.assert_called_once();self.collect.assert_called_once()

    def test_recheck_validates_snapshot_before_calling_model(self):
        parent=self.failed_review_task();path=self.store.directory/parent['id']/'flow/state.json'
        state=json.loads(path.read_text());state['final_draft']['answers'][0]['text']='被改写的旧稿'
        path.write_text(json.dumps(state))
        with self.assertRaises(ValueError):self.store.retry_review(parent['id'],'b'*32)
        self.assertEqual(len(self.sent),2)

    def test_recheck_has_one_revision_and_three_call_limit(self):
        parent=self.failed_review_task();reviews=0
        def recheck(config,body):
            nonlocal reviews
            response=self.send(config,body);content=json.loads(response['choices'][0]['message']['content'])
            if 'verdict' in content:
                reviews+=1
                if reviews==1:content.update(verdict='revise',issues=[{'severity':'minor','description':'需明确为虚构测试','action':'rewrite'}])
            else:content['answers'][0]['text']+='（虚构测试）'
            response['choices'][0]['message']['content']=json.dumps(content);return response
        self.store.backend_factory=lambda p,h:ModelResponses(p,h,self.root/'ledgers',self.config,send=recheck)
        result=self.wait(self.store.retry_review(parent['id'],'b'*32))
        self.assertEqual(result['state']['review_status'],'passed')
        self.assertEqual(result['state']['simulated_calls'],3)
        self.assertEqual(result['state']['revision'],1)
        self.assertIn('revision_context',json.loads(self.sent[-1]['messages'][1]['content']))
        self.search.assert_called_once();self.collect.assert_called_once()

    def test_restart_and_conflicting_request_do_not_replay(self):
        self.budget.observe(self.observation); task=self.store.start(self.payload()); self.wait(task)
        other=LiveStore(self.root/'live',search=self.search,collector=self.collect,budget=self.budget)
        self.assertEqual(other.start(self.payload())['id'],task['id'])
        with self.assertRaises(ValueError): other.start(self.payload(question='不同范围'))
        self.assertEqual(len(self.sent),2)

    def test_bad_evidence_has_no_model_calls(self):
        self.collect.side_effect=lambda url,**kw:{**self.source(url),'content_sha256':'bad'}
        result=self.wait(self.store.start(self.payload()))
        self.assertEqual(result['task']['status'],'stopped'); self.assertFalse(self.sent)

    def test_invalid_review_schema_is_safe_for_web_renderer(self):
        self.budget.observe(self.observation)
        original=self.send
        def malformed(config,body):
            response=original(config,body)
            if 'review_targets' in json.loads(body['messages'][1]['content']):
                response['choices'][0]['message']['content']='{"verdict":"pass","checks":"wrong","issues":null}'
            return response
        self.store.backend_factory=lambda p,h:ModelResponses(p,h,self.root/'ledgers',self.config,send=malformed)
        result=self.wait(self.store.start(self.payload()))
        self.assertEqual(result['state']['review_status'],'invalid')
        self.assertEqual(result['state']['reviews'][0]['result']['checks'],[])
        self.assertIn('未通过',export_markdown(result))

    def test_web_endpoints_share_auth_and_show_result(self):
        server=make_server(self.root/'web-runs',0)
        server.live.search=self.search;server.live.collector=self.collect;server.live.budget=self.budget
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            c=http.client.HTTPConnection(server.host)
            c.request('POST','/api/live/tasks',body=json.dumps(self.payload()),headers={'Content-Type':'application/json'})
            r=c.getresponse();r.read();self.assertEqual(r.status,403)
            headers={'X-Session-Token':server.session,'X-CSRF-Token':server.csrf,'Origin':server.origin,'Content-Type':'application/json'}
            c.request('POST','/api/live/tasks',body=json.dumps(self.payload()),headers=headers)
            r=c.getresponse();task=json.loads(r.read());self.assertEqual(r.status,202)
            deadline=time.monotonic()+4
            while server.live.active and time.monotonic()<deadline:time.sleep(.01)
            c.request('GET','/api/live/tasks/'+task['id'],headers=headers)
            r=c.getresponse();data=json.loads(r.read());self.assertEqual(r.status,200)
            self.assertEqual(data['task']['status'],'waiting_user')
            self.assertNotIn('sk-fake',json.dumps(data));self.assertNotIn('api_key',json.dumps(data))
            c.request('GET','/api/live/tasks/'+task['id']+'/report.md',headers=headers)
            r=c.getresponse();self.assertIn('S1',r.read().decode());self.assertEqual(r.status,200)
            c.close()
        finally:server.shutdown();server.server_close();thread.join(1)


if __name__=='__main__':unittest.main()
