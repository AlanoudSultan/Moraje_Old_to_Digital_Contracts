"""Agent 6: Redline. Claude drafts Arabic rewording; the risk score is recomputed in code."""
from __future__ import annotations

import json

from .. import config, mock_llm
from ..templates import template_redlines
from ..llm import call_structured, rules_block, text_block
from ..state import ContractState, RedlineOutput, ScoreItem


def compute_risk(policy_results: list[dict]) -> tuple[int, list[dict]]:
    """Score per rules.json -> scoring: weight per fail / missing-required, halved when uncertain, cap 100."""
    scoring = config.load_rules()["scoring"]
    rules = {r["id"]: r for r in config.load_rules()["rules"]}
    breakdown = []
    for r in policy_results:
        if r["status"] == "fail" or (r["status"] == "missing" and rules.get(r["rule_id"], {}).get("required")):
            points = scoring["weights"][r["severity"]]
            if r.get("confidence", 1.0) < scoring["low_confidence_below"]:
                points *= scoring["low_confidence_multiplier"]
            breakdown.append(ScoreItem(rule_id=r["rule_id"], severity=r["severity"], points=points).model_dump())
    return min(100, round(sum(b["points"] for b in breakdown))), breakdown


def run(state: ContractState) -> dict:
    flagged = [r for r in state["policy_results"] if r["status"] in ("fail", "missing")]
    risk, breakdown = compute_risk(state["policy_results"])
    if not flagged:
        return {"redlines": [], "risk_score": risk, "score_breakdown": breakdown}

    clauses = {c["clause_id"]: c for c in state["digital"]["clauses"]}
    if config.REDLINE_SOURCE == "template":
        return {"redlines": template_redlines(flagged, clauses), "risk_score": risk, "score_breakdown": breakdown}
    related = [{"clause_id": cid, "number_raw": clauses[cid].get("number_raw"), "heading": clauses[cid].get("heading"),
                "text": clauses[cid]["text"]}
               for cid in sorted({f["clause_id"] for f in flagged if f.get("clause_id") in clauses})]
    content = [
        rules_block(),
        text_block("<flagged_policy_results>\n" + json.dumps(flagged, ensure_ascii=False, indent=1)
                   + "\n</flagged_policy_results>\n<related_clauses>\n"
                   + json.dumps(related, ensure_ascii=False, indent=1) + "\n</related_clauses>\n"
                   "اقترح صياغة بديلة لكل نتيجة مخالفة أو مفقودة."),
    ]
    out = call_structured("redline", "redline", content, RedlineOutput, contract_id=state["contract_id"],
                          mock=lambda: mock_llm.redline(flagged, clauses))
    redlines = []
    for r in out.redlines:
        d = r.model_dump()
        # original_ar must be the exact clause text, not the model's recollection of it.
        if d.get("clause_id") in clauses:
            d["original_ar"] = clauses[d["clause_id"]]["text"]
        else:
            d["clause_id"], d["original_ar"] = None, ""
        redlines.append(d)
    return {"redlines": redlines, "risk_score": risk, "score_breakdown": breakdown}
