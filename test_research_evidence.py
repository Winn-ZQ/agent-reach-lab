import json
import hashlib
import tempfile
import unittest
from pathlib import Path

from research_evidence import build_evidence, handoff_preview, verify_package, question_checklist
from research_acquisition import collect_candidates


class ResearchEvidenceTests(unittest.TestCase):
    def make_fixture(self):
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        search={'status':'searched','backend':'synthetic_test','query':'虚构测试问题','objective':'测试证据契约',
                'searched_at':'2026-09-23T12:42:00+00:00',
                'results':[{'rank':n,'url':f'https://example.com/{n}','title':f'虚构来源{n}'} for n in range(1,4)]}
        (root/'search.json').write_text(json.dumps(search))
        def collector(url,**kwargs):
            body='虚构测试正文，仅用于验证哈希和来源边界。'
            return {'url':url,'status':'fetched_unverified','error':None,'content':body,
                    'content_sha256':hashlib.sha256(body.encode()).hexdigest(),
                    'fetched_at':'2026-09-23T12:42:00+00:00'}
        collect_candidates(search,root/'sources',collector=collector)
        (root / 'run.json').write_text(json.dumps({
            'id': 'a' * 32, 'status': 'completed',
            'input': {'question': '电梯预测性维保有哪些公开资料？', 'region': '中国大陆',
                      'period': '2026', 'sources': ['web']},
            'created_at': '2026-09-23T12:42:00+00:00'
        }, ensure_ascii=False))
        return temp, root

    def test_build_evidence_assigns_stable_source_ids_and_unknown_limits(self):
        temp, root = self.make_fixture()
        try:
            package = build_evidence(root)
            self.assertEqual(package['analysis_status'], 'not_run')
            self.assertEqual([s['source_id'] for s in package['case']['sources']], ['S1', 'S2', 'S3'])
            self.assertEqual(package['case']['web_context']['region'], '中国大陆')
            self.assertIn('发布日期', package['case']['web_context']['limitations'][2])
            self.assertEqual(verify_package(root, package)['kind'], 'collected_public_web')
            preview = handoff_preview({'status': 'prepared', **package,
                                      'preflight': {'budget_status': 'new_budget_required'}})
            self.assertEqual(preview['api_calls'], 0)
            self.assertEqual([role['id'] for role in preview['roles']], ['analysis', 'review', 'repair'])
            self.assertEqual(preview['roles'][1]['status'], 'waiting_for_analysis')
        finally:
            temp.cleanup()

    def test_question_checklist_keeps_separate_conditions_and_full_tail(self):
        q='某App如何离线？哪些设备或套餐可用，哪些操作需要联网？请区分官方说明和未知。'
        questions=question_checklist(q)
        self.assertEqual(len(questions),5)
        self.assertEqual(questions['Q2'],'哪些设备或套餐可用')
        self.assertEqual(questions['Q3'],'哪些操作需要联网？')
        long_question=''.join(f'问题{i}？' for i in range(15))
        result=question_checklist(long_question)
        self.assertEqual(len(result),9)
        self.assertTrue(all(f'问题{i}？' in ' '.join(result.values()) for i in range(15)))

    def test_legacy_packages_keep_hash_while_new_packages_split_questions(self):
        temp,root=self.make_fixture()
        try:
            task_path=root/'run.json';task=json.loads(task_path.read_text())
            task['input']['question']='如何离线？哪些设备支持？'
            task_path.write_text(json.dumps(task))
            old=build_evidence(root,'evidence-package/0.1')
            self.assertEqual(len(verify_package(root,old)['questions']),2)
            new=build_evidence(root)
            self.assertEqual(len(verify_package(root,new)['questions']),3)
            self.assertNotEqual(old['case_sha256'],new['case_sha256'])
            self.assertEqual(old,build_evidence(root,'evidence-package/0.1'))
            with self.assertRaises(ValueError):build_evidence(root,'unknown-version')
        finally:temp.cleanup()

    def test_changed_body_hash_blocks_analysis_input(self):
        temp, root = self.make_fixture()
        try:
            path = root / 'sources/source-1.json'
            record = json.loads(path.read_text())
            record['content'] += '\n被篡改'
            path.write_text(json.dumps(record, ensure_ascii=False))
            with self.assertRaises(ValueError) as context:
                build_evidence(root)
            self.assertIn('哈希', str(context.exception))
        finally:
            temp.cleanup()


if __name__ == '__main__':
    unittest.main()
