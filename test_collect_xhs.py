"""只验证新增只读采集与证据接口；使用虚构笔记，不访问平台或模型。"""
import json
from pathlib import Path
import tempfile
import unittest

from collect_xhs import XhsError, call_tool, collect, collect_saved_searches, normalize_note, plan, verify_package
from research_flow import prepare_case
from research_exports import export_csv, export_markdown


def wrap(data):
    return {'content': [{'type': 'text', 'text': json.dumps(data, ensure_ascii=False)}]}


def note_id(i):
    return f'{i:024x}'


class XhsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / 'collection'
        self.calls = []

    def client(self, name, args):
        self.calls.append((name, args))
        if name == 'check_login_status':
            return {'content': [{'type': 'text', 'text': '✅ 已登录\n用户名: 虚构用户'}]}
        if name == 'search_feeds':
            ids = [1, 2, 3] if args['keyword'].endswith('体验') else [1, 4, 5]
            return wrap({'feeds': [{'id': note_id(i), 'modelType': 'note', 'xsecToken': 'fixture-access-only'} for i in ids]})
        self.assertEqual(name, 'get_feed_detail')
        self.assertFalse(args['load_all_comments'])
        return wrap({'feed_id': args['feed_id'], 'data': {'note': {'noteId': args['feed_id'],
                    'title': '虚构体验', 'desc': '训练提醒可以调整，界面尚需适应。',
                    'user': {'userId': 'same-author'}, 'time': 0},
                    'comments': [{'text': '不应进入分析的模拟评论'}]}})

    def test_bounded_sampling_identity_and_private_tokens(self):
        package = collect('Keep App', self.output, self.client)
        case = prepare_case(package['case'], package['run_date'])
        self.assertEqual([s['note_id'] for s in case['sources']], [note_id(i) for i in [1, 2, 4, 3]])
        self.assertEqual(len(self.calls), 7)  # 登录1+搜索2+正文4，不调用模型
        self.assertEqual(case['sampling_facts']['known_unique_authors'], 1)
        self.assertEqual(case['sampling_facts']['included_notes'], 4)
        self.assertTrue(all(s['published_at'] is None for s in case['sources']))
        self.assertNotIn('fixture-access-only', json.dumps(case))
        self.assertNotIn('不应进入分析', json.dumps(case, ensure_ascii=False))
        self.assertIn('用户陈述不等于产品事实', case['data_provenance'])
        self.assertEqual((self.output / 'analysis-input.json').stat().st_mode & 0o777, 0o600)
        before = (self.output / 'analysis-input.json').read_bytes()
        with self.assertRaises(ValueError): collect('Keep App', self.output, self.client)
        self.assertEqual(before, (self.output / 'analysis-input.json').read_bytes())
        self.assertEqual(verify_package(self.output), case)
        source = self.output / 'source-1.json'
        source.write_text(source.read_text().replace('训练提醒', '虚假改写'))
        with self.assertRaisesRegex(ValueError, 'source changed'): verify_package(self.output)

    def test_login_blocks_search(self):
        def client(name, args):
            self.calls.append(name)
            return {'content': [{'type': 'text', 'text': '❌ 未登录'}]}
        with self.assertRaisesRegex(XhsError, 'login_required'):
            collect('Keep App', self.output, client)
        self.assertEqual(self.calls, ['check_login_status'])
        self.assertFalse((self.output / 'analysis-input.json').exists())

    def test_reuse_saved_search_does_not_repeat_login_or_search(self):
        payload = self.client('search_feeds',{'keyword':'Keep App 使用体验'})
        self.calls.clear()
        package=collect_saved_searches('Keep App',self.output,[{'query':'Keep','payload':payload,
            'filters':{},'returned_at':'2026-09-27T08:00:00Z'}],client=self.client,
            selected_ids=[note_id(2)],selection_note='人工按标题选择，范围已披露')
        self.assertEqual([n for n,a in self.calls],['get_feed_detail'])
        self.assertEqual(package['case']['sampling']['queries'],['Keep'])
        self.assertIn('人工',package['case']['sampling']['selection'])
        self.assertEqual(verify_package(self.output)['sources'][0]['note_id'],note_id(2))

    def test_exports_keep_sampling_without_claiming_web_or_review_success(self):
        package = collect('Keep App', self.output, self.client)
        case = prepare_case(package['case'], package['run_date'])
        detail = {'task': {'id':'fixture', 'mode':'live_api', 'status':'stopped',
                  'input':{'region':'', 'period':''}}, 'case':case,
                  'state': {'api_calls':0, 'final_draft':None, 'review_status':'not_run'}}
        md, csv = export_markdown(detail), export_csv(detail)
        self.assertIn('纳入 4 条笔记，来自 1 名已知作者', md)
        self.assertIn('Keep App 使用问题', md)
        self.assertIn('sampling_count', csv)
        self.assertIn('尚无有效报告', md)
        self.assertNotIn('公开网页来源', md)
        self.assertNotIn('fixture-access-only', csv)

    def test_failed_source_preserves_earlier_and_stops_requests(self):
        def client(name, args):
            if name == 'get_feed_detail' and args['feed_id'] == note_id(2):
                raise XhsError('source_request_failed')
            return self.client(name, args)
        package = collect('Keep App', self.output, client)
        self.assertEqual(len(package['case']['sources']), 1)
        self.assertEqual(package['case']['sampling']['failures'][0]['reason'], 'source_request_failed')
        self.assertEqual(sum(n == 'get_feed_detail' for n, _ in self.calls), 1)

    def test_failed_second_search_keeps_first_and_records_stop(self):
        def client(name, args):
            if name == 'search_feeds' and args['keyword'].endswith('问题'):
                raise XhsError('source_timeout')
            return self.client(name, args)
        with self.assertRaisesRegex(XhsError, 'source_timeout'):
            collect('Keep App', self.output, client)
        self.assertTrue((self.output / 'search-1.json').exists())
        self.assertTrue((self.output / 'search-attempt-2.json').exists())
        self.assertEqual(json.loads((self.output / 'status.json').read_text())['reason'], 'source_timeout')
        self.assertFalse((self.output / 'analysis-input.json').exists())

    def test_mismatched_note_never_becomes_evidence(self):
        payload = wrap({'data': {'note': {'noteId': note_id(2), 'desc': '正文'}}})
        with self.assertRaisesRegex(XhsError, 'note_identity_mismatch'):
            normalize_note(payload, {'note_id': note_id(1)}, 'S1', '2026-09-27T00:00:00Z')

    def test_write_operations_rejected_before_backend(self):
        for name in ['publish_content', 'post_comment_to_feed', 'like_feed', 'delete_cookies']:
            with self.assertRaisesRegex(XhsError, 'operation_not_allowed'): call_tool(name, {})

    def test_queries_and_old_case_provenance_are_explicit(self):
        p = plan('Keep App'); self.assertEqual(p['queries'], ['Keep App 使用体验', 'Keep App 使用问题'])
        self.assertIn('负面倾向', p['bias'])
        raw = {'kind': 'synthetic-feedback', 'task': '测试', 'questions': {'Q1': '测试'},
               'sources': [{'source_id': 'S1', 'content': '虚构'}]}
        self.assertIn('虚构资料', prepare_case(raw, '2026-09-27')['data_provenance'])


if __name__ == '__main__': unittest.main()
