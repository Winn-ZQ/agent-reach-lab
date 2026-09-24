from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from research_flow import (OfflineResponses, digest, parse, prepare_case, run,
                           targets, validate_draft, validate_review)


def fixture():
    raw = {'kind': 'synthetic_fixture', 'task': '核对训练计划功能',
           'questions': {'Q1': '能否修改训练日？'},
           'sources': [{'source_id': 'S1', 'quote': '可调整每周训练日。', 'locator': 'S1-P1'}],
           'disclosure': '旧样例制作时未调用模型API。'}
    case = prepare_case(raw, '2026-09-23')
    draft = {'answers': [{'question_id': 'Q1', 'status': 'answered', 'text': '说明支持调整每周训练日。',
                         'refs': [{'source_id': 'S1', 'quote': '可调整每周训练日。', 'locator': 'S1-P1'}]}],
             'hypotheses': [], 'limitations': ['虚构数据，仅验证程序。']}
    review = {'verdict': 'pass', 'checks': [{'target_id': 'A:Q1', 'claim': '说明支持修改',
               'supported': True, 'source_ids': ['S1'], 'reason': '原文直接支持。'}],
              'issues': [], 'limitations': ['预设模拟复核，不是模型判断。']}
    return raw, case, draft, review


class ResearchFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / 'run'
        self.raw, self.case, self.draft, self.review = fixture()

    def execute(self, rows, limit=4):
        self.responses = OfflineResponses([{'stage': s, 'content': c} for s, c in rows])
        return run(self.case, self.responses, self.directory, limit)

    def revise_review(self):
        review = deepcopy(self.review)
        review['verdict'] = 'revise'
        review['checks'][0]['supported'] = False
        review['issues'] = [{'severity': 'blocking', 'description': '表述范围不清', 'action': 'rewrite'}]
        return review

    def test_direct_pass_and_independent_context(self):
        state = self.execute([('analysis', self.draft), ('review', self.review)])
        self.assertEqual(state['review_status'], 'passed')
        self.assertEqual(state['api_calls'], 0)
        request = json.loads((self.directory/'request-2.json').read_text())
        self.assertEqual([m['role'] for m in request['messages']], ['system', 'user'])
        payload = json.loads(request['messages'][1]['content'])
        self.assertEqual(set(payload), {'case', 'draft', 'review_targets'})
        self.assertEqual(state['reviews'][0]['draft_sha256'], digest(self.draft))

    def test_one_correction_then_fresh_review(self):
        repaired = deepcopy(self.draft)
        repaired['answers'][0]['text'] = '虚构帮助说明称可调整训练日；没有验证真实产品。'
        state = self.execute([('analysis', self.draft), ('review', self.revise_review()),
                              ('repair', repaired), ('review', self.review)])
        self.assertEqual((state['revision'], state['simulated_calls'], state['review_status']), (1, 4, 'passed'))
        self.assertEqual(len(state['versions']), 2)
        self.assertNotEqual(state['reviews'][0]['draft_sha256'], state['reviews'][1]['draft_sha256'])
        self.assertEqual(json.loads((self.directory/'draft-0.json').read_text()), self.draft)

    def test_bad_quote_skips_review_until_repaired(self):
        broken = deepcopy(self.draft)
        broken['answers'][0]['refs'][0]['quote'] = '可...训练日'
        state = self.execute([('analysis', broken), ('repair', self.draft), ('review', self.review)])
        self.assertEqual(state['simulated_calls'], 3)
        self.assertTrue(state['versions'][0]['validation_issues'])
        self.assertEqual(state['review_status'], 'passed')

    def test_bad_source_cannot_pass(self):
        self.review['checks'][0]['source_ids'] = ['disclosure']
        state = self.execute([('analysis', self.draft), ('review', self.review)])
        self.assertEqual((state['review_status'], state['stop_reason']), ('invalid', 'invalid_review'))
        self.assertEqual(state['final_draft'], self.draft)

    def test_invalid_json_review_stops_without_retry(self):
        state = self.execute([('analysis', self.draft), ('review', '{unfinished')])
        self.assertEqual(state['stop_reason'], 'review_invalid_response')
        self.assertEqual(state['simulated_calls'], 2)

    def test_reserve_before_analysis(self):
        state = self.execute([], limit=1)
        self.assertEqual((state['stop_reason'], state['simulated_calls']), ('budget_exhausted', 0))

    def test_reserve_before_repair_preserves_original(self):
        state = self.execute([('analysis', self.draft), ('review', self.revise_review())], limit=3)
        self.assertEqual((state['stop_reason'], state['simulated_calls']), ('budget_exhausted', 2))
        self.assertEqual(state['final_draft'], self.draft)
        self.assertEqual(state['revision'], 0)

    def test_revision_limit_delivers_unpassed_draft(self):
        repaired = deepcopy(self.draft)
        repaired['answers'][0]['text'] = '修正后的草稿'
        state = self.execute([('analysis', self.draft), ('review', self.revise_review()),
                              ('repair', repaired), ('review', self.revise_review())])
        self.assertEqual((state['stop_reason'], state['review_status']), ('revision_limit', 'revise'))
        self.assertEqual(state['final_draft'], repaired)

    def test_fetch_stops_without_inventing_sources(self):
        review = self.revise_review()
        review['issues'][0]['action'] = 'fetch'
        state = self.execute([('analysis', self.draft), ('review', review)])
        self.assertEqual((state['stop_reason'], state['simulated_calls']), ('needs_sources', 2))

    def test_no_change_does_not_loop(self):
        state = self.execute([('analysis', self.draft), ('review', self.revise_review()), ('repair', self.draft)])
        self.assertEqual(state['stop_reason'], 'no_change')
        self.assertEqual(state['simulated_calls'], 3)

    def test_existing_run_refused(self):
        self.execute([('analysis', self.draft), ('review', self.review)])
        with self.assertRaises(FileExistsError):
            run(self.case, OfflineResponses([]), self.directory)

    def test_coverage_and_hypotheses_are_reviewed(self):
        self.draft['hypotheses'] = [{'text': '提示可能改善理解', 'source_ids': ['S1'],
                                    'alternative': '也可能无需提示', 'next_action': '做用户访谈'}]
        self.assertIn('H:1', targets(self.draft))
        self.assertIn('review_target_coverage', validate_review(self.review, self.case, self.draft))
        self.review['checks'].append(deepcopy(self.review['checks'][0]))
        self.assertIn('review_target_coverage', validate_review(self.review, self.case, self.draft))

    def test_distinct_checks_may_share_a_target(self):
        self.review['checks'].append({
            'target_id': 'A:Q1', 'claim': '范围限制另需说明', 'supported': True,
            'source_ids': ['S1'], 'reason': '这是同一问题的另一项独立检查。'
        })
        self.assertNotIn('review_target_coverage', validate_review(self.review, self.case, self.draft))

    def test_false_checks_or_minor_issues_cannot_pass(self):
        self.review['checks'][0]['supported'] = False
        self.assertTrue(validate_review(self.review, self.case, self.draft))
        self.review['checks'][0]['supported'] = True
        self.review['issues'] = [{'severity': 'minor', 'description': '还有问题', 'action': 'rewrite'}]
        self.assertIn('pass_with_unresolved_issues', validate_review(self.review, self.case, self.draft))

    def test_sample_counts_author_identity_and_disclosure(self):
        demo = json.loads((Path(__file__).parent/'docs/examples/app-research/result.json').read_text())
        self.raw['sources'] = [{**s, 'source_id': s['id']} for s in demo['sources']]
        case = prepare_case(self.raw, '2026-09-23')
        facts = case['sampling_facts']
        self.assertEqual((facts['candidate_notes'], facts['included_notes'], facts['known_unique_authors']), (6, 4, 3))
        self.assertEqual(facts['same_author_groups'], [['X1', 'X2']])
        self.assertEqual(len(facts['excluded']), 2)
        self.assertNotIn('未调用模型API', json.dumps(case, ensure_ascii=False))
        self.raw['sources'][1]['author'] = None
        case = prepare_case(self.raw, '2026-09-23')
        self.assertEqual(case['sampling_facts']['unknown_author_notes'], 1)

    def test_bad_locators_exclusions_and_unsupported_answers(self):
        self.draft['answers'][0]['refs'][0]['locator'] = 'made-up'
        self.assertIn('locator_mismatch:S1', validate_draft(self.draft, self.case))
        self.draft['answers'][0]['refs'][0]['locator'] = 'S1-P1'
        self.case['sources'][0]['included'] = False
        self.assertIn('excluded_source_cited:S1', validate_draft(self.draft, self.case))
        self.draft['answers'][0]['refs'] = []
        self.assertIn('answered_without_evidence:Q1', validate_draft(self.draft, self.case))

    def test_strict_json_and_input_integrity(self):
        for text in ('{"x": 1, "x": 2}', '{"x": NaN}'):
            with self.assertRaises(ValueError):
                parse(text)
        self.raw['sources'].append(deepcopy(self.raw['sources'][0]))
        with self.assertRaises(ValueError):
            prepare_case(self.raw, '2026-09-23')
        self.raw['sources'].pop()
        self.raw['sources'][0]['content_sha256'] = 'bad-hash'
        with self.assertRaises(ValueError):
            prepare_case(self.raw, '2026-09-23')

    def test_malformed_draft_never_crashes_or_passes(self):
        broken = {'answers': [{'text': []}], 'hypotheses': [], 'limitations': []}
        state = self.execute([('analysis', broken)], limit=2)
        self.assertEqual(state['stop_reason'], 'budget_exhausted')
        self.assertEqual(state['review_status'], 'blocked_by_validation')

    def test_unknown_is_not_omitted_or_automatically_failed(self):
        self.draft['answers'][0].update(status='unknown', refs=[], text='现有材料无法确认真实效果。')
        self.review['checks'][0].update(source_ids=[], claim='合理未知')
        state = self.execute([('analysis', self.draft), ('review', self.review)])
        self.assertEqual(state['coverage'], {'answered': [], 'unknown': ['Q1'], 'omitted': []})
        self.assertEqual(state['review_status'], 'passed')

    def test_service_failure_does_not_leak_error_or_retry(self):
        responses = OfflineResponses([{'stage':'analysis', 'content':self.draft},
                                      {'stage':'review', 'simulate_failure':True}])
        state = run(self.case, responses, self.directory)
        self.assertEqual(state['simulated_calls'], 2)
        self.assertEqual(state['review_status'], 'failed')
        self.assertEqual(state['final_draft'], self.draft)

    def test_output_bound_and_no_live_sender(self):
        with self.assertRaises(ValueError):
            run(self.case, lambda *a: self.fail('must not call'), self.directory)
        self.assertFalse(self.directory.exists())
        state = self.execute([('analysis', 'x' * 32769)])
        self.assertEqual(state['stop_reason'], 'output_size_limit')
        self.assertEqual(state['simulated_calls'], 1)


if __name__ == '__main__':
    unittest.main()
