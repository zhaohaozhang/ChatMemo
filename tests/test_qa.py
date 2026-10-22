import json
import tempfile
import unittest
from pathlib import Path

import topics
from qa import (
    QA_RULE_VERSION,
    answer_deterministic,
    answer_question,
    finalize_model_units,
    route_question,
    validate_previous_context,
)
from qa_contract import validate_answer_units
from store import Store
from search import retrieve_evidence


DETERMINISTIC_SAMPLE = '''联系人: 小林
[2026-09-01 09:00:00] 小林: 早上好
[2026-09-01 09:00:00] 小林: 重复记录
[2026-09-01 09:00:00] 小林: 重复记录
[2026-09-02 10:00:00] 我: 明天继续
[2026-09-05 12:00:00] 小林: 项目结束
[2026-13-40 10:00:00] 小林: 无效时间但仍属于导入覆盖缺口
'''

RETRIEVAL_SAMPLE = '''联系人: 小林
[2026-08-01 09:00:00] 小林: 我想换工作，正在看新机会。
[2026-08-01 09:01:00] 我: 为什么想换？
[2026-08-01 09:02:00] 小林: 现在的通勤时间太长了。
[2026-08-02 10:00:00] 小林: 我以前喜欢咖啡。
[2026-08-03 10:00:00] 小林: 她喜欢咖啡，我只是帮她买。
[2026-09-10 18:00:00] 小林: 我现在更喜欢茶。
[2026-10-01 12:00:00] 小林: 我已经找到新工作了。
[2026-10-01 12:00:00] 小林: 我已经找到新工作了。
'''


class QuestionRoutingTests(unittest.TestCase):
    def test_all_eight_intents_are_routed(self):
        cases = {
            'time_location': '我们最后一次聊天是什么时候？',
            'count_statistics': '这个月一共聊了多少条？',
            'exact_quote': '他当时的原话是什么？',
            'semantic_search': '找找和换工作意思相近的内容',
            'context_explanation': '那句话前后还说了什么？',
            'change_comparison': '以前和现在的态度有什么变化？',
            'topic_summary': '最近主要聊了哪些主题？',
            'evidence_status': '有没有证据说明他喜欢咖啡？',
        }
        for intent, question in cases.items():
            with self.subTest(intent=intent):
                self.assertIn(intent, route_question(question)['intents'])

    def test_combined_questions_keep_all_required_handlers(self):
        first_work = route_question('最早什么时候说过工作')
        self.assertIn('time_location', first_work['intents'])
        self.assertIn('semantic_search', first_work['intents'])

        coffee = route_question('以前是不是更喜欢咖啡')
        self.assertIn('change_comparison', coffee['intents'])
        self.assertIn('evidence_status', coffee['intents'])
        topic = route_question('关于工作主要聊过什么？')
        self.assertEqual('topic_summary', topic['primary_intent'])

    def test_follow_up_inherits_topic_time_and_speaker(self):
        previous = {
            'contact_id': 8,
            'revision': 3,
            'rules_version': f'qa:{QA_RULE_VERSION};topics:{topics.RULE_VERSION}',
            'primary_intent': 'semantic_search',
            'intents': ['semantic_search'],
            'entities': ['咖啡'],
            'time_expression': '2026年9月',
            'speaker_expression': '小林',
            'citation_ids': [10],
        }
        routed = route_question('后来呢？', previous)
        self.assertEqual(['咖啡'], routed['entities'])
        self.assertEqual('2026年9月', routed['time_expression'])
        self.assertEqual('小林', routed['speaker_expression'])
        self.assertEqual(
            {'entities', 'time_expression', 'speaker_expression'},
            set(routed['inherited_fields']),
        )

    def test_empty_long_and_malformed_previous_are_rejected(self):
        for question in ('', '   ', '问' * 1001):
            with self.subTest(question_length=len(question)):
                with self.assertRaises(ValueError):
                    route_question(question)
        with self.assertRaises(ValueError):
            route_question('后来呢', {'entities': '不是列表'})

    def test_previous_context_rejected_on_rules_version_change(self):
        current = f'qa:{QA_RULE_VERSION};topics:{topics.RULE_VERSION}'
        previous = {
            'contact_id': 9,
            'revision': 4,
            'rules_version': current,
            'primary_intent': 'semantic_search',
            'intents': ['semantic_search'],
            'entities': ['工作'],
            'time_expression': '',
            'speaker_expression': '',
            'citation_ids': [21],
        }
        self.assertEqual(
            previous,
            validate_previous_context(previous, contact_id=9, revision=4, rules_version=current),
        )
        self.assertIsNone(
            validate_previous_context(previous, contact_id=9, revision=4, rules_version='qa:2;topics:1')
        )
        self.assertIsNone(
            validate_previous_context(previous, contact_id=9, revision=5, rules_version=current)
        )
        self.assertIsNone(
            validate_previous_context(previous, contact_id=10, revision=4, rules_version=current)
        )


class DeterministicAnswerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'memory.sqlite3')
        imported = self.store.import_text(
            'deterministic.txt',
            DETERMINISTIC_SAMPLE,
            DETERMINISTIC_SAMPLE.encode('utf-8'),
            name='小林',
        )
        self.contact_id = imported['contact_id']

    def tearDown(self):
        self.tmp.cleanup()

    def ask(self, question, **filters):
        return answer_deterministic(
            self.store,
            self.contact_id,
            route_question(question),
            filters,
        )

    def assert_contract(self, result):
        self.assertEqual('deterministic', result['answer_kind'])
        self.assertTrue(result['answer_units'])
        self.assertIn('scope', result)
        self.assertIn('method', result)
        self.assertIn('uncertainty', result)
        self.assertIn('next_context', result)
        for unit in result['answer_units']:
            self.assertTrue(unit['citation_ids'])
            self.assertIn('calculation', unit)

    def test_first_and_last_message_use_timestamp_not_question_keywords(self):
        first = self.ask('我们第一次对话的时间是哪一天？')
        last = self.ask('当前记录最后一条是什么时候？')
        self.assert_contract(first)
        self.assert_contract(last)
        self.assertIn('2026-09-01 09:00:00', first['answer'])
        self.assertIn('2026-09-05 12:00:00', last['answer'])
        self.assertEqual(1, len(first['citations']))
        self.assertEqual(1, len(last['citations']))

    def test_counts_respect_date_and_speaker_filters(self):
        dated = self.ask('这段时间一共有多少条消息？', date_from='2026-09-01', date_to='2026-09-02')
        speaker = self.ask('小林一共说了多少条？', speaker='小林')
        self.assert_contract(dated)
        self.assertEqual(4, dated['answer_units'][0]['value'])
        self.assertEqual(4, dated['scope']['filtered_messages'])
        self.assertEqual(4, speaker['answer_units'][0]['value'])

    def test_active_days_longest_gap_and_most_active_date(self):
        active = self.ask('一共活跃了多少天？')
        gap = self.ask('最长未聊天间隔是多少？')
        busiest = self.ask('最活跃日期是哪一天？')
        self.assertEqual(3, active['answer_units'][0]['value'])
        self.assertEqual(266400, gap['answer_units'][0]['value_seconds'])
        self.assertEqual('2026-09-01', busiest['answer_units'][0]['date'])
        self.assertEqual(3, busiest['answer_units'][0]['value'])

    def test_same_second_same_text_messages_are_counted_individually(self):
        result = self.ask('同秒同文的重复记录有多少条？')
        self.assert_contract(result)
        self.assertEqual(2, result['answer_units'][0]['value'])
        self.assertEqual(2, len(result['answer_units'][0]['citation_ids']))

    def test_first_message_reports_imported_scope_and_unparsed_time_gap(self):
        result = self.ask('最早的一条消息是什么时候？')
        self.assertIn('当前已导入记录', result['answer'])
        self.assertNotIn('绝对第一次', result['answer'])
        self.assertEqual(5, result['scope']['timestamped_messages'])
        self.assertEqual(1, result['scope']['untimestamped_messages'])
        self.assertTrue(any('无法解析时间' in item for item in result['uncertainty']))

    def test_deterministic_questions_need_no_model_configuration(self):
        for question in (
            '第一次对话是哪一天？',
            '最后一次是什么时候？',
            '消息总数是多少？',
            '最活跃日期是哪一天？',
        ):
            with self.subTest(question=question):
                self.assertEqual('deterministic', self.ask(question)['answer_kind'])


class EvidenceRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'memory.sqlite3')
        imported = self.store.import_text(
            'retrieval.txt',
            RETRIEVAL_SAMPLE,
            RETRIEVAL_SAMPLE.encode('utf-8'),
            name='小林',
        )
        self.contact_id = imported['contact_id']

    def tearDown(self):
        self.tmp.cleanup()

    def retrieve(self, question, filters=None, limit=30):
        route = route_question(question)
        return retrieve_evidence(
            self.store,
            self.contact_id,
            question,
            route,
            filters or {},
            limit=limit,
        )

    def test_structured_first_message_is_answered_without_keyword_retrieval(self):
        result = answer_deterministic(
            self.store,
            self.contact_id,
            route_question('我们第一次对话的时间是哪一天？'),
            {},
        )
        self.assertEqual('deterministic', result['answer_kind'])
        self.assertIn('2026-08-01 09:00:00', result['answer'])

    def test_exact_quotes_and_similar_expressions_are_separate(self):
        exact = self.retrieve('原话“我想换工作”是什么？')
        related = self.retrieve('找找职业变动的相近表达')
        self.assertEqual('direct_evidence', exact['status'])
        self.assertTrue(exact['exact_matches'])
        self.assertTrue(all(row['match_type'] == 'exact_quote' for row in exact['exact_matches']))
        self.assertEqual('similar_expression', related['status'])
        self.assertTrue(any('换工作' in row['text'] for row in related['related_matches']))

    def test_time_speaker_filters_and_four_scope_counts(self):
        result = self.retrieve(
            '找找工作相关内容',
            {'date_from': '2026-10-01', 'date_to': '2026-10-01', 'speaker': '小林'},
        )
        scope = result['scope']
        self.assertEqual(8, scope['contact_total_messages'])
        self.assertEqual(2, scope['filtered_messages'])
        self.assertEqual(2, scope['candidate_messages'])
        self.assertEqual(2, scope['evidence_messages'])
        self.assertEqual(2, len({row['id'] for row in result['evidence']}))

    def test_context_expands_three_messages_on_each_side(self):
        result = self.retrieve('这句话“通勤时间太长了”前后还说了什么？')
        texts = [row['text'] for row in result['evidence']]
        self.assertIn('我想换工作，正在看新机会。', texts)
        self.assertIn('为什么想换？', texts)
        self.assertIn('现在的通勤时间太长了。', texts)
        self.assertTrue(any(row['match_type'] == 'context' for row in result['evidence']))

    def test_change_evidence_uses_distinct_time_points_and_downranks_third_person(self):
        changed = self.retrieve('以前和现在更喜欢什么，有什么变化？')
        dates = {row['timestamp'][:10] for row in changed['evidence']}
        self.assertIn('2026-08-02', dates)
        self.assertIn('2026-09-10', dates)
        coffee = self.retrieve('有没有证据说明小林喜欢咖啡？')
        self.assertIn('我以前喜欢咖啡', coffee['evidence'][0]['text'])
        self.assertNotIn('她喜欢咖啡', coffee['evidence'][0]['text'])

    def test_no_candidate_is_current_scope_not_found_not_absolute_denial(self):
        result = self.retrieve('有没有证据说去过火星基地？')
        self.assertEqual('current_scope_not_found', result['status'])
        self.assertEqual([], result['evidence'])
        self.assertIn('当前范围未找到', result['summary'])
        self.assertNotIn('从未', result['summary'])

    def test_follow_up_retrieval_uses_inherited_entity(self):
        previous = {
            'primary_intent': 'semantic_search', 'intents': ['semantic_search'],
            'entities': ['工作'], 'time_expression': '', 'speaker_expression': '',
        }
        route = route_question('后来呢？', previous)
        result = retrieve_evidence(self.store, self.contact_id, '后来呢？', route, {})
        self.assertNotEqual('current_scope_not_found', result['status'])
        self.assertTrue(any('新工作' in row['text'] for row in result['evidence']))


class AnswerContractTests(unittest.TestCase):
    def setUp(self):
        self.available = {
            1: {
                'id': 1,
                'contact_id': 7,
                'timestamp': '2026-09-01 10:00:00',
                'speaker': '小林',
                'text': '我现在更喜欢茶。',
                'source_id': 4,
                'source_name': 'synthetic.txt',
                'line_start': 2,
                'line_end': 2,
            }
        }
        self.retrieval = {
            'status': 'similar_expression',
            'summary': '当前范围找到 1 条相近表达；它不是逐字原话。',
            'evidence': list(self.available.values()),
            'scope': {
                'contact_id': 7,
                'contact_total_messages': 1,
                'filtered_messages': 1,
                'candidate_messages': 1,
                'evidence_messages': 1,
            },
            'method': ['lexical_ngram'],
        }

    def test_valid_units_preserve_per_unit_citations(self):
        units = [{
            'text': '记录显示当时更喜欢茶。',
            'kind': 'fact',
            'citations': ['M1'],
            'uncertainty': None,
        }]
        validated = validate_answer_units(units, self.available)
        self.assertEqual(['M1'], validated[0]['citations'])
        result = finalize_model_units(units, self.retrieval)
        self.assertEqual('model', result['answer_kind'])
        self.assertEqual(['M1'], result['answer_units'][0]['citations'])

    def test_model_scope_distinguishes_retrieved_from_final_cited_evidence(self):
        second = {**self.available[1], 'id': 2, 'text': '另一条已召回但未被最终结论引用的证据。'}
        retrieval = {
            **self.retrieval,
            'evidence': [self.available[1], second],
            'scope': {**self.retrieval['scope'], 'candidate_messages': 2, 'evidence_messages': 2},
        }
        result = finalize_model_units([{
            'text': '记录显示当时更喜欢茶。',
            'kind': 'fact',
            'citations': ['M1'],
            'uncertainty': None,
        }], retrieval)

        self.assertEqual(2, result['scope']['evidence_messages'])
        self.assertEqual(1, result['scope']['final_evidence_messages'])
        self.assertEqual([1], [message['id'] for message in result['citations']])

    def test_any_invalid_unit_downgrades_the_whole_model_answer(self):
        invalid_cases = {
            'second unit missing citation': [
                {'text': '当时更喜欢茶。', 'kind': 'fact', 'citations': ['M1'], 'uncertainty': None},
                {'text': '而且每天都喝。', 'kind': 'fact', 'citations': [], 'uncertainty': None},
            ],
            'fabricated id': [
                {'text': '喜欢咖啡。', 'kind': 'fact', 'citations': ['M999'], 'uncertainty': None},
            ],
            'other contact or trashed id': [
                {'text': '另一条记录。', 'kind': 'fact', 'citations': ['M2'], 'uncertainty': None},
            ],
            'structural error': {'text': 'not a list'},
        }
        for label, units in invalid_cases.items():
            with self.subTest(label=label):
                result = finalize_model_units(units, self.retrieval)
                self.assertEqual('evidence', result['answer_kind'])
                self.assertIn('degraded_reason', result)
                self.assertTrue(all(unit['kind'] != 'model_fact' for unit in result['answer_units']))

    def test_uncertainty_kind_cannot_bypass_required_citations(self):
        result = finalize_model_units([{
            'text': '小林现在住在北京。',
            'kind': 'uncertainty',
            'citations': [],
            'uncertainty': None,
        }], self.retrieval)

        self.assertEqual('evidence', result['answer_kind'])
        self.assertIn('缺少引用', result['degraded_reason'])
        self.assertNotIn('小林现在住在北京。', result['answer'])

    def test_missing_source_invalidates_every_model_unit_atomically(self):
        broken = {1: {key: value for key, value in self.available[1].items() if key != 'source_id'}}
        units = [{'text': '当时更喜欢茶。', 'kind': 'fact', 'citations': ['M1'], 'uncertainty': None}]
        with self.assertRaises(ValueError):
            validate_answer_units(units, broken)

        retrieval = {**self.retrieval, 'evidence': list(broken.values())}
        result = finalize_model_units(units, retrieval)
        self.assertEqual('evidence', result['answer_kind'])
        self.assertEqual([], [unit for unit in result['answer_units'] if unit.get('kind') == 'model_fact'])


class AnswerQuestionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'memory.sqlite3')
        imported = self.store.import_text(
            'qa.txt', RETRIEVAL_SAMPLE, RETRIEVAL_SAMPLE.encode('utf-8'), name='小林'
        )
        self.contact_id = imported['contact_id']

    def tearDown(self):
        self.tmp.cleanup()

    def test_deterministic_evidence_and_model_paths_share_contract(self):
        deterministic = answer_question(
            self.store, self.contact_id, '我们第一次对话是哪一天？', {}, None
        )
        self.assertEqual('deterministic', deterministic['answer_kind'])
        evidence = answer_question(
            self.store, self.contact_id, '找找职业变动的相近表达', {}, None
        )
        self.assertEqual('evidence', evidence['answer_kind'])

        def summarizer(question, selected):
            self.assertTrue(selected)
            return {'answer_units': [{
                'text': '记录提到过换工作。', 'kind': 'fact',
                'citations': [f"M{selected[0]['id']}"], 'uncertainty': None,
            }]}

        model = answer_question(
            self.store, self.contact_id, '总结工作变化', {}, None, summarizer=summarizer
        )
        self.assertEqual('model', model['answer_kind'])
        for result in (deterministic, evidence, model):
            self.assertTrue(result['answer_units'])
            self.assertEqual(
                {'contact_total_messages', 'filtered_messages', 'candidate_messages', 'evidence_messages'},
                {'contact_total_messages', 'filtered_messages', 'candidate_messages', 'evidence_messages'} & set(result['scope']),
            )
            self.assertIn('method', result)
            self.assertIn('uncertainty', result)
            self.assertIn('next_context', result)

    def test_combined_question_runs_deterministic_and_evidence_handlers(self):
        result = answer_question(
            self.store,
            self.contact_id,
            '找找工作相关内容，并告诉我一共有多少条消息。',
            {},
            None,
        )

        self.assertEqual('combined', result['answer_kind'])
        self.assertTrue(any(unit.get('unit_type') == 'statistic' for unit in result['answer_units']))
        self.assertTrue(any(unit.get('kind') == 'evidence_quote' for unit in result['answer_units']))
        self.assertIn('当前筛选范围内共有 8 条消息', result['answer'])
        self.assertIn('相近表达', result['answer'])
        self.assertIn('sqlite_deterministic', result['method'])
        self.assertIn('lexical_ngram', result['method'])
        self.assertEqual(8, result['scope']['contact_total_messages'])
        self.assertGreaterEqual(result['scope']['candidate_messages'], 1)
        self.assertGreaterEqual(result['scope']['evidence_messages'], 1)

    def test_generic_topic_summary_uses_fixed_topic_overview(self):
        result = answer_question(
            self.store,
            self.contact_id,
            '最近主要聊了什么？',
            {},
            None,
        )

        self.assertEqual('deterministic', result['answer_kind'])
        self.assertIn('饮食', result['answer'])
        self.assertIn('工作学习', result['answer'])
        self.assertGreater(result['scope']['candidate_messages'], 0)
        self.assertIn('fixed_topic_terms', result['method'])
        self.assertTrue(result['citations'])

    def test_model_evidence_is_capped_at_twelve_and_scope_matches_actual_input(self):
        lines = [
            f'[2026-11-{index + 1:02d} 09:00:00] 小林: 工作记录 {index}'
            for index in range(20)
        ]
        extra = '\n'.join(lines)
        self.store.import_text(
            'many-work.txt', extra, extra.encode('utf-8'), contact_id=self.contact_id
        )
        sent = []

        def summarizer(_, evidence):
            sent.extend(evidence)
            return {'answer_units': [{
                'text': '只概括实际收到的证据。',
                'kind': 'summary',
                'citations': [f"M{evidence[0]['id']}"],
                'uncertainty': None,
            }]}

        result = answer_question(
            self.store, self.contact_id, '找找工作相关内容', {}, None, summarizer=summarizer
        )

        self.assertEqual(12, len(sent))
        self.assertEqual(12, result['scope']['evidence_messages'])
        self.assertGreater(result['scope']['candidate_messages'], 12)
        self.assertEqual(1, result['scope']['final_evidence_messages'])

    def test_model_evidence_respects_serialized_character_budget(self):
        lines = [
            f'[2026-12-{index + 1:02d} 09:00:00] 小林: 工作 ' + ('甲' * 3000)
            for index in range(12)
        ]
        extra = '\n'.join(lines)
        self.store.import_text(
            'long-work.txt', extra, extra.encode('utf-8'), contact_id=self.contact_id
        )
        sent = []

        def summarizer(_, evidence):
            sent.extend(evidence)
            return {'answer_units': [{
                'text': '按字符预算整理。',
                'kind': 'summary',
                'citations': [f"M{evidence[0]['id']}"],
                'uncertainty': None,
            }]}

        result = answer_question(
            self.store, self.contact_id, '找找工作相关内容', {}, None, summarizer=summarizer
        )
        records = [{
            'id': f"M{message['id']}",
            'speaker': message['speaker'],
            'time': message['timestamp'],
            'text': message['text'],
        } for message in sent]

        self.assertTrue(sent)
        self.assertLessEqual(len(json.dumps(records, ensure_ascii=False)), 24000)
        self.assertEqual(len(sent), result['scope']['evidence_messages'])

    def test_entity_scoped_first_mention_combines_time_and_evidence_with_filters(self):
        result = answer_question(
            self.store,
            self.contact_id,
            '最早什么时候提到工作？后来怎么样了？',
            {'date_from': '2026-08-01'},
            None,
        )

        self.assertEqual('combined', result['answer_kind'])
        self.assertIn('2026-08-01 09:00:00', result['answer'])
        self.assertIn(1, [message['id'] for message in result['citations']])
        self.assertIn(7, [message['id'] for message in result['citations']])
        self.assertEqual('2026-08-01', result['scope']['date_from'])
        self.assertIn('sqlite_deterministic', result['method'])
        self.assertIn('lexical_ngram', result['method'])

    def test_model_failure_returns_complete_evidence_fallback(self):
        def failing(*_):
            raise ValueError('生成中服务停止')

        result = answer_question(
            self.store, self.contact_id, '总结工作变化', {}, None, summarizer=failing
        )
        self.assertEqual('evidence', result['answer_kind'])
        self.assertIn('生成中服务停止', result['degraded_reason'])
        self.assertTrue(result['answer_units'])

    def test_previous_context_is_accepted_then_invalidated_by_contact_revision(self):
        first = answer_question(
            self.store, self.contact_id, '找找咖啡相关内容', {}, None
        )
        follow_up = answer_question(
            self.store, self.contact_id, '后来呢？', {}, first['next_context']
        )
        self.assertEqual('accepted', follow_up['context_status'])
        self.assertIn('entities', follow_up['route']['inherited_fields'])

        extra = '[2026-11-01 12:00:00] 小林: 新增的一条记录'
        self.store.import_text('extra.txt', extra, extra.encode('utf-8'), contact_id=self.contact_id)
        stale = answer_question(
            self.store, self.contact_id, '后来呢？', {}, first['next_context']
        )
        self.assertEqual('stale', stale['context_status'])
        self.assertEqual([], stale['route']['inherited_fields'])

    def test_previous_context_is_rejected_after_contact_switch(self):
        first = answer_question(
            self.store, self.contact_id, '找找工作相关内容', {}, None
        )
        imported = self.store.import_text(
            'other.txt', RETRIEVAL_SAMPLE, RETRIEVAL_SAMPLE.encode('utf-8'), name='另一虚构联系人'
        )

        switched = answer_question(
            self.store, imported['contact_id'], '后来呢？', {}, first['next_context']
        )

        self.assertEqual('stale', switched['context_status'])
        self.assertEqual([], switched['route']['inherited_fields'])
        self.assertNotEqual(self.contact_id, switched['next_context']['contact_id'])


if __name__ == '__main__':
    unittest.main()
