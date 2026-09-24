import json
from pathlib import Path
import tempfile
import unittest

from configure_model import MODELS
from model_smoke import append_event
from model_trial import MAX_CALLS, Trial, numeric_usage, packets, validate


class TrialTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = {'api_key': 'sk-fake-test-not-real'}
        self.case = {'sources': [{'source_id': 'S1', 'quote': '原文内容'}], 'questions': {'Q1': '问题'}}
        self.messages = [{'role': 'system', 'content': 'test'}, {'role': 'user', 'content': json.dumps(self.case)}]
        self.good = {'answers': [{'question_id': 'Q1', 'status': 'answered', 'text': '内容',
                                 'refs': [{'source_id': 'S1', 'quote': '原文', 'locator': 'P1'}]}],
                     'hypotheses': [], 'limitations': []}

    def tearDown(self):
        self.temp.cleanup()

    def send(self, config, body):
        self.assertEqual(json.loads((self.root / 'events.jsonl').read_text().splitlines()[-1])['event'], 'started')
        return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(self.good)}}],
                'model': body['model'], 'usage': {'prompt_tokens': 10, 'completion_tokens': 10, 'total_tokens': 20}}

    def test_real_input_integrity(self):
        self.assertEqual(set(packets()), {'web-facts', 'app-feedback', 'review-trap'})

    def test_quotes_references_and_coverage(self):
        self.assertEqual(validate(self.good, self.case, 'analysis'), [])
        self.good['answers'][0]['refs'][0]['quote'] = '编造引文'
        self.assertTrue(any('quote_not_literal' in s for s in validate(self.good, self.case, 'analysis')))
        self.good['answers'] = []
        self.assertIn('omitted_question', validate(self.good, self.case, 'analysis'))

    def test_contradictory_pass(self):
        review = {'verdict': 'pass', 'checks': [{'claim': 'x', 'supported': False, 'source_ids': ['S1'], 'reason': '无据'}], 'issues': [], 'limitations': []}
        self.assertIn('contradictory_pass', validate(review, self.case, 'review'))

    def test_prewrite_and_no_replay(self):
        trial = Trial(self.config, self.root, self.send)
        trial.call(MODELS[0], 'case', 'analysis', self.messages, True)
        with self.assertRaises(ValueError):
            trial.call(MODELS[0], 'case', 'analysis', self.messages)
        self.assertNotIn(self.config['api_key'], (self.root/'events.jsonl').read_text())

    def test_usage_unknown_stops_followups(self):
        trial = Trial(self.config, self.root, lambda *args: {'choices': []})
        with self.assertRaises(ValueError):
            trial.call(MODELS[0], 'case', 'analysis', self.messages)
        with self.assertRaises(ValueError):
            trial.call(MODELS[1], 'case', 'analysis', self.messages)
        self.assertEqual(len(trial.history()), 2)

    def test_json_mode_and_final_call_limit(self):
        def send(config, body):
            self.assertEqual(body['response_format'], {'type': 'json_object'})
            return self.send(config, body)
        trial = Trial(self.config, self.root, send)
        for i in range(MAX_CALLS-1):
            append_event(trial.log, {'event': 'started', 'job': str(i), 'model': MODELS[0]})
            append_event(trial.log, {'event': 'finished', 'job': str(i), 'model': MODELS[0], 'status': 'ok', 'usage': {'total_tokens': 1}})
        trial.call(MODELS[0], 'case', 'analysis', self.messages, json_mode=True)
        with self.assertRaises(ValueError):
            trial.call(MODELS[0], 'another', 'analysis', self.messages)

    def test_reserves_review_call(self):
        trial = Trial(self.config, self.root, lambda *args: self.fail('network'))
        for i in range(MAX_CALLS-1):
            append_event(trial.log, {'event': 'started', 'job': str(i), 'model': MODELS[0]})
            append_event(trial.log, {'event': 'finished', 'job': str(i), 'model': MODELS[0], 'status': 'ok', 'usage': {'total_tokens': 1}})
        with self.assertRaises(ValueError):
            trial.call(MODELS[1], 'case', 'analysis', self.messages, True)

    def test_token_guard_and_model_allowlist(self):
        trial = Trial(self.config, self.root, lambda *args: self.fail('network'))
        with self.assertRaises(ValueError):
            trial.call('unknown', 'case', 'analysis', self.messages)
        with self.assertRaises(ValueError):
            trial.call(MODELS[0], 'case', 'analysis', [{'role': 'user', 'content': 'x'*130000}])

    def test_numeric_usage_not_boolean_or_inconsistent(self):
        for raw in ({}, {'prompt_tokens': True, 'completion_tokens': 1, 'total_tokens': 2},
                    {'prompt_tokens': 5, 'completion_tokens': 1, 'total_tokens': 1}):
            with self.assertRaises(ValueError):
                numeric_usage(raw)

    def test_reconciliation_is_counted_not_zeroed(self):
        trial = Trial(self.config, self.root, lambda *args: self.fail('network'))
        append_event(trial.log, {'event': 'started', 'job': 'old', 'model': MODELS[0]})
        append_event(trial.log, {'event': 'finished', 'job': 'old', 'model': MODELS[0], 'status': 'failed', 'usage': None})
        append_event(trial.log, {'event': 'reconciled', 'job': 'old', 'free_only_switch_observed': True, 'accounted_token_upper_bound': 299999})
        with self.assertRaises(ValueError):
            trial.call(MODELS[0], 'new', 'analysis', self.messages)

    def test_response_retained_when_usage_validation_fails(self):
        trial = Trial(self.config, self.root, lambda *args: {'usage': {'total_tokens': 99}, 'choices': []})
        with self.assertRaises(ValueError):
            trial.call(MODELS[0], 'case', 'analysis', self.messages)
        event = trial.history()[-1]
        self.assertTrue(event['response_received'])
        self.assertEqual(event['reported_usage_fields'], {'total_tokens': 99})
        self.assertEqual(event['failure_category'], 'response_validation')
        self.assertTrue((self.root/(MODELS[0]+'--case--analysis.response.json')).exists())


if __name__ == '__main__':
    unittest.main()
