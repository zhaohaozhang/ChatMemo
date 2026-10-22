"""Deterministic local topic extraction. Labels describe mentions, not inferred facts."""
from collections import OrderedDict

RULE_VERSION = "1"

TOPIC_TERMS = OrderedDict([
    ("饮食", ("咖啡", "茶", "奶茶", "早餐", "午饭", "晚饭", "吃饭", "餐厅", "甜点", "饮料", "火锅", "外卖")),
    ("工作学习", ("工作", "项目", "开会", "会议", "同事", "上班", "学习", "考试", "课程", "作业", "论文", "学校")),
    ("行程约定", ("约好", "见面", "周一", "周二", "周三", "周四", "周五", "周六", "周日", "几点", "改期", "预约", "安排")),
    ("健康", ("医院", "医生", "生病", "感冒", "发烧", "药", "体检", "睡眠", "头疼", "健康", "运动", "健身")),
    ("兴趣娱乐", ("电影", "音乐", "游戏", "小说", "看书", "演出", "旅行", "摄影", "画画", "追剧", "综艺", "动漫")),
    ("购物金钱", ("买", "购买", "购物", "付款", "价格", "多少钱", "退款", "优惠", "工资", "借钱", "转账", "账单")),
    ("家庭朋友", ("妈妈", "爸爸", "父母", "家人", "朋友", "同学", "孩子", "姐姐", "妹妹", "哥哥", "弟弟", "亲戚")),
    ("住处出行", ("地铁", "公交", "打车", "开车", "高铁", "飞机", "车站", "回家", "搬家", "租房", "住址", "小区")),
])
TOPIC_NAMES = tuple(TOPIC_TERMS)


def _normalized(value):
    return " ".join(str(value or "").lower().split())


def classify_message(message):
    text = _normalized(message.get("text", ""))
    if not text:
        return []
    return [name for name, terms in TOPIC_TERMS.items() if any(term in text for term in terms)]


def classify_messages(messages):
    grouped = {name: [] for name in TOPIC_NAMES}
    for message in messages:
        for name in classify_message(message):
            grouped[name].append(message)
    return grouped


def _representatives(messages, limit):
    newest = sorted(messages, key=lambda row: (row["timestamp"], row.get("id", 0)), reverse=True)
    chosen = []
    used_ids = set()
    used_days = set()
    for row in newest:
        day = row["timestamp"][:10]
        if day not in used_days:
            chosen.append(row)
            used_ids.add(row.get("id"))
            used_days.add(day)
            if len(chosen) >= limit:
                return chosen
    for row in newest:
        if row.get("id") not in used_ids:
            chosen.append(row)
            if len(chosen) >= limit:
                break
    return chosen


def build_overview(messages, representatives=5):
    ordered = sorted(messages, key=lambda row: (row["timestamp"], row.get("id", 0)))
    grouped = classify_messages(ordered)
    classified_ids = {row.get("id", id(row)) for rows in grouped.values() for row in rows}
    topics = []
    for name in TOPIC_NAMES:
        rows = grouped[name]
        if not rows:
            continue
        topics.append({
            "name": name,
            "message_count": len(rows),
            "day_count": len({row["timestamp"][:10] for row in rows}),
            "first_at": rows[0]["timestamp"],
            "last_at": rows[-1]["timestamp"],
            "messages": _representatives(rows, max(0, representatives)),
        })
    return {"rule_version": RULE_VERSION, "classified_messages": len(classified_ids), "topics": topics}
