"""Short Arabic review report (.docx): decision, reasons, risk, findings, redlines."""
from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Cm, RGBColor

from .. import config
from .arabic_text import gregorian_today_ar, hijri_today_ar, to_arabic_digits
from .docx_builder import GRAY, GREEN, _section_rtl, _set_bidi, _table_rtl, add_par, add_run, pick_font

RED = RGBColor(0xB4, 0x23, 0x18)
ORANGE = RGBColor(0xB5, 0x47, 0x08)
STATUS_AR = {"pass": "مطابق", "fail": "مخالف", "missing": "مفقود", "not_applicable": "لا ينطبق"}
SEVERITY_AR = {"critical": "حرجة", "high": "عالية", "medium": "متوسطة", "low": "منخفضة"}
HUMAN_AR = {"approve": "اعتماد", "reject": "رفض", "request_changes": "طلب تعديلات"}


def build_report(state: dict, out_dir: Path) -> Path:
    font = pick_font()
    doc = Document()
    sec = doc.sections[0]
    for side in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
        setattr(sec, side, Cm(2.2))
    _section_rtl(sec)
    rules = {r["id"]: r for r in config.load_rules()["rules"]}
    digital = state.get("digital") or {}
    clauses = {c["clause_id"]: c for c in digital.get("clauses", [])}
    label = lambda cid: (clauses[cid].get("number_raw") or clauses[cid].get("heading") or cid) if cid in clauses else "-"

    add_par(doc, "تقرير مراجعة عقد", font, 18, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER, color=GREEN)
    add_par(doc, f"{config.PRODUCT_NAME_AR} - {gregorian_today_ar()} الموافق {hijri_today_ar()}", font, 10,
            align=WD_ALIGN_PARAGRAPH.CENTER, color=GRAY)
    add_par(doc, f"العقد: {digital.get('title') or state.get('file_name')}", font, 12, bold=True)
    add_par(doc, f"الملف: {state.get('file_name')}  |  رقم الطلب: {state.get('contract_id')}", font, 10, color=GRAY)

    decision = state.get("decision")
    p = add_par(doc, "", font, 13)
    add_run(p, "القرار الآلي: ", font, 13, bold=True)
    add_run(p, "معتمد تلقائياً" if decision == "approve" else "محال للمراجع", font, 13, bold=True,
            color=GREEN if decision == "approve" else ORANGE)
    hr = state.get("human_review")
    if hr:
        p = add_par(doc, "", font, 12)
        add_run(p, "قرار المراجع: ", font, 12, bold=True)
        add_run(p, HUMAN_AR.get(hr["decision"], hr["decision"]), font, 12)
        if hr.get("notes"):
            add_par(doc, f"ملاحظات المراجع: {hr['notes']}", font, 11)

    ocr = state.get("ocr_confidence") or {}
    add_par(doc, f"درجة المخاطر: {to_arabic_digits(state.get('risk_score', '-'))} من ١٠٠   |   "
                 f"ثقة الرقمنة الدنيا: {to_arabic_digits(format(ocr.get('min', 0), '.2f'))}   |   "
                 f"المتوسط: {to_arabic_digits(format(ocr.get('mean', 0), '.2f'))}", font, 11)

    if state.get("decision_reasons"):
        add_par(doc, "أسباب الإحالة", font, 13, bold=True, space_after=2)
        for r in state["decision_reasons"]:
            add_par(doc, f"- {r}", font, 11, space_after=1)
    if state.get("reviewer_summary_ar"):
        add_par(doc, "ملخص للمراجع", font, 13, bold=True, space_after=2)
        for line in state["reviewer_summary_ar"].splitlines():
            add_par(doc, line, font, 11, space_after=1)

    results = state.get("policy_results") or []
    if results:
        add_par(doc, "نتائج فحص السياسات", font, 13, bold=True, space_after=4)
        t = doc.add_table(rows=1, cols=5)
        t.style = "Table Grid"
        _table_rtl(t)
        for cell, head in zip(t.rows[0].cells, ["القاعدة", "البند", "الحالة", "الخطورة", "الدليل / التوضيح"]):
            cp = cell.paragraphs[0]
            _set_bidi(cp)
            add_run(cp, head, font, 10, bold=True)
        for r in results:
            row = t.add_row().cells
            values = [f"{r['rule_id']} - {rules.get(r['rule_id'], {}).get('title_ar', '')}", label(r.get("clause_id")),
                      STATUS_AR[r["status"]], SEVERITY_AR[r["severity"]],
                      (r.get("evidence_ar") or "") + ("\n" if r.get("evidence_ar") else "") + r.get("explanation_ar", "")]
            for i, (cell, v) in enumerate(zip(row, values)):
                cp = cell.paragraphs[0]
                _set_bidi(cp)
                color = None
                if i == 2:
                    color = GREEN if r["status"] == "pass" else (RED if r["status"] in ("fail", "missing") else GRAY)
                add_run(cp, v, font, 9.5, color=color)

    redlines = state.get("redlines") or []
    if redlines:
        add_par(doc, "", font, 6)
        add_par(doc, "التعديلات المقترحة", font, 13, bold=True, space_after=4)
        for r in redlines:
            add_par(doc, f"{label(r.get('clause_id')) if r.get('clause_id') else 'بند جديد'} "
                         f"({r['rule_id']} - {SEVERITY_AR[r['severity']]}): {r['issue_ar']}", font, 11, bold=True,
                    space_after=2)
            if r.get("original_ar"):
                p = add_par(doc, "", font, 11, space_after=2)
                add_run(p, "النص الحالي: ", font, 10, bold=True, color=GRAY)
                run = add_run(p, r["original_ar"], font, 10.5, color=RED)
                run.font.strike = True
            p = add_par(doc, "", font, 11, space_after=2)
            add_run(p, "الصياغة المقترحة: ", font, 10, bold=True, color=GRAY)
            add_run(p, r["suggested_ar"], font, 10.5, color=GREEN)
            add_par(doc, f"المبرر: {r['rationale_ar']}", font, 10, color=GRAY, space_after=8)

    add_par(doc, "", font, 6)
    add_par(doc, "تنبيه: هذا التقرير أداة دعم قرار وليس استشارة قانونية. القواعد المستخدمة توضيحية ويجب أن "
                 "تعتمدها الإدارة القانونية في الجهة.", font, 9, color=GRAY)
    out = out_dir / "review_report.docx"
    doc.save(out)
    return out
