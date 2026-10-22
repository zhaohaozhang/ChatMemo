"""Transparent local lexical search, not an embedding or language-model engine."""
import re
from collections import Counter
from datetime import date

GROUPS = [
    ("喝", "饮料", "饮品", "咖啡", "茶", "奶茶", "果汁"),
    ("吃", "食物", "口味", "火锅", "烧烤", "辣", "甜"),
    ("工作", "上班", "下班", "值班", "加班", "排班", "职业", "跳槽", "换工作", "新机会"),
    ("休息", "放假", "周末", "休假", "休班"),
    ("见面", "约定", "约好", "碰面", "见一面"),
    ("喜欢", "更喜欢", "偏爱", "爱喝"),
    ("生日", "出生", "生辰"),
    ("讨厌", "不喜欢", "不爱", "不想"),
]
STOP = re.compile(r"之前|以前|现在|最近|什么时候|什么|有没有|怎么|哪里|哪个|哪些|告诉我|帮我|说过|提过|提到|说的|记得|一下|这个人|对方")
QUOTED = re.compile(r"[‘\"“]([^’\"”]{1,200})[’\"”]")


def query_terms(query):
    clean = STOP.sub(" ", query.lower())
    words = re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", clean)
    primary = []
    for word in words:
        word = word.strip("她他我的了呢吗啊呀是很会能和与")
        if not word:
            continue
        if len(word) <= 4 or word.isascii():
            primary.append(word)
        else:
            primary.extend(word[i:i+2] for i in range(len(word)-1))
    expanded = []
    for group in GROUPS:
        if any(word in clean for word in group):
            expanded.extend(word for word in group if len(word) > 1 or word in ("茶", "辣", "甜"))
    return list(dict.fromkeys(primary)), list(dict.fromkeys(expanded))


def validate_dates(date_from, date_to):
    for value in (date_from, date_to):
        if value:
            try:
                date.fromisoformat(value)
            except ValueError:
                raise ValueError("日期格式应为 YYYY-MM-DD。")
    if date_from and date_to and date_from > date_to:
        raise ValueError("开始日期不能晚于结束日期。")


def _rank_candidates(rows, query, exact_phrase):
    primary, expanded = query_terms(query)
    frequencies = Counter()
    for row in rows:
        text = row["text"].lower()
        for term in primary:
            if term in text:
                frequencies[term] += 1
    ranked = []
    for row in rows:
        text = row["text"].lower()
        matched = [term for term in primary if term in text]
        related = [term for term in expanded if term in text]
        exact = bool(exact_phrase and exact_phrase.lower() in text)
        if not (matched or related or exact):
            continue
        score = (20 if exact else 0)
        score += sum(3 + min(3, len(rows) / (frequencies[term] + 1) / 30) for term in matched)
        score += min(6, len(related) * 1.5)
        if re.search(r"(^|[，。！？\s])(我|本人)", row["text"]):
            score += 2
        if re.search(r"(^|[，。！？\s])(她|他|朋友|同事)", row["text"]):
            score -= 4
        ranked.append({
            **row,
            "score": round(score, 2),
            "matched_terms": list(dict.fromkeys(matched + related)),
            "match_type": "exact_quote" if exact else "similar_expression",
        })
    ranked.sort(key=lambda item: (item["score"], item["timestamp"], item["id"]), reverse=True)
    return ranked, primary, expanded


def _diverse_selection(ranked, limit, change_comparison):
    if not ranked or limit <= 0:
        return []
    if not change_comparison or len(ranked) < 2:
        return ranked[:limit]
    chronological = sorted(ranked, key=lambda item: (item["timestamp"], item["id"]))
    selected = [chronological[0]]
    if chronological[-1]["id"] != chronological[0]["id"]:
        selected.append(chronological[-1])
    selected_ids = {item["id"] for item in selected}
    selected.extend(item for item in ranked if item["id"] not in selected_ids)
    return selected[:limit]


def retrieve_evidence(store, contact_id, query, route, filters, limit=30):
    if not isinstance(query, str) or not query.strip() or len(query) > 1000:
        raise ValueError("请输入 1–1000 字的问题。")
    if not isinstance(route, dict) or not isinstance(route.get("intents"), list):
        raise ValueError("问题路由格式无效。")
    if not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("证据条数应为 1–100。")
    filters = filters or {}
    date_from = filters.get("date_from", "")
    date_to = filters.get("date_to", "")
    speaker = filters.get("speaker", "")
    validate_dates(date_from, date_to)
    scope_data = store.message_scope(contact_id, {
        "date_from": date_from,
        "date_to": date_to,
        "speaker": speaker,
    })
    rows = scope_data["messages"]
    retrieval_query = query
    if "entities" in route.get("inherited_fields", []) and route.get("entities"):
        retrieval_query += " " + " ".join(route["entities"])
    quoted = QUOTED.search(query)
    exact_phrase = quoted.group(1).strip() if quoted else ""
    ranked, primary, expanded = _rank_candidates(rows, retrieval_query, exact_phrase)
    chronological_candidates = sorted(
        ranked,
        key=lambda item: (item["timestamp"], item["id"]),
    )
    candidate_bounds = {
        "first_id": chronological_candidates[0]["id"],
        "last_id": chronological_candidates[-1]["id"],
    } if chronological_candidates else {}
    selected = _diverse_selection(
        ranked,
        limit,
        "change_comparison" in route["intents"],
    )

    evidence_by_id = {item["id"]: item for item in selected}
    if "context_explanation" in route["intents"] and selected:
        rows_by_position = {(item["source_id"], item["sequence"]): item for item in rows}
        for match in list(selected):
            for sequence in range(match["sequence"] - 3, match["sequence"] + 4):
                neighbor = rows_by_position.get((match["source_id"], sequence))
                if neighbor is None or neighbor["id"] in evidence_by_id:
                    continue
                if len(evidence_by_id) >= limit:
                    break
                evidence_by_id[neighbor["id"]] = {
                    **neighbor,
                    "score": 0,
                    "matched_terms": [],
                    "match_type": "context",
                }
    evidence = sorted(
        evidence_by_id.values(),
        key=lambda item: (item["timestamp"], item["id"]),
    )
    exact_matches = [item for item in selected if item["match_type"] == "exact_quote"]
    related_matches = [item for item in selected if item["match_type"] == "similar_expression"]
    if exact_matches:
        status = "direct_evidence"
        summary = f"当前范围找到 {len(exact_matches)} 条逐字原话，并附上相关上下文。"
    elif related_matches:
        status = "similar_expression"
        summary = f"当前范围找到 {len(related_matches)} 条相近表达；它们不是逐字原话。"
    else:
        status = "current_scope_not_found"
        summary = "当前范围未找到相关记录；当前数据不足以判断记录范围之外的情况。"
    return {
        "query": query,
        "status": status,
        "summary": summary,
        "exact_matches": exact_matches,
        "related_matches": related_matches,
        "evidence": evidence,
        "scope": {
            "contact_id": contact_id,
            "date_from": date_from,
            "date_to": date_to,
            "speaker": speaker,
            "contact_total_messages": scope_data["total_effective_messages"],
            "filtered_messages": scope_data["filtered_messages"],
            "candidate_messages": len(ranked),
            "evidence_messages": len(evidence),
            "timestamped_messages": scope_data["timestamped_messages"],
            "untimestamped_messages": scope_data["untimestamped_messages"],
        },
        "terms": {"primary": primary, "expanded": expanded},
        "candidate_bounds": candidate_bounds,
        "method": ["exact_phrase", "lexical_ngram", "fixed_synonyms", "source_context"],
    }


def search(store, contact_id, query, date_from="", date_to="", speaker="", limit=30):
    retrieval = retrieve_evidence(
        store,
        contact_id,
        query,
        {"intents": ["semantic_search"]},
        {"date_from": date_from, "date_to": date_to, "speaker": speaker},
        limit=limit,
    )
    return {
        **retrieval,
        "results": retrieval["evidence"],
        "total_matches": retrieval["scope"]["candidate_messages"],
        "mode": "local",
    }
