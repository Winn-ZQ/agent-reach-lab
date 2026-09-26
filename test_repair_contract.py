from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from repair_contract import build_plan,apply_patch,check_repair_review,resolution_evidence
from research_flow import OfflineResponses,run
from test_research_flow import fixture

class RepairContractTests(unittest.TestCase):
 def setUp(self):
  _,self.case,self.draft,self.review=fixture()
  self.refs=[{'source_id':'S1','quote':'可调整每周训练日。'}]
  self.review.update(schema_version='research-review/0.2',verdict='revise',issues=[{'target_id':'A:Q1','severity':'blocking','action':'rewrite','description':'说明只代表给定资料，非实测。'}])
  self.review['checks'][0].update(supported=False,basis='source',refs=self.refs)
  self.plan=build_plan(self.draft,self.review)
  replacement=deepcopy(self.draft['answers'][0]);replacement['text']='给定资料称可调整每周训练日，未实测。'
  self.patch={'changes':[{'target_id':'A:Q1','replacement':replacement}], 'resolutions':[{'issue_id':'I1','resolution':'changed','reason':'收窄为给定资料说明。','refs':self.refs}]}
 def test_patch_preserves_original_and_unaffected_fields(self):
  before=deepcopy(self.draft);result,delta=apply_patch(self.draft,self.patch,self.plan,self.case)
  self.assertEqual(self.draft,before);self.assertEqual(result['limitations'],before['limitations'])
  self.assertEqual(delta[0]['before'],before['answers'][0]);self.assertEqual(result['answers'][0],delta[0]['after'])
 def test_unrelated_or_citation_only_change_cannot_claim_resolved(self):
  for mode in ('no_change','whitespace','refs_only'):
   patch=deepcopy(self.patch);patch['changes'][0]['replacement']=deepcopy(self.draft['answers'][0])
   if mode=='whitespace':patch['changes'][0]['replacement']['text']+=' \n'
   if mode=='refs_only':patch['changes'][0]['replacement']['refs']=[]
   patch['limitations']=['别处被修改，不代表目标已修改。']
   with self.subTest(mode=mode),self.assertRaisesRegex(ValueError,'repair_target_unchanged'):apply_patch(self.draft,patch,self.plan,self.case)
 def test_missing_duplicate_or_unknown_issue_rejected(self):
  for receipts in ([],self.patch['resolutions']*2,[dict(self.patch['resolutions'][0],issue_id='I9')]):
   patch=deepcopy(self.patch);patch['resolutions']=receipts
   with self.assertRaises(ValueError):apply_patch(self.draft,patch,self.plan,self.case)
 def test_cannot_overwrite_unknown_targets_or_identity(self):
  for changes in ([dict(self.patch['changes'][0],target_id='A:Q9')],self.patch['changes']*2,
                  [{'target_id':'A:Q1','replacement':dict(self.patch['changes'][0]['replacement'],question_id='Q9')}]):
   with self.assertRaises(ValueError):apply_patch(self.draft,dict(self.patch,changes=changes),self.plan,self.case)
 def test_dispute_requires_real_evidence_and_independent_review(self):
  patch={'changes':[],'resolutions':[dict(self.patch['resolutions'][0],resolution='disputed')]}
  result,_=apply_patch(self.draft,patch,self.plan,self.case);self.assertEqual(result,self.draft)
  for refs in ([],[{'source_id':'S1','quote':'编造依据'}]):
   patch['resolutions'][0]['refs']=refs
   with self.assertRaises(ValueError):apply_patch(self.draft,patch,self.plan,self.case)
 def test_changed_receipt_may_reuse_only_its_verified_replacement_refs(self):
  patch=deepcopy(self.patch);patch['resolutions'][0]['refs']=[]
  result,_=apply_patch(self.draft,patch,self.plan,self.case)
  receipts=resolution_evidence(patch,self.plan,result)
  self.assertEqual(receipts[0]['evidence_origin'],'validated_replacement_refs')
  self.assertEqual(receipts[0]['refs'],result['answers'][0]['refs'])
  self.assertEqual(patch['resolutions'][0]['refs'],[])
  patch['changes'][0]['replacement']['refs'][0]['quote']='伪造的回答引文'
  with self.assertRaises(ValueError):apply_patch(self.draft,patch,self.plan,self.case)
 def test_final_review_must_cover_each_resolution_and_cannot_pass_unresolved(self):
  final=deepcopy(self.review);final.update(verdict='pass',issues=[])
  self.assertEqual(check_repair_review(final,self.plan,self.case),['repair_recheck_coverage'])
  final['repair_checks']=[{'issue_id':'I1','resolved':False,'reason':'仍未消除','refs':self.refs}]
  self.assertEqual(check_repair_review(final,self.plan,self.case),['repair_recheck_unresolved'])
  final['repair_checks'][0]['resolved']=True
  self.assertEqual(check_repair_review(final,self.plan,self.case),[])
  final['repair_checks'][0]['refs'][0]['quote']='伪造';self.assertTrue(check_repair_review(final,self.plan,self.case))
 def test_live_contract_flow_persists_changes_and_rechecks(self):
  final=deepcopy(self.review);final.update(verdict='pass',issues=[])
  final['checks'][0]['supported']=True
  final['repair_checks']=[{'issue_id':'I1','resolved':True,'reason':'已限定为资料说法','refs':self.refs}]
  backend=OfflineResponses([{'stage':'review','content':self.review},{'stage':'repair','content':self.patch},{'stage':'review','content':final}]);backend.mode='live_api'
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp)/'flow';s=run(self.case,backend,path,call_limit=3,initial_draft=self.draft)
   self.assertEqual(s['review_status'],'passed');self.assertEqual(backend.calls,3)
   req=json.loads((path/'request-3.json').read_text());ctx=json.loads(req['messages'][1]['content'])['revision_context']
   self.assertEqual(ctx['changes'][0]['after'],s['final_draft']['answers'][0])
   self.assertEqual(ctx['repair_plan'],self.plan);self.assertTrue((path/'repair-patch.json').exists())
 def test_no_actual_target_change_stops_before_wasting_review(self):
  patch=deepcopy(self.patch);patch['changes']=[]
  backend=OfflineResponses([{'stage':'review','content':self.review},{'stage':'repair','content':patch}]);backend.mode='live_api'
  with tempfile.TemporaryDirectory() as tmp:
   s=run(self.case,backend,Path(tmp)/'flow',call_limit=3,initial_draft=self.draft)
   self.assertEqual(s['stop_reason'],'repair_target_unchanged');self.assertEqual(backend.calls,2)
   self.assertEqual(s['final_draft'],self.draft);self.assertNotEqual(s['review_status'],'passed')
 def test_missing_patch_evidence_gets_one_bounded_format_repair(self):
  bad=deepcopy(self.patch);bad['resolutions'][0]['refs']=[];bad['changes'][0]['replacement']['refs']=[]
  final=deepcopy(self.review);final.update(verdict='pass',issues=[]);final['checks'][0]['supported']=True
  final['repair_checks']=[{'issue_id':'I1','resolved':True,'reason':'已修正','refs':self.refs}]
  for suffix,repair_again in [('success',self.patch),('failure',bad)]:
   backend=OfflineResponses([{'stage':'review','content':self.review},{'stage':'repair','content':bad},{'stage':'repair','content':repair_again},{'stage':'review','content':final}]);backend.mode='live_api'
   with tempfile.TemporaryDirectory() as tmp:
    s=run(self.case,backend,Path(tmp)/suffix,call_limit=4,initial_draft=self.draft)
    self.assertEqual(s['format_repairs'],1)
    self.assertEqual(s['stop_reason'],'review_passed' if suffix=='success' else 'repair_resolution_evidence')
    self.assertEqual(backend.calls,4 if suffix=='success' else 3)
 def test_later_format_repair_cannot_revert_an_acknowledged_change(self):
  patch=deepcopy(self.patch);patch['changes'][0]['replacement']['refs'][0]['locator']='wrong'
  # 定位标签可由程序绑定；另用不存在的有效JSON字段触发真正的草稿格式校验。
  patch['changes'][0]['replacement']['status']='invalid'
  backend=OfflineResponses([{'stage':'review','content':self.review},{'stage':'repair','content':patch},
      {'stage':'repair','content':self.draft}]);backend.mode='live_api'
  with tempfile.TemporaryDirectory() as tmp:
   s=run(self.case,backend,Path(tmp)/'flow',call_limit=4,initial_draft=self.draft)
   self.assertEqual(s['stop_reason'],'repair_target_unchanged');self.assertEqual(backend.calls,3)

if __name__=='__main__':unittest.main()
