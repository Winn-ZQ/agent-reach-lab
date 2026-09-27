from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

from configure_model import MODELS, save_new
from flow_backend import BackendStopped, REVIEW_CAPACITY
from model_gateway import ModelResponses, checked_grant, safe_failure_kind
from model_smoke import append_event
from model_profiles import profile, output_capacity
from research_flow import digest, encoded, run
from run_research import preflight
from test_research_flow import fixture


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.raw, self.case, self.draft, self.review = fixture()
        self.config = self.root/'private'/'key.json'
        self.key = 'sk-fake-integration-credential-never-real'
        self.config_data = {'api_key': self.key, 'base_url': 'https://dashscope.aliyuncs.com/compatible-mode/v1',
                            'models': MODELS, 'free_only_user_confirmed': True, 'paid_calls_authorized': False}
        save_new(self.config, self.config_data)
        now = datetime.now(timezone.utc)
        self.grant_path = self.root/'private'/'budget.json'
        self.grant = {'grant_id':'test-only', 'approved':True, 'authorization_note':'unit test fixture, not user authorization',
                      'free_only':True, 'paid_calls_authorized':False,
                      'case_sha256':digest(self.case), 'parent_run':'historical-first-round-test',
                      'expires_at':(now+timedelta(hours=1)).isoformat(), 'max_calls':4,
                      'roles':{'analysis':MODELS[0], 'repair':MODELS[0], 'review':MODELS[1]},
                      'token_limits':{m:300000 for m in MODELS},
                      'free_quota_observation':{m:{'remaining_tokens':500000,'stop_when_used_up':True,
                                                   'observed_at':now.isoformat()} for m in MODELS}}
        save_new(self.grant_path,self.grant)
        self.seen = []

    def update_grant(self):
        self.grant_path.write_text(json.dumps(self.grant))

    def response(self, content, prompt=10, output=5):
        return {'choices':[{'message':{'content':encoded(content)},'finish_reason':'stop'}],
                'usage':{'prompt_tokens':prompt,'completion_tokens':output,'total_tokens':prompt+output}}

    def successful_send(self, config, body):
        self.assertEqual(config['api_key'],self.key)
        self.assertEqual(body['enable_thinking'],False)
        self.assertEqual(body['max_tokens'],4096)
        self.assertFalse(body['stream'])
        self.seen.append(deepcopy(body))
        return self.response(self.review if body['model']==MODELS[1] else self.draft)

    def gateway(self, send=None):
        return ModelResponses(self.grant_path,digest(self.case),self.root/'ledgers',self.config,
                              send if send is not None else self.successful_send)

    def messages(self):
        return [{'role':'system','content':'只输出JSON'}, {'role':'user','content':encoded(self.case)}]

    def test_narrow_input_cap_preserves_same_model_review_reservation(self):
        self.use_profile('qwen-review-thinking-v1')
        self.grant['max_input_bytes'] = 8192
        self.grant['token_limits'][MODELS[0]] = 30000
        self.update_grant()
        with self.gateway(lambda config, body:self.response(self.draft)).session() as source:
            source.respond('analysis', self.messages())
            started = next(e for e in source.events() if e['event'] == 'started')
            self.assertGreater(started['reserved_capacity'][MODELS[0]], 8192+8192)
            oversized = self.messages(); oversized[0]['content'] = 'x'*8200
            with self.assertRaisesRegex(BackendStopped, 'input_size_limit'):
                source.respond('review', oversized)
            self.assertEqual(source.snapshot()['simulated_calls'], 1)

    def test_input_cap_cannot_disable_bounds(self):
        for value in (0, 8191, 180001, True, '8192'):
            self.grant['max_input_bytes'] = value; self.update_grant()
            with self.assertRaises(BackendStopped): checked_grant(self.grant_path, digest(self.case))

    def test_segment_projection_checks_original_case_scope_before_network(self):
        from evidence_segments import present_case, catalog
        projected=present_case(self.case,catalog(self.case))
        messages=self.messages();messages[1]['content']=encoded(projected)
        with self.gateway().session() as source:
            source.respond('analysis',messages)
            projected['task']='另一个未授权任务'
            messages[1]['content']=encoded(projected)
            with self.assertRaisesRegex(BackendStopped,'task_scope_mismatch'):
                source.respond('analysis',messages)
            self.assertEqual(len(self.seen),1)

    def use_profile(self, name):
        roles, options = profile(name)
        observation = next(iter(self.grant['free_quota_observation'].values()))
        self.grant.update(roles=roles, role_options=options, model_profile=name,
                          token_limits={m:300000 for m in set(roles.values())},
                          free_quota_observation={m:deepcopy(observation) for m in set(roles.values())})
        self.update_grant()

    def test_thinking_payload_and_usage_include_reasoning(self):
        self.use_profile('qwen-review-thinking-v1')
        def send(config, body):
            self.assertTrue(body['enable_thinking'])
            self.assertEqual(body['max_completion_tokens'],8192)
            self.assertEqual(body['thinking_budget'],4096)
            self.assertNotIn('max_tokens',body)
            self.assertNotIn('response_format',body)
            self.assertNotIn('reasoning_effort',body)
            result=self.response(self.review,output=6000)
            result['usage']['completion_tokens_details']={'reasoning_tokens':5000}
            return result
        with self.gateway(send).session() as source:
            source.respond('review',self.messages())
            self.assertEqual(source.snapshot()['known_tokens_by_model'][MODELS[0]],6010)
            self.assertEqual(source.events()[1]['role_options']['timeout_seconds'],180)

    def test_thinking_reserves_same_model_full_review_capacity(self):
        self.use_profile('qwen-review-thinking-v1')
        self.grant['token_limits'][MODELS[0]]=REVIEW_CAPACITY+4096
        self.update_grant();self.config.unlink()
        with self.gateway(lambda *a:self.fail('network')).session() as source:
            with self.assertRaisesRegex(BackendStopped,'token_budget_exhausted'):
                source.respond('analysis',self.messages())

    def test_max_requires_credentials_scope_and_quota(self):
        self.use_profile('max-review-thinking-v1')
        with self.gateway(lambda *a:self.fail('network')).session() as source:
            with self.assertRaisesRegex(BackendStopped,'model_not_configured'):
                source.respond('review',self.messages())
        self.grant['free_quota_observation']['qwen3.8-max']['stop_when_used_up']=False
        self.update_grant()
        with self.assertRaises(BackendStopped):checked_grant(self.grant_path,digest(self.case))

    def test_deepseek_thinking_has_supported_effort_only(self):
        self.use_profile('deepseek-review-thinking-v1')
        def send(config,body):
            self.assertEqual(body['reasoning_effort'],'low')
            self.assertNotIn('thinking_budget',body)
            return self.response(self.review)
        with self.gateway(send).session() as source: source.respond('review',self.messages())

    def test_json_thinking_profile_is_explicit_and_preserves_v1(self):
        from model_profiles import request_options
        self.use_profile('qwen-review-thinking-json-v2')
        body=request_options(self.grant,'review')
        self.assertTrue(body['enable_thinking'])
        self.assertEqual(body['response_format'],{'type':'json_object'})
        self.use_profile('qwen-review-thinking-v1')
        self.assertNotIn('response_format',request_options(self.grant,'review'))
        self.use_profile('deepseek-review-thinking-budget-v2')
        body=request_options(self.grant,'review')
        self.assertEqual(body['max_completion_tokens'],16384)
        self.assertEqual(body['reasoning_effort'],'low')
        self.assertEqual(output_capacity(self.grant,'review'),16394)

    def test_invalid_options_and_midrun_profile_change_are_rejected(self):
        self.use_profile('qwen-review-thinking-v1')
        self.grant['role_options']['review']['max_completion_tokens']=4096
        self.update_grant()
        with self.assertRaises(BackendStopped):checked_grant(self.grant_path,digest(self.case))
        self.use_profile('qwen-review-thinking-v1')
        with self.gateway(lambda *a:self.fail('network')).session() as source:
            self.grant['role_options']['review']['timeout_seconds']=200;self.update_grant()
            with self.assertRaisesRegex(BackendStopped,'budget_changed'):
                source.respond('review',self.messages())

    def test_reasoning_over_cap_keeps_usage_and_stops(self):
        self.use_profile('qwen-review-thinking-v1')
        with self.gateway(lambda *a:self.response(self.review,output=8203)).session() as source:
            with self.assertRaisesRegex(BackendStopped,'capacity_estimate_exceeded'):
                source.respond('review',self.messages())
            self.assertEqual(source.snapshot()['known_tokens_by_model'][MODELS[0]],8213)

    def test_failure_diagnostics_are_fixed_categories_without_secret_messages(self):
        import socket,ssl
        from urllib.error import URLError
        from http.client import RemoteDisconnected,IncompleteRead
        examples=[(URLError(socket.gaierror('private-host')), 'dns'),
                  (TimeoutError(self.key),'timeout'),(RemoteDisconnected(self.key),'remote_closed'),
                  (IncompleteRead(b'private-response'),'incomplete_read'),
                  (ConnectionResetError(self.key),'connection_reset'),
                  (URLError(ssl.SSLEOFError(self.key)), 'tls_eof'),
                  (ssl.SSLError(self.key),'tls'),(ValueError(self.key),'response_or_usage')]
        for exc,kind in examples:self.assertEqual(safe_failure_kind(exc),kind)
        def fail(config,body):raise URLError(RemoteDisconnected(self.key))
        with self.gateway(fail).session() as source:
            with self.assertRaisesRegex(BackendStopped,'transport_or_usage_error'):
                source.respond('analysis',self.messages())
            event=source.events()[-1]
            self.assertEqual(event['failure_kind'],'remote_closed')
            self.assertTrue(source.snapshot()['usage_unknown'])
            self.assertNotIn(self.key,json.dumps(source.events()))

    def test_full_flow_role_routing_and_usage(self):
        source = self.gateway()
        with source.session():
            state = run(self.case,source,self.root/'result')
        self.assertEqual(state['mode'],'mock_api')
        self.assertEqual((state['api_calls'],state['simulated_calls'],state['review_status']),(0,2,'passed'))
        self.assertEqual(state['known_tokens_by_model'],{m:15 for m in MODELS})
        self.assertEqual([b['model'] for b in self.seen], MODELS)
        self.assertEqual(self.seen[0]['response_format'],{'type':'json_object'})
        self.assertNotIn('response_format',self.seen[1])
        payload=json.loads(self.seen[1]['messages'][1]['content'])
        self.assertEqual(set(payload),{'case','draft','review_targets'})
        self.assertNotIn(self.key,(source.directory/'events.jsonl').read_text())

    def test_stream_transport_is_explicit_and_requests_usage(self):
        self.grant['transport_mode']='sse';self.update_grant()
        def send(config,body):
            self.assertTrue(body['stream'])
            self.assertEqual(body['stream_options'],{'include_usage':True})
            return self.response(self.review)
        with self.gateway(send).session() as source:
            source.respond('review',self.messages())
            self.assertFalse(source.snapshot()['usage_unknown'])
        self.grant['transport_mode']='other';self.update_grant()
        with self.assertRaises(BackendStopped):checked_grant(self.grant_path,digest(self.case))

    def test_no_grant_stops_before_config_or_network(self):
        self.grant_path.unlink(); self.config.unlink()
        self.assertEqual(preflight(self.case,self.grant_path)['budget_status'],'new_budget_required')
        with self.assertRaisesRegex(BackendStopped,'new_budget_required'):
            with self.gateway(lambda *a:self.fail('network')).session():
                self.fail('session entered')
        self.assertFalse((self.root/'ledgers').exists())

    def test_expired_unapproved_paid_or_stale_quota_denied(self):
        original=deepcopy(self.grant)
        changes=[('approved',False),('paid_calls_authorized',True),('expires_at','2000-01-01T00:00:00Z')]
        for key,value in changes:
            self.grant=deepcopy(original); self.grant[key]=value; self.update_grant()
            with self.assertRaises(BackendStopped): checked_grant(self.grant_path,digest(self.case))
        self.grant=original
        self.grant['free_quota_observation'][MODELS[0]]['observed_at']='2000-01-01T00:00:00Z'; self.update_grant()
        with self.assertRaisesRegex(BackendStopped,'quota_observation_stale'):
            checked_grant(self.grant_path,digest(self.case))

    def test_reserves_reviewer_before_reading_credentials(self):
        self.grant['token_limits'][MODELS[1]]=REVIEW_CAPACITY-1; self.update_grant()
        self.config.unlink()
        source=self.gateway(lambda *a:self.fail('network'))
        with source.session():
            with self.assertRaisesRegex(BackendStopped,'token_budget_exhausted'):
                source.respond('analysis',self.messages())
            self.assertEqual(source.snapshot()['simulated_calls'],0)

    def test_same_model_reservation_is_combined(self):
        self.grant['roles']={r:MODELS[0] for r in ('analysis','review','repair')}
        self.grant['token_limits']={MODELS[0]:REVIEW_CAPACITY}
        self.grant['free_quota_observation']={MODELS[0]:self.grant['free_quota_observation'][MODELS[0]]}
        self.update_grant()
        with self.gateway(lambda *a:self.fail('network')).session() as source:
            with self.assertRaisesRegex(BackendStopped,'token_budget_exhausted'):
                source.respond('analysis',self.messages(),True)

    def test_missing_usage_stops_future_calls_and_keeps_response(self):
        source=self.gateway(lambda *a:{'choices':[]})
        with source.session():
            with self.assertRaises(BackendStopped):source.respond('analysis',self.messages(),True)
            self.assertTrue(source.snapshot()['usage_unknown'])
            with self.assertRaisesRegex(BackendStopped,'prior_failure_or_unknown_usage'):
                source.respond('review',self.messages())
        self.assertTrue((source.directory/'response-1.json').exists())
        with self.assertRaisesRegex(BackendStopped,'budget_already_used'):
            with self.gateway().session():self.fail('replayed')

    def test_timeout_recorded_before_send_and_not_retried(self):
        def fail(config,body):
            rows=source.events()
            self.assertEqual(rows[-1]['event'],'started')
            raise TimeoutError(self.key)
        source=self.gateway(fail)
        with source.session():
            with self.assertRaises(BackendStopped):source.respond('analysis',self.messages())
        self.assertEqual(source.snapshot()['simulated_calls'],1)
        self.assertNotIn(self.key,(source.directory/'events.jsonl').read_text())

    def test_budget_persists_across_new_output_directories(self):
        with self.gateway().session() as source:
            source.respond('analysis',self.messages())
        with self.assertRaisesRegex(BackendStopped,'budget_already_used'):
            with self.gateway().session():self.fail('reset')
        self.assertEqual(source.snapshot()['simulated_calls'],1)

    def test_inflight_crash_blocks_restart(self):
        source=self.gateway()
        with source.session():
            append_event(source.directory/'events.jsonl',{'event':'started','attempt':1,'model':MODELS[0]})
        with self.assertRaisesRegex(BackendStopped,'budget_already_used'):
            with self.gateway().session():self.fail('replayed')
        self.assertTrue(source.snapshot()['usage_unknown'])

    def test_concurrency_is_locked(self):
        with self.gateway().session():
            with self.assertRaisesRegex(BackendStopped,'task_busy'):
                with self.gateway().session():self.fail('concurrent')

    def test_budget_cannot_change_midrun(self):
        with self.gateway().session() as source:
            self.grant['max_calls']=3; self.update_grant()
            with self.assertRaisesRegex(BackendStopped,'budget_changed'):
                source.respond('analysis',self.messages())
        self.assertEqual(source.snapshot()['simulated_calls'],0)

    def test_scope_and_configuration_rejected_before_send(self):
        with self.gateway(lambda *a:self.fail('network')).session() as source:
            changed=deepcopy(self.case); changed['task']='另一个任务'
            with self.assertRaisesRegex(BackendStopped,'task_scope_mismatch'):
                run(changed,source,self.root/'different')
            self.config_data['paid_calls_authorized']=True
            self.config.write_text(json.dumps(self.config_data))
            with self.assertRaisesRegex(BackendStopped,'model_configuration_invalid'):
                source.respond('analysis',self.messages())
        self.assertEqual(source.snapshot()['simulated_calls'],0)

    def test_truncation_counts_known_usage_but_stops(self):
        response=self.response(self.draft); response['choices'][0]['finish_reason']='length'
        with self.gateway(lambda *a:response).session() as source:
            with self.assertRaisesRegex(BackendStopped,'incomplete_response'):
                source.respond('analysis',self.messages())
            self.assertEqual(source.snapshot()['known_tokens_by_model'][MODELS[0]],15)
            self.assertFalse(source.snapshot()['usage_unknown'])
            with self.assertRaises(BackendStopped):source.respond('review',self.messages())

    def test_capacity_estimate_exceeded_is_not_hidden(self):
        with self.gateway(lambda *a:self.response(self.draft,prompt=200000)).session() as source:
            with self.assertRaisesRegex(BackendStopped,'capacity_estimate_exceeded'):
                source.respond('analysis',self.messages())
            self.assertEqual(source.snapshot()['known_tokens_by_model'][MODELS[0]],200005)

    def test_key_echo_is_redacted_and_cannot_enter_report(self):
        response=self.response({'echo':self.key})
        with self.gateway(lambda *a:response).session() as source:
            state=run(self.case,source,self.root/'result')
        self.assertEqual(state['stop_reason'],'credential_echo')
        for p in source.directory.glob('*.json*'):
            self.assertNotIn(self.key,p.read_text())
        self.assertNotIn(self.key,(self.root/'result'/'report.md').read_text())

    def test_call_limit_includes_reserved_review(self):
        self.grant['max_calls']=2; self.update_grant()
        with self.gateway().session() as source:
            source.respond('analysis',self.messages())
            with self.assertRaisesRegex(BackendStopped,'call_budget_exhausted'):
                source.respond('repair',self.messages())
            source.respond('review',self.messages())
            with self.assertRaisesRegex(BackendStopped,'call_budget_exhausted'):
                source.respond('review',self.messages())

    def test_full_repair_flow_uses_four_bounded_requests(self):
        bad_review=deepcopy(self.review)
        bad_review['verdict']='revise'; bad_review['checks'][0]['supported']=False
        bad_review['issues']=[{'severity':'blocking','description':'范围须说明为虚构资料','action':'rewrite'}]
        repair=deepcopy(self.draft); repair['answers'][0]['text']='虚构说明支持修改训练日，未核实真实产品。'
        outputs=iter([self.draft,bad_review,repair,self.review])
        def send(config,body):
            self.seen.append(body['model'])
            return self.response(next(outputs))
        with self.gateway(send).session() as source:
            state=run(self.case,source,self.root/'result')
        self.assertEqual((state['review_status'],state['revision'],state['simulated_calls']),('passed',1,4))
        self.assertEqual(state['known_tokens_by_model'],{m:30 for m in MODELS})
        self.assertEqual(self.seen,[MODELS[0],MODELS[1],MODELS[0],MODELS[1]])

    def test_invalid_json_is_counted_but_cannot_pass(self):
        response=self.response(self.draft); response['choices'][0]['message']['content']='{invalid'
        with self.gateway(lambda *a:response).session() as source:
            state=run(self.case,source,self.root/'result')
        self.assertEqual(state['stop_reason'],'invalid_response')
        self.assertEqual(state['known_tokens_by_model'][MODELS[0]],15)
        self.assertEqual(state['simulated_calls'],1)

    def test_each_request_must_use_the_authorized_case(self):
        messages=self.messages()
        payload=json.loads(messages[1]['content']); payload['task']='换了一个范围'
        messages[1]['content']=encoded(payload)
        with self.gateway(lambda *a:self.fail('network')).session() as source:
            with self.assertRaisesRegex(BackendStopped,'task_scope_mismatch'):
                source.respond('analysis',messages)
            self.assertEqual(source.snapshot()['simulated_calls'],0)


if __name__=='__main__':
    unittest.main()
