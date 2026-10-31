"""Agent 7: Decision. The route is plain code over rules.json thresholds; Haiku only writes the summary."""
from __future__ import annotations

import json

from .. import config, mock_llm
from ..templates import template_summary
from ..llm import call_structured, text_block
from ..state import NON_CONTRACT_DOC_TYPES, ContractState, DecisionSummary
from ..tools.arabic_text import to_arabic_digits


def _fmt(x: float) -> str:
    return to_arabic_digits(f"{x:.2f}")


def route(state: ContractState) -> tuple[str, list[str]]:
    """approve only if every condition holds; every failed condition becomes a reason."""
    th = config.thresholds()
    reasons: list[str] = []
    intake = state.get("intake") or {}
    if state.get("errors"):
        reasons.append("حدث خطأ أثناء المعالجة الآلية ولا يجوز الاعتماد التلقائي")
    if intake and not intake.get("readable", False):
        if intake.get("doc_type") == "not_a_contract":
            reasons.append("المستند لا يبدو عقداً")
        else:
            reasons.append("المستند غير مقروء بدرجة كافية للرقمنة")
    if not intake:
        reasons.append("لم يكتمل فحص الاستلام")

    ocr = state.get("ocr_confidence")
    if ocr:
        if ocr["min"] < th["min_block_confidence_for_auto_approve"]:
            reasons.append(f"ثقة الرقمنة الدنيا {_fmt(ocr['min'])} أقل من الحد المطلوب "
                           f"{_fmt(th['min_block_confidence_for_auto_approve'])}")
        if ocr["mean"] < th["min_mean_confidence_for_auto_approve"]:
            reasons.append(f"متوسط ثقة الرقمنة {_fmt(ocr['mean'])} أقل من الحد المطلوب "
                           f"{_fmt(th['min_mean_confidence_for_auto_approve'])}")
    elif intake.get("readable"):
        reasons.append("لم تكتمل الرقمنة")

    non_contract = intake.get("doc_type") in NON_CONTRACT_DOC_TYPES
    unreadable_text = bool(ocr) and ocr["mean"] < config.REVIEW_MIN_OCR_MEAN and "risk_score" not in state
    if unreadable_text:
        reasons.append(f"جودة القراءة منخفضة جداً (متوسط الثقة {_fmt(ocr['mean'])})، فلم تُجرَ المراجعة القانونية "
                       "ويلزم نسخة أوضح من الأصل")
    elif non_contract:
        kind = "صك ملكية" if intake["doc_type"] == "property_deed" else "وثيقة قضائية"
        reasons.append(f"المستند {kind} وليس عقداً، فلا تنطبق قواعد مراجعة العقود ويلزم تحقق بشري من النسخة الرقمية")
    elif "risk_score" in state:
        if state["risk_score"] > th["max_risk_score_for_auto_approve"]:
            reasons.append(f"درجة المخاطر {to_arabic_digits(state['risk_score'])} أعلى من الحد المسموح "
                           f"{to_arabic_digits(th['max_risk_score_for_auto_approve'])}")
    elif intake.get("readable"):
        reasons.append("لم تكتمل المراجعة القانونية")

    if th.get("critical_finding_forces_escalation", True):
        critical = [r["rule_id"] for r in state.get("policy_results", [])
                    if r["severity"] == "critical" and r["status"] in ("fail", "missing")]
        if critical:
            reasons.append("ملاحظات حرجة: " + "، ".join(critical))
    return ("approve" if not reasons else "escalate"), reasons


def run(state: ContractState) -> dict:
    decision, reasons = route(state)
    if decision == "approve":
        return {"decision": decision, "decision_reasons": reasons, "reviewer_summary_ar": ""}

    digital = state.get("digital") or {}
    clauses = {c["clause_id"]: c for c in digital.get("clauses", [])}
    number_of = {cid: (c.get("number_raw") or c.get("heading") or "«" + " ".join(c["text"].split()[:4]) + "...»")
                 for cid, c in clauses.items()}
    ocr = state.get("ocr_confidence") or {"min": 0.0, "mean": 0.0, "low_conf_block_ids": []}
    low_blocks = set(ocr.get("low_conf_block_ids", []))
    low_clauses = [number_of[c["clause_id"]] for c in clauses.values() if low_blocks & set(c["block_ids"])]
    redlines = state.get("redlines", [])
    payload = {
        "title": digital.get("title"), "parties": digital.get("parties", []), "decision_reasons": reasons,
        "risk_score": state.get("risk_score"), "ocr_confidence": ocr, "low_confidence_clauses": low_clauses,
        "redlines": [{"clause": number_of.get(r.get("clause_id") or "", "بند مفقود"), "severity": r["severity"],
                      "rule_id": r["rule_id"], "issue_ar": r["issue_ar"]} for r in redlines],
        "policy_results": [{**r, "clause": number_of.get(r.get("clause_id") or "", "")}
                           for r in state.get("policy_results", []) if r["status"] in ("fail", "missing")],
        "intake_issues": (state.get("intake") or {}).get("issues_ar", []),
        "errors": state.get("errors", []),
    }
    if config.SUMMARY_SOURCE == "template":
        summary = template_summary(reasons, redlines, ocr, low_clauses, number_of)
        return {"decision": decision, "decision_reasons": reasons, "reviewer_summary_ar": summary}
    content = [text_block("<input>\n" + json.dumps(payload, ensure_ascii=False, indent=1) + "\n</input>")]
    try:
        summary = call_structured(
            "decision", "decision", content, DecisionSummary, contract_id=state["contract_id"],
            mock=lambda: mock_llm.summary(reasons, redlines, ocr, low_clauses, number_of),
        ).reviewer_summary_ar
    except Exception:
        # The summary is a convenience; escalation must still happen without it.
        summary = mock_llm.summary(reasons, redlines, ocr, low_clauses, number_of)["reviewer_summary_ar"]
    return {"decision": decision, "decision_reasons": reasons, "reviewer_summary_ar": summary}
