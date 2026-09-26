from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from flow_backend import BackendStopped
from research_flow import OfflineResponses
from review_benchmark import FIXTURE, CONDITIONS, comparison_ready, evaluate, materialize, packet, score, inherit_paragraph
from research_flow import digest


class ReviewBenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.fixture=json.loads(FIXTURE.read_text())

    def response(self, condition):
        expected={}
        for group in self.fixture['groups']:
            if condition=='paragraph': expected['A:'+group['id']]=all(c['expected_supported'] for c in group['claims'])
            else:expected.update({'A:'+c['id']:c['expected_supported'] for c in group['claims']})
        return {'schema_version':'research-review/0.2','verdict':'revise',
                'checks':[{'target_id':k,'supported':v,'claim':'模拟检查','source_ids':[],
                           'basis':'draft_logic','refs':[],'reason':'预设模拟理由，仅用于评分程序测试。'} for k,v in expected.items()],
                'issues':[{'target_id':k,'severity':'blocking','action':'rewrite','description':'预设不支持标签。'} for k,v in expected.items() if not v],
                'limitations':['模拟，不代表模型质量。']}

    def test_hidden_key_not_sent_and_evidence_identical(self):
        for condition in CONDITIONS:
            payload=packet(self.fixture,condition)
            text=json.dumps(payload,ensure_ascii=False)
            for forbidden in ('expected_supported','scoring_rationale','category'):
                self.assertNotIn(forbidden,text)
            self.assertEqual(payload['case'],materialize(self.fixture)[0])
        changed=deepcopy(self.fixture)
        for group in changed['groups']:
            group['scoring_rationale']='SECRET GOLD REASON'
            for claim in group['claims']:claim['expected_supported']=not claim['expected_supported']
        for condition in CONDITIONS:
            self.assertEqual(packet(changed,condition),packet(self.fixture,condition))

    def test_correct_counts_and_comparable_group_scores(self):
        for condition,count in [('paragraph',8),('atomic',12)]:
            result=score(self.fixture,condition,self.response(condition))
            self.assertTrue(result['valid'])
            self.assertEqual(result['scores']['correct'],count)
            self.assertEqual(result['group_scores']['correct'],8)

    def test_detects_false_accept_and_false_rejection_separately(self):
        response=self.response('atomic')
        for c in response['checks']:
            if c['target_id']=='A:Q1:C2':c['supported']=True
            if c['target_id']=='A:Q5:C1':c['supported']=False
        response['issues']=[i for i in response['issues'] if i['target_id']!='A:Q1:C2']
        response['issues'].append({'target_id':'A:Q5:C1','severity':'blocking','action':'rewrite','description':'模拟误判'})
        result=score(self.fixture,'atomic',response)
        self.assertEqual(result['scores']['false_accepts'],['A:Q1:C2'])
        self.assertEqual(result['scores']['false_rejections'],['A:Q5:C1'])
        self.assertEqual(result['group_scores']['correct'],6)

    def test_invalid_or_duplicate_cannot_get_semantic_score(self):
        for mode in ('missing','duplicate','invented_quote'):
            response=self.response('atomic')
            if mode=='missing':response['checks'].pop()
            if mode=='duplicate':
                duplicate=deepcopy(response['checks'][0]);duplicate['reason']='不同措辞的重复';response['checks'].append(duplicate)
            if mode=='invented_quote':
                response['checks'][0].update(basis='source',source_ids=['S1'],refs=[{'source_id':'S1','quote':'虚构的勾选'}])
            result=score(self.fixture,'atomic',response)
            self.assertFalse(result['valid']);self.assertIsNone(result['scores'])

    def test_two_independent_requests_and_failure_no_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            backend=OfflineResponses([{'stage':'review','content':self.response(c)} for c in CONDITIONS])
            result=evaluate(self.fixture,backend,Path(tmp))
            self.assertEqual(len(result),2);self.assertEqual(backend.calls,2)
            self.assertTrue(comparison_ready(result))
            requests=[json.loads((Path(tmp)/(c+'-request.json')).read_text()) for c in CONDITIONS]
            for req in requests:
                self.assertEqual(len(req['messages']),2)
                self.assertNotIn('预设模拟理由',req['messages'][1]['content'])
        with tempfile.TemporaryDirectory() as tmp:
            class Failed(OfflineResponses):
                def respond(self,*args,**kwargs):
                    self.calls+=1;raise BackendStopped('transport_or_usage_error')
            backend=Failed([])
            result=evaluate(self.fixture,backend,Path(tmp))
            self.assertEqual(backend.calls,1)
            self.assertEqual(result[0]['status'],'transport_stopped')
            self.assertFalse(comparison_ready(result))
            self.assertFalse((Path(tmp)/'atomic-request.json').exists())

    def test_recovery_reuses_valid_response_and_rejects_changed_fixture_or_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent=Path(tmp)
            class FailedSecond(OfflineResponses):
                def respond(inner,*args,**kwargs):
                    if inner.calls:raise BackendStopped('transport_or_usage_error')
                    return super().respond(*args,**kwargs)
            results=evaluate(self.fixture,FailedSecond([{'stage':'review','content':self.response('paragraph')}]),parent)
            (parent/'manifest.json').write_text(json.dumps({'fixture_sha256':digest(self.fixture),'case_sha256':digest(materialize(self.fixture)[0])}))
            (parent/'summary.json').write_text(json.dumps({'results':results}))
            inherited=inherit_paragraph(self.fixture,parent)
            self.assertEqual(inherited['group_scores']['correct'],8)
            with tempfile.TemporaryDirectory() as child:
                backend=OfflineResponses([{'stage':'review','content':self.response('atomic')}])
                recovered=[inherited]+evaluate(self.fixture,backend,Path(child),('atomic',))
                self.assertTrue(comparison_ready(recovered));self.assertEqual(backend.calls,1)
                self.assertFalse((Path(child)/'paragraph-request.json').exists())
            changed=deepcopy(self.fixture);changed['groups'][0]['claims'][0]['text']='changed'
            with self.assertRaises(ValueError):inherit_paragraph(changed,parent)
            (parent/'atomic-request.json').write_text('{}')
            with self.assertRaises(ValueError):inherit_paragraph(self.fixture,parent)


if __name__=='__main__': unittest.main()
