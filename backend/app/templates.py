"""Fixed-text outputs that replace Claude calls in economy mode.

- Redlines: one approved replacement clause per rule, from config/redline_templates.json.
- Reviewer summary: assembled from the decision reasons and findings.

Both use only facts already computed by code or earlier agents, so they cost nothing and cannot
invent findings.
"""
from __future__ import annotations

import json
from functools import lru_cache

from . import config
from .tools.arabic_text import to_arabic_digits

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


@lru_cache(maxsize=1)
def redline_templates() -> dict:
    return json.loads(config.REDLINE_TEMPLATES_PATH.read_text(encoding="utf-8"))["templates"]


def template_redlines(flagged: list[dict], clauses_by_id: dict[str, dict]) -> list[dict]:
    """One redline per failed or missing rule, wording from the legal team's templates."""
    templates = redline_templates()
    out = []
    for f in sorted(flagged, key=lambda r: SEVERITY_ORDER[r["severity"]]):
        t = templates.get(f["rule_id"])
        if t is None:
            continue
        clause = clauses_by_id.get(f.get("clause_id") or "")
        missing = f["status"] == "missing" or clause is None
        issue = f.get("explanation_ar") or (t["issue_missing_ar"] if missing else t["issue_fail_ar"])
        out.append({
            "clause_id": None if missing else f["clause_id"],
            "rule_id": f["rule_id"],
            "severity": f["severity"],
            "issue_ar": issue,
            "original_ar": "" if missing else clause["text"],
            "suggested_ar": t["suggested_ar"],
            "rationale_ar": t["rationale_ar"],
        })
    return out


def template_summary(reasons: list[str], redlines: list[dict], ocr: dict, low_conf_clauses: list[str],
                     number_of: dict[str, str]) -> str:
    """Reviewer briefing in the structure the decision skill asks for (reason, findings, OCR, action)."""
    lines = ["سبب الإحالة: " + "؛ ".join(reasons[:3]) + "."]
    if redlines:
        lines.append("الملاحظات الجوهرية:")
        for r in sorted(redlines, key=lambda r: SEVERITY_ORDER[r["severity"]])[:5]:
            where = number_of.get(r.get("clause_id") or "", "بند مفقود")
            lines.append(f"- {where}: {r['issue_ar']}")
    ocr_issue = any("الثقة" in r or "الرقمنة" in r or "القراءة" in r for r in reasons)
    if ocr_issue:
        names = "، ".join(low_conf_clauses[:5]) or "عدة مقاطع"
        low = to_arabic_digits(format(ocr.get("min", 0), ".2f"))
        lines.append(f"جودة الرقمنة: أدنى ثقة {low}، والمقاطع منخفضة الثقة: {names}.")
    if ocr_issue and not redlines:
        action = "طلب نسخة أوضح من الأصل"
    elif any(r["severity"] == "critical" for r in redlines):
        action = "إعادة التفاوض"
    elif redlines:
        action = "اعتماد بعد التعديل"
    else:
        action = "التحقق من النسخة الرقمية مقابل الأصل"
    lines.append(f"الإجراء المقترح: {action}.")
    return "\n".join(lines)
