import json
import shutil
import tempfile
import unittest
from pathlib import Path

from research_evidence import build_evidence, handoff_preview, verify_package


FIXTURE = Path(__file__).parent / 'runs/public-web-elevator-2026-09-23'


class ResearchEvidenceTests(unittest.TestCase):
    def make_fixture(self):
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        shutil.copy(FIXTURE / 'search.json', root / 'search.json')
        shutil.copytree(FIXTURE / 'sources', root / 'sources')
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
