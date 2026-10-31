"""Agent 5: Policy check against config/rules.json. Text only."""
from __future__ import annotations

import json

from .. import config, mock_llm
from ..llm import call_structured, rules_block, text_block
from ..state import ContractState, PolicyOutput, PolicyResult
from ..tools.arabic_text import normalize


def run(state: ContractState) -> dict:
    digital = state["digital"]
    typing = {t["clause_id"]: t for t in state["clause_types"]}
    clauses = [{"clause_id": c["clause_id"], "type": typing.get(c["clause_id"], {}).get("type", "other"),
                "secondary_types": typing.get(c["clause_id"], {}).get("secondary_types", []),
                "heading": c.get("heading"), "text": c["text"]} for c in digital["clauses"]]
    parties = digital.get("parties", [])
    content = [
        rules_block(),
        text_block("<clauses>\n" + json.dumps(clauses, ensure_ascii=False, indent=1) + "\n</clauses>\n"
                   "<parties>\n" + json.dumps(parties, ensure_ascii=False) + "\n</parties>\n"
                   "<missing_types>" + json.dumps(state.get("missing_types", [])) + "</missing_types>\n"
                   "طبّق كل قاعدة من rules_json على البنود وأعد نتيجة واحدة لكل قاعدة."),
    ]
    out = call_structured("policy", "policy", content, PolicyOutput, contract_id=state["contract_id"],
                          mock=lambda: mock_llm.policy(clauses, state["clause_types"], digital))
    return {"policy_results": [r.model_dump() for r in _validate(out.policy_results, digital)]}


def _validate(results: list[PolicyResult], digital: dict) -> list[PolicyResult]:
    """Enforce the contract with the rules file: one result per rule, rule severity, real evidence."""
    rules = {r["id"]: r for r in config.load_rules()["rules"]}
    texts = {c["clause_id"]: c["text"] for c in digital["clauses"]}
    by_rule: dict[str, PolicyResult] = {}
    for r in results:
        if r.rule_id in rules and r.rule_id not in by_rule:
            by_rule[r.rule_id] = r
    fixed = []
    for rule_id, rule in rules.items():
        r = by_rule.get(rule_id)
        if r is None:
            # A rule the model did not answer is treated as an uncertain failure, never as a pass.
            r = PolicyResult(rule_id=rule_id, clause_id=None, status="missing" if rule["required"] else "not_applicable",
                             severity=rule["severity"], evidence_ar="",
                             explanation_ar="لم يتمكن الوكيل من تقييم هذه القاعدة", confidence=0.3)
        r.severity = rule["severity"]
        if r.clause_id and r.clause_id not in texts:
            r.clause_id = None
        if r.evidence_ar and r.clause_id:
            if normalize(r.evidence_ar) not in normalize(texts[r.clause_id]):
                r.confidence = min(r.confidence, 0.5)
                r.explanation_ar += " (الاقتباس غير مطابق حرفياً لنص البند)"
        fixed.append(r)
    return fixed
