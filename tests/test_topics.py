import copy
import time
import unittest

from topics import RULE_VERSION, TOPIC_NAMES, build_overview, classify_message, classify_messages


def message(identifier, timestamp, text, speaker='小林'):
    return {'id': identifier, 'timestamp': timestamp, 'speaker': speaker, 'text': text,
            'kind': 'text', 'source_id': 1, 'source_name': 'synthetic.txt',
            'line_start': identifier, 'line_end': identifier}


class TopicClassifierTests(unittest.TestCase):
    def test_eight_topics_and_stable_order(self):
        fixtures = [
            ('饮食', '下午喝咖啡'), ('工作学习', '项目要开会'), ('行程约定', '周六下午见'),
            ('健康', '明天去医院'), ('兴趣娱乐', '晚上看电影'), ('购物金钱', '已经付款了'),
            ('家庭朋友', '妈妈今天来'), ('住处出行', '坐地铁回家'),
        ]
        self.assertEqual(TOPIC_NAMES, tuple(name for name, _ in fixtures))
        for index, (topic, text) in enumerate(fixtures, 1):
            self.assertIn(topic, classify_message(message(index, '2026-09-01 10:00:00', text)))

    def test_multilabel_repetition_unmatched_negation_and_third_person(self):
        labels = classify_message(message(1, '2026-09-01 10:00:00', '和妈妈坐地铁去医院买咖啡咖啡'))
        self.assertEqual(len(labels), len(set(labels)))
        self.assertEqual(labels, ['饮食', '健康', '购物金钱', '家庭朋友', '住处出行'])
        self.assertEqual(classify_message(message(2, '2026-09-01 10:00:00', '蓝色的云飘过')), [])
        self.assertEqual(classify_message(message(3, '2026-09-01 10:00:00', '我不喜欢咖啡')), ['饮食'])
        self.assertEqual(classify_message(message(4, '2026-09-01 10:00:00', '她朋友喜欢电影')), ['兴趣娱乐', '家庭朋友'])

    def test_classify_messages_does_not_duplicate_one_message_per_topic(self):
        rows = [message(1, '2026-09-01 10:00:00', '咖啡咖啡咖啡'),
                message(2, '2026-09-01 11:00:00', '普通内容')]
        classified = classify_messages(rows)
        self.assertEqual([row['id'] for row in classified['饮食']], [1])
        self.assertEqual(sum(len(value) for value in classified.values()), 1)


class TopicOverviewTests(unittest.TestCase):
    def setUp(self):
        self.rows = [
            message(1, '2026-09-01 08:00:00', '咖啡和项目'),
            message(2, '2026-09-01 09:00:00', '再喝咖啡'),
            message(3, '2026-09-02 09:00:00', '茶'),
            message(4, '2026-09-03 09:00:00', '晚饭'),
            message(5, '2026-09-04 09:00:00', '早餐'),
            message(6, '2026-09-05 09:00:00', '甜点'),
            message(7, '2026-09-06 09:00:00', '奶茶'),
            message(8, '2026-09-07 09:00:00', '没有匹配的普通内容'),
        ]

    def test_counts_bounds_days_order_and_representatives(self):
        result = build_overview(list(reversed(self.rows)))
        self.assertEqual(result['rule_version'], RULE_VERSION)
        self.assertEqual(result['classified_messages'], 7)
        self.assertEqual([topic['name'] for topic in result['topics']], ['饮食', '工作学习'])
        food = result['topics'][0]
        self.assertEqual(food['message_count'], 7)
        self.assertEqual(food['day_count'], 6)
        self.assertEqual(food['first_at'], '2026-09-01 08:00:00')
        self.assertEqual(food['last_at'], '2026-09-06 09:00:00')
        self.assertEqual([row['id'] for row in food['messages']], [7, 6, 5, 4, 3])

    def test_empty_and_unmatched_are_honest(self):
        self.assertEqual(build_overview([])['topics'], [])
        result = build_overview([message(9, '2026-09-01 09:00:00', '蓝色的云')])
        self.assertEqual(result['classified_messages'], 0)
        self.assertEqual(result['topics'], [])


class TopicPerformanceTests(unittest.TestCase):
    def test_thirty_thousand_messages_under_two_seconds_without_mutation(self):
        terms = ['咖啡', '项目开会', '周六见', '医院', '电影', '付款', '妈妈', '地铁']
        rows = [message(index + 1, f'2026-09-{index % 28 + 1:02d} 12:00:00', terms[index % len(terms)] + ' ' + '甲' * 120)
                for index in range(30000)]
        before = copy.deepcopy(rows[0])
        started = time.perf_counter()
        result = build_overview(rows)
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, 2.0)
        self.assertEqual(rows[0], before)
        self.assertEqual(result['classified_messages'], 30000)


if __name__ == '__main__':
    unittest.main()
