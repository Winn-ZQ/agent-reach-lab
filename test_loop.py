import json
import tempfile
import unittest
from pathlib import Path
from loop import initialize, accept_review, revise, digest, save


class LoopTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.draft = self.root / 'input.md'
        self.draft.write_text('待检查的结论', encoding='utf-8')
        self.evidence = self.root / 'source.json'
        save(self.evidence, {'content': '原始证据', 'content_sha256': digest('原始证据')})
        self.run = self.root / 'run'
        initialize(self.run, self.draft, self.evidence, 1)

    def review(self, verdict='revise'):
        state = json.loads((self.run / 'state.json').read_text())
        result = {k: state[k] for k in ('draft_sha256', 'evidence_sha256')}
        result.update(verdict=verdict,
                      checks=[{'claim': '结论', 'supported': verdict == 'pass', 'evidence_location': '第一段'}],
                      issues=[] if verdict == 'pass' else [{'description': '超出证据', 'action': 'rewrite'}])
        path = self.root / 'review.json'
        save(path, result)
        return path

    def test_pass_is_terminal(self):
        self.assertEqual(accept_review(self.run, self.review('pass'))['status'], 'passed')
        with self.assertRaises(ValueError):
            revise(self.run, self.draft, '试图继续')

    def test_limit_stops_unresolved_report(self):
        self.assertEqual(accept_review(self.run, self.review())['status'], 'needs_revision')
        self.draft.write_text('修正后仍待检查', encoding='utf-8')
        revise(self.run, self.draft, '缩小结论范围')
        self.assertEqual(accept_review(self.run, self.review())['status'], 'needs_human')
        with self.assertRaises(ValueError):
            revise(self.run, self.draft, '超限重试')

    def test_stale_review_rejected(self):
        old = self.review()
        accept_review(self.run, old)
        self.draft.write_text('新版本', encoding='utf-8')
        revise(self.run, self.draft, '修改')
        with self.assertRaises(ValueError):
            accept_review(self.run, old)

    def test_pass_with_issues_rejected(self):
        path = self.review()
        review = json.loads(path.read_text())
        review['verdict'] = 'pass'
        save(path, review)
        with self.assertRaises(ValueError):
            accept_review(self.run, path)

    def test_no_change_rejected(self):
        accept_review(self.run, self.review())
        with self.assertRaises(ValueError):
            revise(self.run, self.draft, '没有实际改动')

    def test_tampered_archive_rejected(self):
        (self.run / 'draft-0.md').write_text('偷偷修改', encoding='utf-8')
        with self.assertRaises(ValueError):
            accept_review(self.run, self.review('pass'))


if __name__ == '__main__':
    unittest.main()
