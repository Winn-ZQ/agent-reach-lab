"""保存的小红书样本→真实调度器→导出；网络由测试响应替代。"""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from analyze_xhs import execute
from collect_xhs import collect
from configure_model import MODELS, save_new
from model_gateway import ModelResponses
from research_budget import WebBudget


class SavedXhsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root/'source'
        self.output = self.root/'analysis'
        self.config = self.root/'config.json'
        save_new(self.config, {'api_key':'sk-fake-xhs-test-not-a-credential',
            'base_url':'https://dashscope.aliyuncs.com/compatible-mode/v1',
            'models':MODELS, 'free_only_user_confirmed':True, 'paid_calls_authorized':False})
        self.budget = WebBudget(self.root/'budget', self.config)
        self.budget.observe({'remaining_tokens':{m:1000000 for m in MODELS}, 'stop_when_used_up':True})
        self.sent = []
        self.acquisition_calls = []
        collect('虚构 App', self.source, self.client)

    def client(self, name, args):
        self.acquisition_calls.append(name)
        if name == 'check_login_status':
            return {'content':[{'type':'text', 'text':'已登录'}]}
        if name == 'search_feeds':
            value = {'feeds':[{'id':'a'*24, 'modelType':'note', 'xsecToken':'fixture-access-only'}]}
        else:
            value = {'data':{'note':{'noteId':'a'*24, 'title':'虚构反馈',
                     'desc':'训练提醒可以调整。', 'user':{'userId':'fixture-author'}, 'time':0}}}
        return {'content':[{'type':'text', 'text':json.dumps(value)}]}

    def send(self, config, body):
        self.sent.append(body)
        payload = json.loads(body['messages'][1]['content'])
        case = payload.get('case', payload)
        segment = case['sources'][0]['segments'][0]['segment_id']
        if 'review_targets' in payload:
            self.assertTrue(body['enable_thinking'])
            value = {'checks':[{'target_id':k, 'supported':True,
                     'reason':'预设测试判断', 'evidence_ids':[segment]} for k in payload['review_targets']]}
        else:
            self.assertFalse(body['enable_thinking'])
            value = {'answers':[{'question_id':q, 'status':'answered', 'text':'这篇笔记提到训练提醒可以调整。',
                     'refs':[{'segment_id':segment}]} for q in case['questions']],
                     'hypotheses':[], 'limitations':['仅为虚构测试样本。']}
        return {'choices':[{'message':{'content':json.dumps(value)}, 'finish_reason':'stop'}],
                'usage':{'prompt_tokens':20, 'completion_tokens':20, 'total_tokens':40}}

    def factory(self, path, sha):
        return ModelResponses(path, sha, self.root/'ledger', self.config, send=self.send)

    def test_saved_evidence_runs_once_and_settles_without_recollection(self):
        before = (self.source/'analysis-input.json').read_bytes()
        calls = list(self.acquisition_calls)
        with patch('analyze_xhs.PRIVATE', self.root):
            result = execute(self.source, self.output, budget=self.budget, backend_factory=self.factory)
            with self.assertRaises(ValueError):
                execute(self.source, self.output, budget=self.budget, backend_factory=self.factory)
        self.assertEqual(result['state']['review_status'], 'passed')
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(result['state']['api_calls'], 0)
        self.assertEqual(self.acquisition_calls, calls)
        self.assertEqual((self.source/'analysis-input.json').read_bytes(), before)
        self.assertIn('纳入 1 条笔记', (self.output/'report.md').read_text())
        self.assertNotIn('fixture-access-only', (self.output/'materials.csv').read_text())
        self.assertEqual(self.budget.status()['remaining_tokens'][MODELS[0]], 999920)
        self.assertEqual((self.output/'detail.json').stat().st_mode & 0o777, 0o600)

    def test_tampered_evidence_rejected_before_budget_or_models(self):
        p = self.source/'source-1.json'
        p.write_text(p.read_text().replace('训练提醒', '篡改提醒'))
        with patch('analyze_xhs.PRIVATE', self.root), self.assertRaises(ValueError):
            execute(self.source, self.output, budget=self.budget, backend_factory=self.factory)
        self.assertFalse(list(self.budget.root.glob('allocation-*')))
        self.assertFalse(self.sent)
        self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()
