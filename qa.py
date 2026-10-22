"""Question routing and context validation for evidence-backed chat QA."""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime

import topics
from qa_contract import evidence_fallback, validate_answer_units
from qa_contract import select_model_evidence


QA_RULE_VERSION = "1"
MAX_QUESTION_LENGTH = 1000
INTENT_ORDER = (
    "time_location",
    "count_statistics",
    "exact_quote",
    "semantic_search",
    "context_explanation",
    "change_comparison",
    "topic_summary",
    "evidence_status",
)

_INTENT_PATTERNS = {
    "time_location": re.compile(r"首次|第一次|最早|最后一次|最近一次|什么时候|哪[一]?天|日期|时间"),
    "count_statistics": re.compile(r"多少|几次|数量|频率|占比|活跃|间隔|总共|一共"),
    "exact_quote": re.compile(r"原话|逐字|谁说过|这句话|有没有说过[‘\"“]"),
    "semantic_search": re.compile(r"意思相近|类似|相关|关于|提到|说过(?![‘\"“])|找找|查找"),
    "context_explanation": re.compile(r"前后|背景|上下文|指谁|指的谁|这里的|那天.*还说"),
    "change_comparison": re.compile(r"变化|改变|以前.*(?:现在|后来)|过去.*现在|前后态度|更喜欢"),
    "topic_summary": re.compile(r"主题|主要聊|聊过什么|谈过哪些|归纳|总结|都聊"),
    "evidence_status": re.compile(r"证据|是不是|是否|有没有|能否说明|可以证明|没(?:有)?说过"),
}
_FOLLOW_UP = re.compile(r"^(后来|然后|之后|之前|那次|那天|还有|接着)(呢|怎么样|又怎样)?[？?。.]?$")
_TIME_EXPRESSION = re.compile(
    r"(20\d{2}年(?:\d{1,2}月(?:\d{1,2}日)?)?|20\d{2}[-/]\d{1,2}(?:[-/]\d{1,2})?|"
    r"今天|昨天|前天|最近|近期|这个月|上个月|今年|去年|以前|过去|现在|后来)"
)
_SPEAKER_EXPRESSION = re.compile(r"([\u4e00-\u9fffA-Za-z0-9_]{1,30})(?:当时)?说(?:过|了|的)")
_ENTITY_PATTERNS = (
    re.compile(r"(?:找找|查找)([\u4e00-\u9fffA-Za-z0-9_]{1,40}?)(?:相关|相近|内容|记录)"),
    re.compile(r"(?:关于|提到|说过|围绕)([\u4e00-\u9fffA-Za-z0-9_]{1,40}?)(?:的|时|是|有|没|？|\?|$)"),
    re.compile(r"(?:喜欢|改成|换成)([\u4e00-\u9fffA-Za-z0-9_]{1,20})"),
    re.compile(r"[‘\"“]([^’\"”]{1,80})[’\"”]"),
)


def rules_version() -> str:
    return f"qa:{QA_RULE_VERSION};topics:{topics.RULE_VERSION}"


def _validate_route_previous(previous):
    if previous is None:
        return
    if not isinstance(previous, dict):
        raise ValueError("上一轮上下文格式无效。")
    type_rules = {
        "entities": list,
        "time_expression": str,
        "speaker_expression": str,
        "intents": list,
        "primary_intent": str,
    }
    for field, expected_type in type_rules.items():
        if field in previous and not isinstance(previous[field], expected_type):
            raise ValueError("上一轮上下文格式无效。")
    if any(not isinstance(item, str) for item in previous.get("entities", [])):
        raise ValueError("上一轮上下文格式无效。")


def _extract_entities(question: str) -> list[str]:
    entities = []
    for pattern in _ENTITY_PATTERNS:
        for match in pattern.finditer(question):
            value = match.group(1).strip(" 的了呢吗呀，,。.!！?？")
            if value and value not in entities:
                entities.append(value)
    return entities


def route_question(question: str, previous: dict | None = None) -> dict:
    if not isinstance(question, str) or not question.strip():
        raise ValueError("问题不能为空。")
    question = question.strip()
    if len(question) > MAX_QUESTION_LENGTH:
        raise ValueError(f"问题不能超过 {MAX_QUESTION_LENGTH} 个字符。")
    _validate_route_previous(previous)

    matched = [intent for intent in INTENT_ORDER if _INTENT_PATTERNS[intent].search(question)]
    is_follow_up = bool(_FOLLOW_UP.match(question))
    inherited_fields = []
    entities = _extract_entities(question)
    time_match = _TIME_EXPRESSION.search(question)
    time_expression = time_match.group(1) if time_match and not is_follow_up else ""
    speaker_match = _SPEAKER_EXPRESSION.search(question)
    speaker_expression = speaker_match.group(1) if speaker_match else ""

    if is_follow_up and previous:
        if not matched:
            matched = list(previous.get("intents", []))
        for field, current in (
            ("entities", entities),
            ("time_expression", time_expression),
            ("speaker_expression", speaker_expression),
        ):
            prior = previous.get(field)
            if not current and prior:
                if field == "entities":
                    entities = list(prior)
                elif field == "time_expression":
                    time_expression = prior
                else:
                    speaker_expression = prior
                inherited_fields.append(field)

    if not matched:
        matched = ["semantic_search"]
    matched = [intent for intent in INTENT_ORDER if intent in set(matched)]
    if is_follow_up and previous:
        primary = previous.get("primary_intent")
    elif "topic_summary" in matched:
        primary = "topic_summary"
    else:
        primary = matched[0]
    if primary not in matched:
        primary = matched[0]
    return {
        "question": question,
        "primary_intent": primary,
        "intents": matched,
        "entities": entities,
        "time_expression": time_expression,
        "speaker_expression": speaker_expression,
        "inherited_fields": inherited_fields,
    }


def validate_previous_context(
    previous: dict | None,
    contact_id: int,
    revision: int,
    rules_version: str,
) -> dict | None:
    if not isinstance(previous, dict):
        return None
    if (
        previous.get("contact_id") != contact_id
        or previous.get("revision") != revision
        or previous.get("rules_version") != rules_version
    ):
        return None
    try:
        _validate_route_previous(previous)
    except ValueError:
        return None
    return previous


def _citation(message):
    return {
        key: message[key]
        for key in (
            "id", "timestamp", "speaker", "text", "source_id", "source_name",
            "line_start", "line_end",
        )
    }


def _deterministic_result(store, contact_id, route, scope, unit, selected):
    selected_ids = list(dict.fromkeys(message["id"] for message in selected))
    selected_by_id = {message["id"]: message for message in scope["messages"]}
    citations = [_citation(selected_by_id[item]) for item in selected_ids if item in selected_by_id]
    uncertainty = []
    if scope["untimestamped_messages"]:
        uncertainty.append(
            f"有 {scope['untimestamped_messages']} 条疑似消息无法解析时间，时间结论仅覆盖可解析记录。"
        )
    result_scope = {key: value for key, value in scope.items() if key != "messages"}
    result_scope.update({
        "contact_total_messages": scope["total_effective_messages"],
        "candidate_messages": len(scope["messages"]),
        "evidence_messages": len(citations),
        "final_evidence_messages": len(citations),
    })
    unit.setdefault("unit_type", "statistic")
    unit.setdefault("uncertainty", list(uncertainty))
    unit["citation_ids"] = selected_ids
    contact = store.contact(contact_id)
    next_context = {
        "contact_id": contact_id,
        "revision": contact["revision"],
        "rules_version": rules_version(),
        "primary_intent": route["primary_intent"],
        "intents": list(route["intents"]),
        "entities": list(route.get("entities", [])),
        "time_expression": route.get("time_expression", ""),
        "speaker_expression": route.get("speaker_expression", ""),
        "citation_ids": selected_ids,
    }
    return {
        "answer_kind": "deterministic",
        "answer": unit["text"],
        "answer_units": [unit],
        "intent": route["primary_intent"],
        "auxiliary_intents": [
            intent for intent in route["intents"] if intent != route["primary_intent"]
        ],
        "scope": result_scope,
        "citations": citations,
        "method": ["sqlite_deterministic"],
        "uncertainty": uncertainty,
        "next_context": next_context,
    }


def answer_deterministic(store, contact_id: int, route: dict, filters: dict):
    """Answer questions that structured records can calculate exactly."""

    if not isinstance(route, dict):
        raise ValueError("问题路由格式无效。")
    intents = route.get("intents", [])
    if not ({"time_location", "count_statistics"} & set(intents)):
        return None
    if route.get("entities") and "count_statistics" not in intents:
        return None
    scope = store.message_scope(contact_id, filters)
    messages = scope["messages"]
    if not messages:
        return None
    question = route.get("question", "")

    if re.search(r"同秒同文|同一秒.*(?:相同|重复)|重复记录", question):
        groups = {}
        for message in messages:
            groups.setdefault((message["timestamp"], message["text"]), []).append(message)
        selected = [message for group in groups.values() if len(group) > 1 for message in group]
        value = len(selected)
        unit = {
            "text": f"当前筛选范围内共有 {value} 条同秒同文消息，按不同消息 ID 分别计数。",
            "value": value,
            "calculation": "按 timestamp 与 text 分组，保留每条不同 message_id，统计组大小大于 1 的消息数。",
        }
        return _deterministic_result(store, contact_id, route, scope, unit, selected)

    if re.search(r"最长.*间隔|未聊天间隔", question):
        ordered = sorted(messages, key=lambda item: (item["timestamp"], item["id"]))
        pairs = [
            (
                int((datetime.fromisoformat(right["timestamp"]) - datetime.fromisoformat(left["timestamp"])).total_seconds()),
                left,
                right,
            )
            for left, right in zip(ordered, ordered[1:])
        ]
        seconds, left, right = max(pairs, key=lambda item: item[0]) if pairs else (0, ordered[0], ordered[0])
        unit = {
            "text": f"当前筛选范围内最长未聊天间隔为 {seconds} 秒。",
            "value_seconds": seconds,
            "calculation": "按 timestamp、message_id 排序，计算相邻消息时间差并取最大值。",
        }
        return _deterministic_result(store, contact_id, route, scope, unit, [left, right])

    if re.search(r"最活跃.*(?:日期|哪天)|哪天.*最活跃", question):
        by_date = Counter(message["timestamp"][:10] for message in messages)
        date, value = min(by_date.items(), key=lambda item: (-item[1], item[0]))
        selected = [message for message in messages if message["timestamp"].startswith(date)]
        unit = {
            "text": f"当前筛选范围内最活跃日期是 {date}，共有 {value} 条消息。",
            "date": date,
            "value": value,
            "calculation": "按消息日期分组计数；并列时取较早日期。",
        }
        return _deterministic_result(store, contact_id, route, scope, unit, selected)

    if re.search(r"活跃.*(?:多少|几).*天|活跃天数", question):
        dates = sorted({message["timestamp"][:10] for message in messages})
        unit = {
            "text": f"当前筛选范围内共有 {len(dates)} 个活跃日期。",
            "value": len(dates),
            "calculation": "对可解析时间消息的 YYYY-MM-DD 日期去重计数。",
        }
        selected = [
            next(message for message in messages if message["timestamp"].startswith(date))
            for date in dates
        ]
        return _deterministic_result(store, contact_id, route, scope, unit, selected)

    if re.search(r"首次|第一次|最早", question):
        selected = [min(messages, key=lambda item: (item["timestamp"], item["id"]))]
        item = selected[0]
        unit = {
            "text": f"当前已导入记录中最早的一条是 {item['timestamp']}，由{item['speaker']}发送。",
            "value": item["timestamp"],
            "unit_type": "fact",
            "calculation": "在当前筛选范围按 timestamp、message_id 升序取第一条。",
        }
        return _deterministic_result(store, contact_id, route, scope, unit, selected)

    if re.search(r"最后|最近一次|最晚", question):
        selected = [max(messages, key=lambda item: (item["timestamp"], item["id"]))]
        item = selected[0]
        unit = {
            "text": f"当前已导入记录中最后的一条是 {item['timestamp']}，由{item['speaker']}发送。",
            "value": item["timestamp"],
            "unit_type": "fact",
            "calculation": "在当前筛选范围按 timestamp、message_id 降序取第一条。",
        }
        return _deterministic_result(store, contact_id, route, scope, unit, selected)

    if "count_statistics" in intents:
        value = len(messages)
        unit = {
            "text": f"当前筛选范围内共有 {value} 条消息。",
            "value": value,
            "calculation": "统计通过联系人、日期和说话人筛选后的不同 message_id。",
        }
        return _deterministic_result(store, contact_id, route, scope, unit, messages)
    return None


def _topic_summary_result(store, contact_id: int, route: dict, filters: dict):
    """Summarize fixed local topics when the question has no narrower entity."""

    if route.get("intents") != ["topic_summary"] or route.get("entities"):
        return None
    scope = store.message_scope(contact_id, filters)
    overview = topics.build_overview(scope["messages"])
    citations_by_id = {}
    units = []
    for topic in overview["topics"]:
        citation_ids = []
        for message in topic["messages"]:
            citations_by_id[message["id"]] = _citation(message)
            citation_ids.append(message["id"])
        units.append({
            "text": (
                f"{topic['name']}：{topic['message_count']} 条，覆盖 {topic['day_count']} 个日期，"
                f"从 {topic['first_at']} 到 {topic['last_at']}。"
            ),
            "unit_type": "topic_summary",
            "citation_ids": citation_ids,
            "calculation": "使用固定主题词表对当前筛选范围逐条分类；同一消息可以属于多个主题。",
            "uncertainty": [],
        })
    uncertainty = []
    if not units:
        units = [{
            "text": "当前筛选范围内未识别出固定主题；这不表示记录中没有其他话题。",
            "unit_type": "uncertainty",
            "citation_ids": [],
            "calculation": "使用固定主题词表检查当前筛选范围，没有命中已定义主题。",
            "uncertainty": ["固定词表之外的话题可能未被识别。"],
        }]
        uncertainty = ["固定词表之外的话题可能未被识别。"]
    citations = [citations_by_id[item] for item in sorted(citations_by_id)]
    result_scope = {key: value for key, value in scope.items() if key != "messages"}
    result_scope.update({
        "contact_total_messages": scope["total_effective_messages"],
        "candidate_messages": overview["classified_messages"],
        "evidence_messages": len(citations),
        "final_evidence_messages": len(citations),
    })
    return {
        "answer_kind": "deterministic",
        "answer": "\n".join(unit["text"] for unit in units),
        "answer_units": units,
        "citations": citations,
        "scope": result_scope,
        "method": ["fixed_topic_terms"],
        "uncertainty": uncertainty,
    }


def finalize_model_units(units: object, retrieval: dict) -> dict:
    evidence = retrieval.get("evidence", []) if isinstance(retrieval, dict) else []
    contact_id = retrieval.get("scope", {}).get("contact_id") if isinstance(retrieval, dict) else None
    available = {
        message["id"]: message
        for message in evidence
        if isinstance(message, dict)
        and isinstance(message.get("id"), int)
        and (contact_id is None or message.get("contact_id") == contact_id)
    }
    try:
        validated = validate_answer_units(units, available)
    except ValueError as error:
        return evidence_fallback(retrieval, str(error))
    citation_ids = list(dict.fromkeys(
        citation
        for unit in validated
        for citation in unit["citations"]
    ))
    cited_numbers = {int(citation[1:]) for citation in citation_ids}
    citations = [message for message in evidence if message.get("id") in cited_numbers]
    scope = dict(retrieval.get("scope", {}))
    scope["final_evidence_messages"] = len(citations)
    return {
        "answer_kind": "model",
        "answer": "\n".join(unit["text"] for unit in validated),
        "answer_units": validated,
        "citations": citations,
        "scope": scope,
        "method": list(retrieval.get("method", [])) + ["local_model_summary"],
        "uncertainty": [
            unit["uncertainty"] for unit in validated if unit.get("uncertainty")
        ],
    }


def _evidence_result(retrieval):
    evidence = retrieval.get("evidence", [])
    scope = dict(retrieval.get("scope", {}))
    scope["final_evidence_messages"] = len(evidence)
    units = [{
        "text": f"{message['timestamp']} {message['speaker']}：{message['text']}",
        "kind": "evidence_quote",
        "citations": [f"M{message['id']}"],
        "uncertainty": None,
    } for message in evidence]
    if not units:
        units = [{
            "text": retrieval.get("summary", "当前范围未找到相关记录。"),
            "kind": "uncertainty",
            "citations": [],
            "uncertainty": "当前记录范围证据不足。",
        }]
    return {
        "answer_kind": "evidence",
        "answer": retrieval.get("summary", ""),
        "answer_units": units,
        "citations": evidence,
        "scope": scope,
        "method": retrieval.get("method", []),
        "uncertainty": ([] if evidence else ["当前记录范围证据不足。"]),
    }


def _combine_results(deterministic: dict, evidence: dict) -> dict:
    citations_by_id = {
        message["id"]: message
        for result in (deterministic, evidence)
        for message in result.get("citations", [])
    }
    citations = [citations_by_id[item] for item in sorted(citations_by_id)]
    scope = dict(evidence.get("scope", {}))
    scope["final_evidence_messages"] = len(citations)
    combined = {
        "answer_kind": "combined",
        "answer": "\n".join(filter(None, [deterministic.get("answer", ""), evidence.get("answer", "")])),
        "answer_units": list(deterministic.get("answer_units", [])) + list(evidence.get("answer_units", [])),
        "citations": citations,
        "scope": scope,
        "method": list(dict.fromkeys(deterministic.get("method", []) + evidence.get("method", []))),
        "uncertainty": list(dict.fromkeys(
            deterministic.get("uncertainty", []) + evidence.get("uncertainty", [])
        )),
    }
    if evidence.get("degraded_reason"):
        combined["degraded_reason"] = evidence["degraded_reason"]
    return combined


def _entity_time_result(store, contact_id: int, route: dict, filters: dict, retrieval: dict):
    """Calculate first/last mention from the complete retrieved candidate set."""

    if "time_location" not in route.get("intents", []) or not route.get("entities"):
        return None
    question = route.get("question", "")
    if re.search(r"首次|第一次|最早", question):
        bound_key, label = "first_id", "最早"
    elif re.search(r"最后|最近一次|最晚", question):
        bound_key, label = "last_id", "最后"
    else:
        return None
    message_id = retrieval.get("candidate_bounds", {}).get(bound_key)
    if not isinstance(message_id, int):
        return None
    scope = store.message_scope(contact_id, filters)
    item = next((message for message in scope["messages"] if message["id"] == message_id), None)
    if item is None:
        return None
    entity = "、".join(route["entities"])
    unit = {
        "text": f"当前筛选范围内，与“{entity}”相关的{label}一条是 {item['timestamp']}，由{item['speaker']}发送。",
        "value": item["timestamp"],
        "unit_type": "fact",
        "calculation": "先在当前筛选范围检索相关消息，再按 timestamp、message_id 确定时间边界。",
    }
    return _deterministic_result(store, contact_id, route, scope, unit, [item])


def answer_question(
    store,
    contact_id: int,
    question: str,
    filters: dict,
    previous: dict | None,
    summarizer=None,
) -> dict:
    """Single orchestration entry point for every natural-language question."""

    contact = store.contact(contact_id)
    current_rules = rules_version()
    accepted_previous = validate_previous_context(
        previous,
        contact_id=contact_id,
        revision=contact["revision"],
        rules_version=current_rules,
    )
    context_status = "none" if previous is None else ("accepted" if accepted_previous else "stale")
    route = route_question(question, accepted_previous)
    deterministic = answer_deterministic(store, contact_id, route, filters or {})
    if deterministic is None:
        deterministic = _topic_summary_result(store, contact_id, route, filters or {})
    evidence_result = None
    handled_intents = {"time_location", "count_statistics"}
    if deterministic is not None and "topic_summary" in route["intents"] and not route.get("entities"):
        handled_intents.add("topic_summary")
    needs_evidence = any(
        intent not in handled_intents
        for intent in route["intents"]
    )
    if deterministic is None or needs_evidence:
        from search import retrieve_evidence

        retrieval = retrieve_evidence(store, contact_id, question, route, filters or {})
        if deterministic is None:
            deterministic = _entity_time_result(
                store, contact_id, route, filters or {}, retrieval
            )
        evidence_result = _evidence_result(retrieval)
        if summarizer is not None and retrieval["evidence"]:
            model_evidence = select_model_evidence(retrieval["evidence"])
            model_retrieval = {
                **retrieval,
                "evidence": model_evidence,
                "scope": {
                    **retrieval["scope"],
                    "evidence_messages": len(model_evidence),
                },
            }
            if not model_evidence:
                local_retrieval = {
                    **retrieval,
                    "scope": {**retrieval["scope"], "evidence_messages": 0},
                }
                evidence_result = evidence_fallback(
                    local_retrieval,
                    "召回证据超过本地模型输入字符预算，未发送任何聊天证据。",
                )
            else:
                try:
                    if callable(summarizer):
                        model_result = summarizer(question, model_evidence)
                    else:
                        model_result = summarizer.answer_units(question, model_evidence)
                    units = model_result.get("answer_units") if isinstance(model_result, dict) else model_result
                    evidence_result = finalize_model_units(units, model_retrieval)
                except (ValueError, TypeError, RuntimeError, OSError) as error:
                    evidence_result = evidence_fallback(model_retrieval, str(error))

    if deterministic is not None and evidence_result is not None:
        result = _combine_results(deterministic, evidence_result)
    elif deterministic is not None:
        result = deterministic
    else:
        result = evidence_result

    citation_ids = [message["id"] for message in result.get("citations", [])]
    result["next_context"] = {
        "contact_id": contact_id,
        "revision": contact["revision"],
        "rules_version": current_rules,
        "primary_intent": route["primary_intent"],
        "intents": list(route["intents"]),
        "entities": list(route.get("entities", [])),
        "time_expression": route.get("time_expression", ""),
        "speaker_expression": route.get("speaker_expression", ""),
        "citation_ids": citation_ids,
    }
    result["intent"] = route["primary_intent"]
    result["auxiliary_intents"] = [
        intent for intent in route["intents"] if intent != route["primary_intent"]
    ]
    result["route"] = route
    result["context_status"] = context_status
    return result
