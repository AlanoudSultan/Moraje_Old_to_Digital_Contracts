"""Deterministic stand-ins for the Claude agents, used when no API key is configured (MOCK_LLM).

They let the full pipeline, UI, and tests run offline. Output quality is far below the real
agents; the UI labels results produced this way as "وضع تجريبي بدون نموذج".
"""
from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path

from . import config
from .tools.arabic_text import normalize, parse_clause_number, to_arabic_digits, to_western_digits

# ---------- shared helpers ----------


def _has(text: str, *needles: str) -> bool:
    n = normalize(text)
    return any(normalize(x) in n for x in needles)


_NUMBER_WORDS = {
    "سبعه": 7, "عشره": 10, "خمسه عشر": 15, "ثلاثين": 30, "خمسه واربعين": 45, "ستين": 60,
    "تسعين": 90, "مائه وعشرين": 120, "ماىه وعشرين": 120, "مائه": 100,
}


def extract_days(text: str) -> list[int]:
    n = normalize(text)
    days = [int(m) for m in re.findall(r"(\d+)\s*(?:يوم|يوما)", n)]
    for word, value in sorted(_NUMBER_WORDS.items(), key=lambda kv: -len(kv[0])):
        if re.search(word + r"\s*(?:يوم|يوما)", n):
            days.append(value)
            n = n.replace(word, " ")
    return days


def _evidence(text: str, *needles: str) -> str:
    """Shortest exact span of the original text around the first matching needle (max ~25 words)."""
    for sentence in re.split(r"(?<=[.،])\s+", text):
        if any(normalize(x) in normalize(sentence) for x in needles):
            words = sentence.split()
            return " ".join(words[:25])
    return " ".join(text.split()[:20])


# ---------- intake ----------


def intake(file_type: str, has_text_layer: bool, page_count: int, mean_brightness: float, sharpness: float) -> dict:
    scanned = not has_text_layer
    quality = 0.97 if not scanned else max(0.35, min(0.9, sharpness))
    issues = []
    if scanned:
        issues.append("المستند صورة ممسوحة ضوئياً وليس نصاً رقمياً")
        if sharpness < 0.75:
            issues.append("وضوح الصورة منخفض وقد يؤثر على دقة القراءة")
    return {
        "is_scanned": scanned, "quality_score": round(quality, 2), "doc_type": "other_contract",
        "language": "ar", "page_count_seen": min(page_count, config.INTAKE_MAX_PAGES), "readable": quality >= 0.3,
        "issues_ar": issues,
    }


# ---------- digitization ----------


@lru_cache(maxsize=1)
def _truth_index() -> dict[str, dict]:
    index = {}
    for f in Path(config.SAMPLES_DIR).glob("*.truth.json"):
        data = json.loads(f.read_text(encoding="utf-8"))
        index[data["sha256"]] = data
    return index


def file_sha256(path: str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def digitize_page(source_sha: str, page_no: int, text_layer: str, page_blocks_geom: list[tuple]) -> dict:
    truth = _truth_index().get(source_sha)
    if truth and page_no <= len(truth["pages"]):
        return {"key_fields": [], **truth["pages"][page_no - 1]}
    if text_layer.strip():
        return _digitize_from_text(page_no, text_layer, page_blocks_geom)
    return {
        "title": None, "parties": [], "key_fields": [], "tables": [], "dates": [], "stamps": [],
        "page_header_footer": [], "blocks": [{
            "id": f"p{page_no}_b1", "kind": "other", "number_raw": None, "heading": None,
            "text": "[؟] لا يمكن قراءة هذه الصفحة في الوضع التجريبي بدون نموذج.",
            "continues_from_previous_page": False, "continues_to_next_page": False,
            "confidence": 0.2, "bbox": [0.05, 0.05, 0.95, 0.95],
        }],
    }


def _digitize_from_text(page_no: int, text_layer: str, geom: list[tuple]) -> dict:
    blocks = []
    for i, (bbox, text) in enumerate(geom or [((0.05, 0.05, 0.95, 0.95), text_layer)], start=1):
        text = " ".join(text.split())
        if not text:
            continue
        number, _ = parse_clause_number(text)
        kind = "clause" if number else ("title" if page_no == 1 and i == 1 else "paragraph")
        heading = None
        body = text
        if number and ":" in text[:80]:
            head, _, body = text.partition(":")
            heading = head.replace(number, "").strip() or None
        blocks.append({
            "id": f"p{page_no}_b{len(blocks) + 1}", "kind": kind, "number_raw": number, "heading": heading,
            "text": body.strip(), "continues_from_previous_page": False, "continues_to_next_page": False,
            "confidence": 0.9, "bbox": list(bbox),
        })
    return {"title": blocks[0]["text"] if blocks and blocks[0]["kind"] == "title" else None, "parties": [],
            "key_fields": [],
            "blocks": blocks, "tables": [], "dates": [], "stamps": [], "page_header_footer": []}


# ---------- clause typing ----------

SIGNALS = [
    ("data_protection", ["البيانات الشخصية", "حماية البيانات"]),
    ("governing_law", ["النظام الواجب التطبيق", "القانون الواجب التطبيق", "يخضع هذا العقد", "تخضع"]),
    ("disputes", ["المنازعات", "التحكيم", "المحكمة المختصة", "المحاكم المختصة", "خلاف ينشأ"]),
    ("confidentiality", ["السرية", "سرية", "عدم الإفصاح"]),
    ("force_majeure", ["القوة القاهرة"]),
    ("assignment", ["التنازل", "من الباطن"]),
    ("penalties", ["غرامة", "غرامات", "جزاء"]),
    ("liability", ["المسؤولية", "التعويض", "الأضرار"]),
    ("termination", ["إنهاء", "الإنهاء", "فسخ"]),
    ("payment", ["الدفع", "قيمة العقد", "المقابل المالي", "الأجرة", "فاتورة", "ريال"]),
    ("term", ["مدة العقد", "مدة الإيجار", "مدة هذا العقد", "يسري", "يتجدد"]),
    ("parties", ["الطرف الأول", "المؤجر", "سجل تجاري", "هوية وطنية"]),
    ("subject", ["موضوع العقد", "نطاق العمل", "أجر الطرف الأول", "يلتزم الطرف الثاني بتوريد"]),
    ("ip", ["الملكية الفكرية"]),
    ("notices", ["الإشعارات", "المراسلات"]),
]

CORE_TYPES = ["parties", "subject", "term", "payment", "termination", "liability", "governing_law",
              "disputes", "confidentiality", "data_protection"]


def classify(clauses: list[dict]) -> dict:
    out = []
    for c in clauses:
        heading = c.get("heading") or ""
        text = c["text"]
        hits = [t for t, kws in SIGNALS if _has(heading, *kws)]
        body_hits = [t for t, kws in SIGNALS if _has(text, *kws) and t not in hits]
        types = hits + body_hits
        if not types:
            types = ["other"]
        primary = types[0]
        secondary = [t for t in types[1:3] if t != primary]
        out.append({"clause_id": c["clause_id"], "type": primary, "secondary_types": secondary,
                    "confidence": 0.9 if hits else 0.7})
    covered = {o["type"] for o in out} | {s for o in out for s in o["secondary_types"]}
    return {"clause_types": out, "missing_types": [t for t in CORE_TYPES if t not in covered]}


# ---------- policy ----------


def _check(rule_id: str, text: str, digital: dict) -> tuple[bool, list[str], str]:
    """Returns (passes, evidence needles, Arabic explanation)."""
    if rule_id == "PARTY-01":
        ok = all(p.get("id_number") for p in digital.get("parties", [])) and len(digital.get("parties", [])) >= 2
        return ok, ["سجل تجاري", "هوية", "الطرف"], ("الأطراف محددون بالاسم ورقم السجل أو الهوية" if ok
                                                    else "أحد الأطراف غير محدد برقم سجل تجاري أو هوية")
    if rule_id == "TERM-01":
        renew = _has(text, "يتجدد")
        days = extract_days(text)
        ok = _has(text, "تبدأ", "يسري", "مدة") and (not renew or (days and min(days) >= 30))
        return ok, ["يتجدد", "تبدأ", "مدة"], ("المدة محددة وشروط التجديد مقبولة" if ok
                                               else "مهلة الإشعار بعدم التجديد أقل من ٣٠ يوماً أو المدة غير محددة")
    if rule_id == "PAY-01":
        days = extract_days(text)
        ok = bool(days) and max(days) <= 60 and _has(text, "ريال") and _has(text, "ضريبة")
        why = "مدة السداد ضمن ٦٠ يوماً والمبلغ محدد بالريال مع بيان الضريبة" if ok else (
            "مدة السداد تتجاوز ٦٠ يوماً" if days and max(days) > 60 else "لم يحدد العقد مدة السداد أو حالة ضريبة القيمة المضافة")
        return ok, ["يوماً", "ريال", "فاتورة"], why
    if rule_id == "TERMN-01":
        ok = _has(text, "لأي من الطرفين", "يحق لكل طرف", "لكل من الطرفين") and not _has(text, "لا يحق للطرف")
        return ok, ["إنهاء", "فسخ"], ("حق الإنهاء متاح للطرفين مع مهلة تصحيح" if ok
                                      else "حق الإنهاء غير متوازن بين الطرفين")
    if rule_id == "LIAB-01":
        ok = _has(text, "لا تتجاوز", "حد أقصى", "الحد الأقصى") and not _has(text, "غير المحدودة", "دون حد")
        return ok, ["المسؤولية"], ("المسؤولية محددة بسقف" if ok else "المسؤولية غير محدودة على أحد الطرفين")
    if rule_id == "CONF-01":
        ok = _has(text, "كل طرف", "الطرفان", "الطرفين") and _has(text, "بعد انتهاء", "سنوات")
        return ok, ["سرية"], ("التزام السرية متبادل ويستمر بعد انتهاء العقد" if ok
                              else "التزام السرية على طرف واحد أو لا يستمر بعد انتهاء العقد")
    if rule_id == "DATA-01":
        ok = _has(text, "نظام حماية البيانات الشخصية")
        return ok, ["البيانات"], ("يلتزم الطرفان بنظام حماية البيانات الشخصية" if ok
                                  else "لا يشير البند إلى نظام حماية البيانات الشخصية")
    if rule_id == "LAW-01":
        ok = _has(text, "المملكة العربية السعودية") and not _has(text, "إنجلترا", "ويلز", "نيويورك", "فرنسا")
        return ok, ["يخضع"], ("النظام الواجب التطبيق هو أنظمة المملكة" if ok else "العقد يخضع لقانون أجنبي")
    if rule_id == "DISP-01":
        ok = (_has(text, "المحاكم المختصة", "المحكمة المختصة", "المركز السعودي للتحكيم")
              and not _has(text, "لندن", "باريس", "دبي"))
        return ok, ["المنازعات", "المحكمة", "التحكيم", "خلاف"], ("تسوية المنازعات داخل المملكة" if ok
                                                                 else "مقر تسوية المنازعات خارج المملكة")
    if rule_id == "PEN-01":
        ok = not _has(text, "دون حد أقصى") and (_has(text, "بحد أقصى عشرة", "حد أقصى") or not _has(text, "غرامة"))
        return ok, ["غرامة"], ("الغرامات ضمن سقف ١٠٪" if ok else "غرامات التأخير بلا سقف")
    if rule_id == "FM-01":
        return True, ["القوة القاهرة"], "يوجد بند للقوة القاهرة يشمل الطرفين"
    if rule_id == "ASSIGN-01":
        ok = _has(text, "موافقة") and not _has(text, "دون الحاجة")
        return ok, ["التنازل", "من الباطن"], ("التنازل مشروط بموافقة كتابية" if ok
                                              else "يجيز البند التنازل دون موافقة الطرف الآخر")
    return True, [], ""


def policy(clauses: list[dict], typing: list[dict], digital: dict) -> dict:
    rules = config.load_rules()["rules"]
    by_id = {c["clause_id"]: c for c in clauses}
    results = []
    for rule in rules:
        matches = [t["clause_id"] for t in typing
                   if t["type"] == rule["clause_type"] or rule["clause_type"] in t["secondary_types"]]
        if not matches:
            results.append({
                "rule_id": rule["id"], "clause_id": None,
                "status": "missing" if rule["required"] else "not_applicable",
                "severity": rule["severity"], "evidence_ar": "",
                "explanation_ar": f"لا يتضمن العقد بنداً بشأن {rule['title_ar']}.", "confidence": 0.85,
            })
            continue
        cid = matches[0]
        text = " ".join(by_id[m]["text"] for m in matches)
        ok, needles, why = _check(rule["id"], text, digital)
        clause_conf = by_id[cid].get("confidence", 1.0)
        results.append({
            "rule_id": rule["id"], "clause_id": cid, "status": "pass" if ok else "fail",
            "severity": rule["severity"], "evidence_ar": _evidence(by_id[cid]["text"], *needles),
            "explanation_ar": why, "confidence": round(min(0.9, clause_conf), 2),
        })
    return {"policy_results": results}


# ---------- redline ----------

def redline(flagged: list[dict], clauses_by_id: dict[str, dict]) -> dict:
    from .templates import template_redlines

    return {"risk_score": 0, "score_breakdown": [], "redlines": template_redlines(flagged, clauses_by_id)}


# ---------- decision summary ----------


def summary(reasons: list[str], redlines: list[dict], ocr: dict, low_conf_clauses: list[str],
            number_of: dict[str, str]) -> dict:
    from .templates import template_summary

    return {"reviewer_summary_ar": template_summary(reasons, redlines, ocr, low_conf_clauses, number_of)}


__all__ = ["intake", "digitize_page", "classify", "policy", "redline", "summary", "file_sha256",
           "extract_days", "to_western_digits"]
