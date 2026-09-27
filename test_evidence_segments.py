from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from evidence_segments import catalog, present_case, recover_case, bind_response, check_disputed_changes
from research_flow import OfflineResponses, run, validate_draft, validate_review
from repair_contract import build_plan
from test_research_flow import fixture


class SegmentTests(unittest.TestCase):
    def setUp(self):
        _, self.case, self.draft, self.review = fixture()
        self.segments = catalog(self.case)
        self.ref = {'segment_id': self.segments[0]['segment_id']}
        self.raw_draft = deepcopy(self.draft)
        self.raw_draft['answers'][0]['refs'] = [self.ref]
        self.raw_review = deepcopy(self.review)
        self.raw_review['schema_version'] = 'research-review/0.2'
        check = self.raw_review['checks'][0]
        check.pop('source_ids')
        check.update(basis='source', refs=[self.ref], condition_check=dict(
            status='none_found', reason='未发现影响所问功能的其他条件。', refs=[]))

    def test_full_text_preserved_including_tables_boundaries_and_excluded_source(self):
        body = '| 功能 | 免费 |\n| 导出 | ✓ |\n' + '中' * 2900 + '\n例外：团队需付费。\n'
        self.case['sources'][0]['quote'] = body
        self.case['sources'].append(dict(source_id='S2', quote='排除资料', included=False))
        items = catalog(self.case)
        self.assertEqual(''.join(s['text'] for s in items if s['source_id'] == 'S1'), body)
        self.assertTrue(all(s['text'] == body[s['start']:s['end']] for s in items if s['source_id'] == 'S1'))
        self.assertEqual(len({s['segment_id'] for s in items}), len(items))
        shown = present_case(self.case, items)
        self.assertNotIn('quote', shown['sources'][0])
        self.assertEqual(''.join(s['text'] for s in shown['sources'][0]['segments']), body)
        self.assertFalse(shown['sources'][1]['included'])
        self.assertEqual(recover_case(shown), self.case)

    def test_presented_evidence_cannot_change_ids_hash_or_text(self):
        for field in ('text', 'segment_id', 'catalog_sha256'):
            shown = present_case(self.case, self.segments)
            if field == 'catalog_sha256': shown['reference_contract'][field] = 'fake'
            else: shown['sources'][0]['segments'][0][field] += 'fake'
            with self.assertRaisesRegex(ValueError, 'segment_input_mismatch'):recover_case(shown)

    def test_bound_draft_matches_original_and_keeps_raw_untouched(self):
        raw = deepcopy(self.raw_draft)
        bound, audit = bind_response(raw, 'analysis', self.case, self.segments)
        self.assertEqual(bound, self.draft)
        self.assertEqual(raw, self.raw_draft)
        self.assertEqual(validate_draft(bound, self.case), [])
        self.assertEqual(audit[0]['path'], 'answers/0')

    def test_forged_mixed_duplicate_excluded_and_stale_refs_rejected(self):
        for refs in ([{'segment_id':'invented'}], [dict(self.ref, quote='伪造')],
                     [self.ref, self.ref], [{'source_id':'S1','quote':'可调整每周训练日。'}]):
            raw = deepcopy(self.raw_draft); raw['answers'][0]['refs'] = refs
            with self.assertRaises(ValueError):
                bind_response(raw, 'analysis', self.case, self.segments)
        excluded = deepcopy(self.case); excluded['sources'][0]['included'] = False
        with self.assertRaises(ValueError):
            bind_response(self.raw_draft, 'analysis', excluded, catalog(excluded))
        changed = deepcopy(self.case); changed['sources'][0]['quote'] += '新增条件'
        with self.assertRaisesRegex(ValueError, 'catalog_mismatch'):
            bind_response(self.raw_draft, 'analysis', changed, self.segments)
        with self.assertRaises(ValueError):
            bind_response(self.raw_draft, 'analysis', changed, catalog(changed))

    def test_missing_condition_cannot_pass_or_lack_evidence(self):
        for supported, verdict, refs in ((True, 'pass', [self.ref]), (False, 'revise', [])):
            raw = deepcopy(self.raw_review)
            raw['verdict'] = verdict
            raw['checks'][0].update(supported=supported, condition_check=dict(status='missing', reason='条件漏写', refs=refs))
            with self.assertRaises(ValueError):
                bind_response(raw, 'review', self.case, self.segments)
        raw['checks'][0]['condition_check']['refs'] = [self.ref]
        raw['issues'] = [dict(target_id='A:Q1',severity='blocking',description='条件遗漏',action='rewrite')]
        bound, _ = bind_response(raw, 'review', self.case, self.segments)
        self.assertEqual(validate_review(bound, self.case, self.draft, True), [])

    def test_condition_and_source_identity_cannot_be_silently_dropped(self):
        for mutation in ('missing_condition', 'conflicting_source'):
            raw = deepcopy(self.raw_review)
            if mutation == 'missing_condition':raw['checks'][0].pop('condition_check')
            else:raw['checks'][0]['source_ids'] = ['S2']
            with self.assertRaises(ValueError):bind_response(raw, 'review', self.case, self.segments)

    def test_literal_binding_is_not_a_semantic_correctness_claim(self):
        raw = deepcopy(self.raw_draft)
        raw['answers'][0]['text'] = '每周训练日永远不能调整。'
        bound, _ = bind_response(raw, 'analysis', self.case, self.segments)
        self.assertEqual(bound['answers'][0]['text'], raw['answers'][0]['text'])
        self.assertEqual(validate_draft(bound, self.case), [])  # 语义错误留给复核与人工评测。

    def test_full_flow_records_original_bound_and_catalog_without_mutating_case(self):
        before = deepcopy(self.case)
        backend = OfflineResponses([dict(stage='analysis',content=self.raw_draft),
                                    dict(stage='review',content=self.raw_review)])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'run'
            state = run(self.case, backend, path, reference_mode='segments-v1')
            self.assertEqual(state['review_status'], 'passed')
            self.assertEqual(state['reference_mode'], 'segments-v1')
            self.assertEqual(json.loads((path/'response-1.txt').read_text()), self.raw_draft)
            self.assertEqual(json.loads((path/'bound-response-1.json').read_text()), self.draft)
            self.assertTrue((path/'reference-bindings-2.json').exists())
            self.assertTrue((path/'evidence-segments.json').exists())
            payload = json.loads(json.loads((path/'request-2.json').read_text())['messages'][1]['content'])
            self.assertEqual(payload['draft'], self.draft)
            self.assertNotIn('quote', payload['case']['sources'][0])
        self.assertEqual(self.case, before)

    def test_bad_ref_stops_without_an_extra_model_call(self):
        raw = deepcopy(self.raw_review); raw['checks'][0]['refs'] = [{'segment_id':'fake'}]
        backend = OfflineResponses([dict(stage='review',content=raw)])
        with tempfile.TemporaryDirectory() as tmp:
            state = run(self.case, backend, Path(tmp)/'run', initial_draft=self.draft, reference_mode='segments-v1')
            self.assertNotEqual(state['review_status'], 'passed')
            self.assertEqual(backend.calls, 1)
            self.assertIn('segment_ref_', state['stop_reason'])

    def test_repair_cannot_dispute_and_silently_change_same_target(self):
        review = deepcopy(self.review)
        review['issues'] = [dict(target_id='A:Q1', severity='blocking', description='误报', action='rewrite')]
        plan = build_plan(self.draft, review)
        patch = dict(changes=[], resolutions=[dict(issue_id='I1',resolution='disputed',reason='原文支持旧稿',
            refs=[self.ref],review_assessment=dict(decision='dispute',reason='核对来源后原意见不成立',refs=[self.ref]))])
        bound, _ = bind_response(patch, 'repair', self.case, self.segments, patch=True)
        check_disputed_changes(bound, plan, self.draft)
        replacement = deepcopy(self.draft['answers'][0]); replacement['text'] = '错误地改为不能调整'
        bound['changes'] = [dict(target_id='A:Q1',replacement=replacement)]
        with self.assertRaisesRegex(ValueError, 'disputed_target_changed'):
            check_disputed_changes(bound, plan, self.draft)
        patch['resolutions'][0]['review_assessment']['decision'] = 'accept'
        with self.assertRaisesRegex(ValueError, 'repair_assessment'):
            bind_response(patch, 'repair', self.case, self.segments, patch=True)

    def test_repair_recheck_covers_refs_and_dispute_without_changing_answer(self):
        first = deepcopy(self.raw_review); first.update(verdict='revise',issues=[dict(
            target_id='A:Q1', severity='blocking',description='错误认为功能不支持',action='rewrite')])
        first['checks'][0]['supported'] = False
        patch = dict(changes=[], resolutions=[dict(issue_id='I1',resolution='disputed',reason='旧稿与原文一致',
            refs=[self.ref],review_assessment=dict(decision='dispute',reason='原文明确支持调整',refs=[self.ref]))])
        final = deepcopy(self.raw_review)
        final['repair_checks'] = [dict(issue_id='I1',resolved=True,reason='原复核误读，旧稿正确',refs=[self.ref])]
        backend = OfflineResponses([dict(stage='review',content=first),dict(stage='repair',content=patch),dict(stage='review',content=final)])
        backend.mode = 'live_api'  # 触发真实路径契约，响应仍为模拟。
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'run'
            state = run(self.case, backend, path, initial_draft=self.draft,call_limit=3,reference_mode='segments-v1')
            self.assertEqual(state['review_status'], 'passed')
            self.assertEqual(state['final_draft'], self.draft)
            self.assertEqual(backend.calls, 3)
            self.assertTrue((path/'reference-bindings-3.json').exists())


if __name__ == '__main__':
    unittest.main()
