import unittest
from copy import deepcopy
from thinking_benchmark import examples, messages, score


class ThinkingBenchmarkTests(unittest.TestCase):
    def test_gold_not_sent_and_positive_is_valid_control(self):
        from research_flow import validate_draft, encoded
        for name,payload,gold in examples():
            if name == 'positive':
                self.assertEqual(validate_draft(payload['draft'],payload['case']),[])
            changed=deepcopy(payload);changed['expected_supported']='SECRET ANSWER'
            self.assertEqual(messages(payload),messages(changed))
            self.assertNotIn('expected_supported',encoded(messages(payload)))
        self.assertTrue(all(examples()[1][2].values()))

    def test_invalid_review_gets_no_semantic_credit(self):
        _,payload,gold=examples()[1]
        for response in ({}, [], None):
            result=score(payload,response,gold)
            self.assertFalse(result['valid'])
            self.assertIsNone(result['semantic_score'])
