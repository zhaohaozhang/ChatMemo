import json
import tempfile
import unittest
from pathlib import Path

from evaluation import evaluate_case, load_cases


FIXTURES = Path(__file__).parent / 'fixtures'
REQUIRED = {
    'id', 'question', 'allowed_intents', 'allowed_answers', 'required_citations',
    'forbidden_claims', 'acceptable_uncertainty', 'scope',
}


class EvaluationFixtureTests(unittest.TestCase):
    def test_fixture_covers_eight_intents_and_required_schema(self):
        cases = load_cases(FIXTURES / 'qa_cases.json')
        self.assertGreaterEqual(len(cases), 16)
        self.assertTrue(all(REQUIRED.issubset(case) for case in cases))
        intents = {intent for case in cases for intent in case['allowed_intents']}
        self.assertEqual({
            'time_location', 'count_statistics', 'exact_quote', 'semantic_search',
            'context_explanation', 'change_comparison', 'topic_summary', 'evidence_status',
        }, intents)

    def test_missing_field_invalid_reference_and_nonfiction_contact_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'qa_synthetic_chat.txt').write_text(
                '[2026-01-01 00:00:00] 甲: 虚构消息', encoding='utf-8'
            )
            base = {
                'fixture_type': 'synthetic_chat_qa', 'contact_name': '甲（虚构）',
                'cases': [{
                    'id': 'x', 'question': '问题', 'allowed_intents': ['semantic_search'],
                    'allowed_answers': ['答案'], 'required_citations': ['M1'],
                    'forbidden_claims': [], 'acceptable_uncertainty': [], 'scope': {},
                }],
            }
            for label, mutation in (
                ('missing', lambda value: value['cases'][0].pop('scope')),
                ('reference', lambda value: value['cases'][0].update(required_citations=['M99'])),
                ('contact', lambda value: value.update(contact_name='真实联系人')),
            ):
                value = json.loads(json.dumps(base, ensure_ascii=False))
                mutation(value)
                path = root / f'{label}.json'
                path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
                with self.subTest(label=label), self.assertRaises(ValueError):
                    load_cases(path)


class EvaluationResultTests(unittest.TestCase):
    def setUp(self):
        self.case = {
            'id': 'case', 'question': '问题', 'allowed_intents': ['semantic_search'],
            'allowed_answers': ['允许答案'], 'required_citations': ['M1'],
            'forbidden_claims': ['禁止结论'], 'acceptable_uncertainty': ['需要说明'],
            'scope': {'contact_total_messages': 2},
        }
        self.answer = {
            'answer_kind': 'evidence', 'answer': '允许答案', 'intent': 'semantic_search',
            'answer_units': [{'text': '允许答案', 'kind': 'evidence_quote', 'citations': ['M1'], 'uncertainty': None}],
            'citations': [{'id': 1}], 'uncertainty': ['需要说明'],
            'scope': {'contact_total_messages': 2},
        }

    def test_allowed_answer_passes(self):
        self.assertEqual([], evaluate_case(self.case, self.answer))

    def test_failures_have_stable_error_codes(self):
        mutations = {
            'missing_required_citation': lambda answer: answer.update(citations=[]),
            'forbidden_claim': lambda answer: answer.update(answer='允许答案 禁止结论'),
            'wrong_intent': lambda answer: answer.update(intent='time_location'),
            'missing_uncertainty': lambda answer: answer.update(uncertainty=[]),
            'scope_mismatch': lambda answer: answer.update(scope={'contact_total_messages': 3}),
            'partial_model_answer': lambda answer: answer.update(
                answer_kind='model',
                answer_units=[{'text': '事实', 'kind': 'fact', 'citations': [], 'uncertainty': None}],
            ),
        }
        for expected, mutate in mutations.items():
            answer = json.loads(json.dumps(self.answer, ensure_ascii=False))
            mutate(answer)
            with self.subTest(expected=expected):
                self.assertIn(expected, evaluate_case(self.case, answer))


if __name__ == '__main__':
    unittest.main()
