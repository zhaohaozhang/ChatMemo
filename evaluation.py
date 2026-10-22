"""Reproducible evaluator for the bundled, entirely fictional chat corpus."""

from __future__ import annotations

import argparse
import json
import re
import tempfile
from pathlib import Path

from parser import parse_text
from qa import answer_question
from store import Store


REQUIRED_CASE_FIELDS = {
    "id", "question", "allowed_intents", "allowed_answers", "required_citations",
    "forbidden_claims", "acceptable_uncertainty", "scope",
}
ALL_INTENTS = {
    "time_location", "count_statistics", "exact_quote", "semantic_search",
    "context_explanation", "change_comparison", "topic_summary", "evidence_status",
}
CITATION = re.compile(r"^M([1-9]\d*)$")
PRIVATE_PATH = re.compile(r"(?:[A-Za-z]:\\|/Users/|/home/)")


def load_cases(path: Path) -> list[dict]:
    path = Path(path)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("无法读取虚构评测案例。") from error
    if not isinstance(document, dict) or document.get("fixture_type") != "synthetic_chat_qa":
        raise ValueError("评测文件必须明确标记为 synthetic_chat_qa。")
    contact_name = document.get("contact_name")
    if not isinstance(contact_name, str) or "虚构" not in contact_name:
        raise ValueError("评测联系人必须明确标记为虚构。")
    cases = document.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("评测案例不能为空。")
    chat_path = path.parent / "qa_synthetic_chat.txt"
    try:
        chat_text = chat_path.read_text(encoding="utf-8")
    except OSError as error:
        raise ValueError("缺少配套的虚构聊天记录。") from error
    parsed = parse_text(chat_text)
    message_count = len(parsed["messages"])
    if PRIVATE_PATH.search(json.dumps(document, ensure_ascii=False)):
        raise ValueError("虚构评测不得包含本机绝对路径。")
    seen = set()
    for case in cases:
        if not isinstance(case, dict) or not REQUIRED_CASE_FIELDS.issubset(case):
            raise ValueError("评测案例缺少必需字段。")
        if not isinstance(case["id"], str) or not case["id"] or case["id"] in seen:
            raise ValueError("评测案例 ID 必须非空且唯一。")
        seen.add(case["id"])
        if not isinstance(case["question"], str) or not case["question"].strip():
            raise ValueError("评测问题不能为空。")
        for field in ("allowed_intents", "allowed_answers", "required_citations", "forbidden_claims", "acceptable_uncertainty"):
            if not isinstance(case[field], list) or any(not isinstance(item, str) for item in case[field]):
                raise ValueError(f"评测字段 {field} 格式无效。")
        if not case["allowed_intents"] or not set(case["allowed_intents"]).issubset(ALL_INTENTS):
            raise ValueError("评测意图无效。")
        if not case["allowed_answers"] or not isinstance(case["scope"], dict):
            raise ValueError("评测允许答案或 scope 无效。")
        for citation in case["required_citations"]:
            match = CITATION.fullmatch(citation)
            if not match or int(match.group(1)) > message_count:
                raise ValueError(f"评测引用不存在：{citation}")
        previous = case.get("previous_case")
        if previous is not None and previous not in seen:
            raise ValueError("多轮案例必须引用之前已定义的案例。")
    return cases


def _answer_citations(answer):
    citations = set()
    for item in answer.get("citations", []):
        if isinstance(item, dict) and isinstance(item.get("id"), int):
            citations.add(f"M{item['id']}")
        elif isinstance(item, str) and CITATION.fullmatch(item):
            citations.add(item)
    return citations


def evaluate_case(case: dict, answer: dict) -> list[str]:
    errors = []
    combined = "\n".join(
        [str(answer.get("answer", ""))]
        + [str(unit.get("text", "")) for unit in answer.get("answer_units", []) if isinstance(unit, dict)]
    )
    if answer.get("intent") not in case["allowed_intents"]:
        errors.append("wrong_intent")
    if not any(allowed in combined for allowed in case["allowed_answers"]):
        errors.append("missing_allowed_answer")
    citations = _answer_citations(answer)
    if not set(case["required_citations"]).issubset(citations):
        errors.append("missing_required_citation")
    if any(claim and claim in combined for claim in case["forbidden_claims"]):
        errors.append("forbidden_claim")
    expected_uncertainty = case["acceptable_uncertainty"]
    actual_uncertainty = "\n".join(str(item) for item in answer.get("uncertainty", []))
    if expected_uncertainty and not any(item in actual_uncertainty for item in expected_uncertainty):
        errors.append("missing_uncertainty")
    actual_scope = answer.get("scope", {})
    if any(actual_scope.get(key) != value for key, value in case["scope"].items()):
        errors.append("scope_mismatch")
    if answer.get("answer_kind") == "model":
        factual = {"fact", "quote", "comparison", "summary"}
        units = answer.get("answer_units")
        if not isinstance(units, list) or any(
            not isinstance(unit, dict)
            or (unit.get("kind") in factual and not unit.get("citations"))
            for unit in (units or [])
        ):
            errors.append("partial_model_answer")
    return list(dict.fromkeys(errors))


def run_evaluation(fixtures: Path) -> dict:
    fixtures = Path(fixtures)
    cases = load_cases(fixtures / "qa_cases.json")
    chat = (fixtures / "qa_synthetic_chat.txt").read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / "evaluation.sqlite3")
        imported = store.import_text("qa_synthetic_chat.txt", chat, chat.encode("utf-8"), name="小周（虚构）")
        contact_id = imported["contact_id"]
        prior_answers = {}
        results = []
        for case in cases:
            previous_id = case.get("previous_case")
            previous = prior_answers[previous_id]["next_context"] if previous_id else None
            answer = answer_question(
                store,
                contact_id,
                case["question"],
                case.get("filters", {}),
                previous,
            )
            prior_answers[case["id"]] = answer
            issues = evaluate_case(case, answer)
            results.append({
                "id": case["id"],
                "passed": not issues,
                "errors": issues,
                "answer_kind": answer.get("answer_kind"),
                "intent": answer.get("intent"),
                "answer": answer.get("answer"),
                "citations": sorted(_answer_citations(answer)),
                "scope": answer.get("scope", {}),
                "uncertainty": answer.get("uncertainty", []),
            })
    return {
        "fixture_type": "synthetic_chat_qa",
        "total": len(results),
        "passed": sum(item["passed"] for item in results),
        "failed": sum(not item["passed"] for item in results),
        "cases": results,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="运行完全虚构的聊天问答评测")
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args(argv)
    report = run_evaluation(options.fixtures)
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("total", "passed", "failed")}, ensure_ascii=False))
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
