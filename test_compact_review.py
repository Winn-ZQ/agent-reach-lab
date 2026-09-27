from copy import deepcopy
import json
import csv
import io
from pathlib import Path
import tempfile
import unittest

import compact_review as compact
from evidence_segments import catalog, recover_case
from research_flow import OfflineResponses, run, validate_review
from repair_contract import apply_patch, build_plan
from test_research_flow import fixture
from thinking_benchmark import messages
from research_exports import export_csv, export_markdown


class CompactReviewTests(unittest.TestCase):
    def setUp(self):
        _, self.case, self.draft, _ = fixture()
        self.segments = catalog(self.case)
        self.sid = self.segments[0]['segment_id']
        self.raw = {'checks': [dict(target_id='A:Q1', supported=True, reason='原文支持调整。', evidence_ids=[self.sid]),
                               dict(target_id='L:1', supported=True, reason='如实披露虚构范围。', evidence_ids=[])]}

    def bind(self, raw=None, draft=None, plan=None):
        return compact.normalize_review(raw or self.raw, self.case, draft or self.draft, self.segments, plan)

    def test_correct_review_derives_fields_without_extra_model_judgments(self):
        bound, audit = self.bind()
        self.assertEqual(bound['verdict'], 'pass')
        self.assertEqual(bound['checks'][0]['claim'], self.draft['answers'][0]['text'])
        self.assertEqual(bound['checks'][1]['basis'], 'evidence_gap')
        self.assertEqual(validate_review(bound, self.case, self.draft, True, True), [])
        self.assertEqual(len(audit), 1)
        self.assertNotIn('verdict', self.raw)

    def test_exact_coverage_includes_limitations_unknown_and_duplicates_rejected(self):
        for checks in (self.raw['checks'][:1], self.raw['checks'] * 2,
                       self.raw['checks'] + [dict(self.raw['checks'][1], target_id='L:2')]):
            with self.assertRaises(ValueError): self.bind({'checks': checks})

    def test_false_is_preserved_and_forged_or_empty_fact_evidence_cannot_pass(self):
        raw = deepcopy(self.raw); raw['checks'][1].update(supported=False, reason='限制说明夹带无依据事实。')
        bound, _ = self.bind(raw)
        self.assertEqual(bound['verdict'], 'revise')
        self.assertEqual(bound['issues'][0]['target_id'], 'L:1')
        for ids in ([], ['fake'], [self.sid, self.sid]):
            raw = deepcopy(self.raw); raw['checks'][0]['evidence_ids'] = ids
            with self.assertRaises(ValueError): self.bind(raw)
        unknown = deepcopy(self.draft); unknown['answers'][0].update(status='unknown', refs=[])
        raw['checks'][0]['evidence_ids'] = []
        self.assertEqual(self.bind(raw, unknown)[0]['verdict'], 'pass')

    def test_focus_preserves_full_source_and_does_not_receive_gold(self):
        payload = dict(case=self.case, draft=self.draft, review_targets={})
        shown = compact.review_payload(payload, self.segments)
        self.assertEqual(recover_case(shown['case']), self.case)
        self.assertEqual(shown, compact.review_payload(payload, self.segments))
        modified = dict(payload, expected_supported='SECRET TEST ANSWER')
        self.assertEqual(messages(payload, compact.MODE), messages(modified, compact.MODE))

    def test_limitation_can_be_fixed_and_rechecked_without_mutating_other_targets(self):
        original = deepcopy(self.draft); original['limitations'] = ['免费版一定永久免费。']
        first = deepcopy(self.raw); first['checks'][1].update(supported=False, reason='来源没有永久免费承诺。')
        patch = dict(changes=[dict(target_id='L:1', replacement={'text':'所给来源未说明收费及未来政策。'})],
                     resolutions=[dict(issue_id='I1', resolution='changed', reason='删除资料外承诺，明确未知。', refs=[{'segment_id':self.sid}])])
        final = deepcopy(self.raw)
        final['repair_checks'] = [dict(issue_id='I1', resolved=True, reason='已去掉无依据承诺。', evidence_ids=[self.sid])]
        backend = OfflineResponses([dict(stage='review', content=first), dict(stage='repair', content=patch),
                                    dict(stage='review', content=final)])
        backend.mode = 'live_api'
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'run'
            state = run(self.case, backend, path, call_limit=3, initial_draft=original, reference_mode=compact.MODE)
            self.assertEqual(state['review_status'], 'passed', state['stop_reason'])
            self.assertEqual(state['final_draft']['answers'], original['answers'])
            self.assertEqual(state['final_draft']['limitations'], ['所给来源未说明收费及未来政策。'])
            self.assertEqual(json.loads((path/'draft-0.json').read_text()), original)
            self.assertEqual(backend.calls, 3)
            self.assertTrue((path/'reference-bindings-3.json').exists())
            detail = dict(case=self.case, state=state, task=dict(id='compact-test',mode='live_api',
                status='completed',input=dict(region='',period='')))
            rows = list(csv.DictReader(io.StringIO(export_csv(detail).lstrip('\ufeff'))))
            limits = [r for r in rows if r['record_type'] == 'limitation']
            self.assertEqual([(r['id'],r['text']) for r in limits], [('L:1','所给来源未说明收费及未来政策。')])
            self.assertIn(limits[0]['text'], export_markdown(detail))
            self.assertNotIn('永久免费', export_csv(detail))

    def test_limitation_ids_cannot_be_replaced_wholesale_and_unresolved_cannot_pass(self):
        first = deepcopy(self.raw); first['checks'][1]['supported'] = False
        review, _ = self.bind(first); plan = build_plan(self.draft, review, True)
        final = deepcopy(self.raw)
        final['repair_checks'] = [dict(issue_id='I1', resolved=False, reason='仍未解决', evidence_ids=[self.sid])]
        with self.assertRaisesRegex(ValueError, 'recheck_conflict'): self.bind(final, plan=plan)
        patch = dict(changes=[], resolutions=[dict(issue_id='I1', resolution='disputed', reason='原稿正确',
            refs=[{'source_id':'S1', 'quote':self.segments[0]['text']}])], limitations=['静默覆盖'])
        with self.assertRaisesRegex(ValueError, 'limitations_overwrite'):
            apply_patch(self.draft, patch, plan, self.case, True)


if __name__ == '__main__':
    unittest.main()
