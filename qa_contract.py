"""Strict, atomic contract for model-produced answer units."""

from __future__ import annotations

import copy
import json
import re


FACTUAL_KINDS = {"fact", "quote", "comparison", "summary"}
ALLOWED_KINDS = FACTUAL_KINDS | {"uncertainty"}
REQUIRED_EVIDENCE_FIELDS = {
    "id", "contact_id", "timestamp", "speaker", "text", "source_id",
    "source_name", "line_start", "line_end",
}
CITATION = re.compile(r"^M([1-9]\d*)$")
MODEL_MAX_EVIDENCE = 12
MODEL_MAX_EVIDENCE_CHARACTERS = 24000


def model_evidence_records(evidence: list[dict]) -> list[dict]:
    return [{
        "id": f"M{message['id']}",
        "speaker": message["speaker"],
        "time": message["timestamp"],
        "text": message["text"],
    } for message in evidence]


def serialized_model_evidence(evidence: list[dict]) -> str:
    return json.dumps(model_evidence_records(evidence), ensure_ascii=False)


def select_model_evidence(evidence: list[dict]) -> list[dict]:
    """Choose the actual model payload within both count and character limits."""

    selected = []
    for message in evidence:
        if len(selected) >= MODEL_MAX_EVIDENCE:
            break
        candidate = selected + [message]
        if len(serialized_model_evidence(candidate)) <= MODEL_MAX_EVIDENCE_CHARACTERS:
            selected.append(message)
    return selected


def validate_answer_units(units: object, available: dict[int, dict]) -> list[dict]:
    if not isinstance(units, list) or not 1 <= len(units) <= 20:
        raise ValueError("模型回答 JSON 必须包含 1–20 个 answer_units。")
    if not isinstance(available, dict):
        raise ValueError("可用证据集合格式无效。")
    validated = []
    for index, unit in enumerate(units, 1):
        if not isinstance(unit, dict):
            raise ValueError(f"第 {index} 个回答单元结构无效。")
        text = unit.get("text")
        kind = unit.get("kind")
        citations = unit.get("citations")
        uncertainty = unit.get("uncertainty")
        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            raise ValueError(f"第 {index} 个回答单元文本无效。")
        if kind not in ALLOWED_KINDS:
            raise ValueError(f"第 {index} 个回答单元类型无效。")
        if not isinstance(citations, list) or any(not isinstance(item, str) for item in citations):
            raise ValueError(f"第 {index} 个回答单元引用格式无效。")
        if uncertainty is not None and not isinstance(uncertainty, str):
            raise ValueError(f"第 {index} 个回答单元不确定性格式无效。")
        if not citations:
            raise ValueError(f"第 {index} 个回答单元缺少引用。")
        normalized = []
        for citation in citations:
            match = CITATION.fullmatch(citation)
            if not match:
                raise ValueError(f"第 {index} 个回答单元引用格式无效。")
            message_id = int(match.group(1))
            message = available.get(message_id)
            if message is None:
                raise ValueError(f"模型生成了不存在的引用 {citation}。")
            missing = REQUIRED_EVIDENCE_FIELDS - set(message)
            if missing or not message.get("source_id") or not message.get("source_name"):
                raise ValueError(f"引用 {citation} 缺少完整来源定位。")
            if citation not in normalized:
                normalized.append(citation)
        validated.append({
            "text": text.strip(),
            "kind": kind,
            "citations": normalized,
            "uncertainty": uncertainty.strip() if isinstance(uncertainty, str) else None,
        })
    return validated


def evidence_fallback(retrieval: dict, reason: str) -> dict:
    evidence = retrieval.get("evidence", []) if isinstance(retrieval, dict) else []
    safe_evidence = [
        copy.deepcopy(message)
        for message in evidence
        if isinstance(message, dict)
        and not (REQUIRED_EVIDENCE_FIELDS - set(message))
        and message.get("source_id")
        and message.get("source_name")
    ]
    units = [{
        "text": f"{message['timestamp']} {message['speaker']}：{message['text']}",
        "kind": "evidence_quote",
        "citations": [f"M{message['id']}"],
        "uncertainty": None,
    } for message in safe_evidence]
    if not units:
        units = [{
            "text": "当前范围没有可安全展示的完整来源证据。",
            "kind": "uncertainty",
            "citations": [],
            "uncertainty": "证据或来源定位不完整。",
        }]
    summary = retrieval.get("summary") if isinstance(retrieval, dict) else ""
    scope = copy.deepcopy(retrieval.get("scope", {})) if isinstance(retrieval, dict) else {}
    scope["final_evidence_messages"] = len(safe_evidence)
    return {
        "answer_kind": "evidence",
        "answer": summary or "模型结论不可用，已降级为原文证据。",
        "answer_units": units,
        "citations": safe_evidence,
        "scope": scope,
        "method": list(retrieval.get("method", [])) + ["atomic_model_downgrade"] if isinstance(retrieval, dict) else ["atomic_model_downgrade"],
        "uncertainty": ["模型输出未通过逐单元引用校验，未展示任何模型结论。"],
        "degraded_reason": str(reason),
    }
