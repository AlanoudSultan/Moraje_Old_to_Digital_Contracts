"""Deeds and court records: fill the user's deed template (نموذج الصك) from the OCR lines, let the
reviewer edit or skip each field, and render the digital copy from the template.

Field keys mirror the template's eight sections. Values come from one text-only Claude call over
the OCR lines, with the same anti-fabrication rule as the rest of the pipeline: a value must be an
exact substring of the lines it cites (except a few classification fields), otherwise it is kept
but marked low-confidence for the reviewer. A field's confidence is the weakest cited line's.
"""
from __future__ import annotations

import html
import json
import re

from pydantic import BaseModel

from . import config, db
from .tools.arabic_text import count_ar, normalize

STYLE_PATH = config.BACKEND_DIR / "assets" / "templates" / "deed_template_style.css"

# ---------- field spec (template order) ----------

SECTIONS = [
    ("header", "الترويسة"), ("basic", "البيانات الأساسية"), ("parties", "الأطراف / المُلّاك"),
    ("property", "بيانات العقار"), ("bounds", "الحدود والأطوال"), ("text", "نص الصك"),
    ("special", "بنود خاصة بنوع الصك"), ("closing", "الخاتمة والتوثيق"),
]

FIXED: list[tuple[str, str]] = [
    ("header.court", "اسم المحكمة أو كتابة العدل"), ("header.title", "عنوان المستند"),
    ("header.deed_number", "رقم الصك (التسجيل)"), ("header.date_hijri", "التاريخ الهجري"),
    ("header.date_gregorian", "التاريخ الميلادي"), ("header.volume", "رقم الجلد"), ("header.page", "رقم الصفحة"),
    ("basic.deed_type", "نوع الصك / العملية"), ("basic.city", "المدينة"), ("basic.prev_deed_number", "رقم الصك السابق"),
    ("basic.prev_deed_date", "تاريخ الصك السابق"), ("basic.district", "الحي"), ("basic.status", "حالة الصك"),
    ("basic.restrictions", "القيود على الصك"),
    ("property.real_estate_id", "رقم الهوية العقارية"), ("property.type", "نوع العقار"),
    ("property.usage", "نوع الاستخدام"), ("property.plot", "رقم القطعة"), ("property.plan", "رقم المخطط"),
    ("property.district_city", "الحي والمدينة"), ("property.area_number", "المساحة رقماً"),
    ("property.area_words", "المساحة كتابةً"), ("property.deal_value", "قيمة الصفقة"),
    ("property.description", "وصف العقار وموقعه"),
    ("text.opening", "عبارة الافتتاح"), ("text.judge", "القاضي وصفته"),
    ("text.subject", "موضوع الصك (الإقرار / الدعوى / الطلب)"), ("text.ownership_doc", "مستند التملك"),
    ("waqf.name", "اسم الوقف"), ("waqf.beneficiaries", "الموقوف عليهم"), ("waqf.revenue", "مصارف الريع"),
    ("waqf.nazir", "الناظر وشروط النظارة"), ("waqf.nazir_share", "نسبة الناظر من الريع"),
    ("waqf.on_extinction", "عند انقطاع الموقوف عليهم"),
    ("inherit.deceased", "المتوفى"), ("inherit.death_date", "تاريخ الوفاة"),
    ("inherit.heirs", "الورثة وصلتهم وهوياتهم"), ("inherit.shares", "الأنصبة"),
    ("sale.seller", "البائع"), ("sale.buyer", "المشتري"), ("sale.price", "الثمن"), ("sale.payment", "طريقة السداد"),
    ("mortgage.mortgagee", "الجهة المرتهنة"), ("mortgage.amount", "مبلغ الرهن"),
    ("closing.verdict", "عبارة الحكم أو الإثبات"), ("closing.date", "تاريخ التحرير"),
    ("closing.judge_name", "اسم القاضي / كاتب العدل"), ("closing.margins", "الهوامش والتهميشات"),
    ("closing.appeal_head", "رئيس محكمة التمييز"),
]
PARTY_FIELDS = [("role", "الصفة"), ("name", "الاسم الكامل"), ("id_number", "رقم الهوية / الحفيظة"),
                ("nationality", "الجنسية"), ("share", "النسبة ٪"), ("agency", "رقم الوكالة وتاريخها")]
PERSON_FIELDS = [("name", "الاسم"), ("id_number", "رقم الهوية"), ("role", "الصفة")]
SIDES = [("north", "شمالاً"), ("south", "جنوباً"), ("east", "شرقاً"), ("west", "غرباً")]
MAX_PARTIES, MAX_PEOPLE, MAX_CONDITIONS = 6, 4, 12
CONDITION_ORDINALS = ["أولاً", "ثانياً", "ثالثاً", "رابعاً", "خامساً", "سادساً", "سابعاً", "ثامناً", "تاسعاً",
                      "عاشراً", "حادي عشر", "ثاني عشر"]

# Classification fields may be a standard word rather than a quote from the page.
FREE_KEYS = re.compile(r"^(basic\.deed_type|basic\.status|property\.type|property\.usage|header\.title|"
                       r"parties\.\d+\.role|text\.(attendees|witnesses)\.\d+\.role)$")


def all_keys() -> dict[str, tuple[str, str]]:
    """key -> (section, Arabic label), in template order."""
    out: dict[str, tuple[str, str]] = {}
    section_of = {"header": "header", "basic": "basic", "property": "property", "text": "text",
                  "waqf": "special", "inherit": "special", "sale": "special", "mortgage": "special", "closing": "closing"}
    for key, label in FIXED:
        out[key] = (section_of[key.split(".")[0]], label)
        if key == "property.description":
            for side, side_ar in SIDES:
                out[f"bounds.{side}.description"] = ("bounds", f"{side_ar}: وصف الحد")
                out[f"bounds.{side}.length"] = ("bounds", f"{side_ar}: الطول (م)")
        if key == "basic.restrictions":
            for i in range(MAX_PARTIES):
                for f, f_ar in PARTY_FIELDS:
                    out[f"parties.{i}.{f}"] = ("parties", f"الطرف {i + 1}: {f_ar}")
        if key == "text.judge":
            for group, g_ar in (("attendees", "الحاضر"), ("witnesses", "المعرّف / الشاهد")):
                for i in range(MAX_PEOPLE):
                    for f, f_ar in PERSON_FIELDS:
                        out[f"text.{group}.{i}.{f}"] = ("text", f"{g_ar} {i + 1}: {f_ar}")
        if key == "waqf.beneficiaries":
            for i in range(MAX_CONDITIONS):
                out[f"waqf.conditions.{i}"] = ("special", f"شروط الوقف: {CONDITION_ORDINALS[i]}")
    return out


KEYS = all_keys()


# ---------- extraction ----------

class ExtractedField(BaseModel):
    key: str
    value: str
    line_ids: list[str]


class DeedExtraction(BaseModel):
    fields: list[ExtractedField]


def _lines(state: dict) -> list[dict]:
    return [l for p in state.get("ocr_pages") or [] for l in p.get("lines", [])]


def _key_list_for_prompt() -> str:
    return "\n".join(f"{k}: {label}" for k, (_, label) in KEYS.items())


def extract(state: dict) -> list[dict]:
    """Template fields filled from the OCR lines: [{key, section, label, value, confidence, line_ids}]."""
    from .llm import call_structured, text_block

    lines = _lines(state)
    if not lines:
        return []
    payload = json.dumps([{"id": l["id"], "text": l["text"], "confidence": l["confidence"]} for l in lines],
                         ensure_ascii=False, indent=1)
    content = [text_block("<keys>\n" + _key_list_for_prompt() + "\n</keys>", cache=True),
               text_block("<lines>\n" + payload + "\n</lines>")]
    out = call_structured("deed", "deed", content, DeedExtraction, contract_id=state.get("contract_id"),
                          mock=lambda: _rule_extraction(state, lines))
    return validate(out.fields, lines)


def _rule_extraction(state: dict, lines: list[dict]) -> DeedExtraction:
    """Without Claude: map the structuring step's key fields and a few obvious patterns."""
    by_label = {"رقم الصك": "header.deed_number", "تاريخ الصك": "header.date_hijri", "المحكمة": "header.court",
                "اسم المالك": "parties.0.name", "رقم الهوية": "parties.0.id_number", "المساحة": "property.area_number",
                "الحي": "basic.district", "المدينة": "basic.city", "رقم القطعة": "property.plot",
                "رقم المخطط": "property.plan"}
    fields = []
    for k in (state.get("digital") or {}).get("key_fields", []):
        key = by_label.get(k["label"])
        if key:
            src = next((l["id"] for l in lines if k["value"] in l["text"]), None)
            fields.append(ExtractedField(key=key, value=k["value"], line_ids=[src] if src else []))
    for l in lines:
        if "الحمد لله" in l["text"] and not any(f.key == "text.opening" for f in fields):
            fields.append(ExtractedField(key="text.opening", value=l["text"], line_ids=[l["id"]]))
    return DeedExtraction(fields=fields)


def validate(fields: list[ExtractedField], lines: list[dict]) -> list[dict]:
    """Keep known keys once; confidence = weakest cited line; non-quotes are flagged, never trusted."""
    by_id = {l["id"]: l for l in lines}
    seen, out = set(), []
    for f in fields:
        if f.key not in KEYS or f.key in seen or not f.value.strip():
            continue
        seen.add(f.key)
        cited = [by_id[i] for i in f.line_ids if i in by_id]
        conf = min((l["confidence"] for l in cited), default=0.3)
        joined = normalize(" ".join(l["text"] for l in cited))
        quoted = bool(cited) and normalize(f.value) in joined
        if not quoted and not FREE_KEYS.match(f.key):
            conf = min(conf, 0.3)  # not found in the cited lines: the reviewer must check it
        if "[؟]" in f.value:
            conf = min(conf, 0.5)
        section, label = KEYS[f.key]
        out.append({"key": f.key, "section": section, "label": label, "value": f.value.strip(),
                    "confidence": round(conf, 3), "line_ids": [l["id"] for l in cited], "quoted": quoted})
    return out


# ---------- reviewer edits ----------

def current_fields(contract_id: str, extracted: list[dict]) -> list[dict]:
    """Extracted fields with the reviewer's edits and skips applied, plus fields the reviewer added."""
    edits = db.deed_edits(contract_id)
    th = config.thresholds()["block_highlight_below"]
    by_key = {f["key"]: dict(f, status="auto") for f in extracted}
    for key, e in edits.items():
        if key not in KEYS:
            continue
        f = by_key.get(key) or {"key": key, "section": KEYS[key][0], "label": KEYS[key][1], "value": "",
                                "confidence": None, "line_ids": [], "quoted": False}
        f = dict(f, status=e["status"])
        if e["status"] == "edited":
            f["value"] = e["value"] or ""
        by_key[key] = f
    order = {k: i for i, k in enumerate(KEYS)}
    out = sorted(by_key.values(), key=lambda f: order[f["key"]])
    for f in out:
        f["low"] = f["status"] == "auto" and (f["confidence"] or 0) < th
    return out


# ---------- rendering (the user's template, filled) ----------

def _filled(fields: dict, key: str) -> bool:
    f = fields.get(key)
    return bool(f and f["status"] != "skipped" and (f["value"] or "").strip())


def _v(fields: dict, key: str) -> str:
    """Cell HTML for a filled field; low-confidence values highlighted. Callers only ask for filled keys."""
    f = fields[key]
    text = html.escape(f["value"]).replace("\n", "<br>")
    return f'<mark title="يحتاج تحقق">{text}</mark>' if f.get("low") else text


def _has(fields: dict, prefix: str) -> bool:
    return any(k.startswith(prefix) and _filled(fields, k) for k in fields)


def render_html(contract_id: str, fields_list: list[dict], source_name: str) -> str:
    """The user's template filled with the current values. Anything not filled (or skipped) is left
    out entirely: fields, table rows and columns, sub-blocks, and whole sections."""
    from .tools.arabic_text import gregorian_today_ar, hijri_today_ar

    F = {f["key"]: f for f in fields_list}
    css = STYLE_PATH.read_text(encoding="utf-8")
    v = lambda k: _v(F, k)  # noqa: E731
    filled = lambda k: _filled(F, k)  # noqa: E731

    def section(title: str, body: str) -> str:
        return f'<section class="sec"><h2>{title}</h2>{body}</section>' if body else ""

    def kv(items: list[tuple[str, str]], full_keys: set[str] = frozenset()) -> str:
        """Label/value grid, two per row; long values span the row. Only filled items."""
        pairs = [(k, l) for k, l in items if filled(k) and k not in full_keys]
        fulls = [(k, l) for k, l in items if filled(k) and k in full_keys]
        if not pairs and not fulls:
            return ""
        cells = []
        for i, (k, label) in enumerate(pairs):
            cls = []
            if i == len(pairs) - 1 and len(pairs) % 2:  # odd one out spans the row
                cls.append("full")
            cells.append((cls, k, label))
        cells += [(["full"], k, label) for k, label in fulls]
        # The bottom row has no bottom border.
        last_row = 1 if "full" in cells[-1][0] else 2
        for c in cells[-last_row:]:
            c[0].append("last")
        return '<div class="box kv">' + "".join(
            f'<div class="{" ".join(c)}"><span class="l">{label}</span><span class="v">{v(k)}</span></div>'
            for c, k, label in cells) + "</div>"

    def table(columns: list[tuple[str, str]], rows: list[dict[str, str]], widths: dict[str, str], style: str) -> str:
        """Grid table keeping only rows with something filled and columns with something filled."""
        rows = [r for r in rows if any(filled(k) for k in r.values() if k)]
        cols = [(c, h) for c, h in columns if any(r.get(c) and filled(r[c]) for r in rows)]
        if not rows or not cols:
            return ""
        tmpl = " ".join(widths.get(c, "1fr") for c, _ in cols)
        head = "".join(f'<span class="h">{h}</span>' for _, h in cols)
        body = "".join("".join(f"<span>{v(r[c]) if r.get(c) and filled(r[c]) else ''}</span>" for c, _ in cols)
                       for r in rows)
        return f'<div class="tb" style="grid-template-columns:{tmpl};{style}">{head}{body}</div>'

    def para(key: str, title: str) -> str:
        return f'<div class="para"><span class="t">{title}</span><span class="c">{v(key)}</span></div>' if filled(key) else ""

    def li(key: str, label: str) -> str:
        return f"<li>{label}: {v(key)}</li>" if filled(key) else ""

    # ---- header
    hdr_cols = [("header.deed_number", "رقم الصك (التسجيل)"), ("header.date_hijri", "التاريخ الهجري"),
                ("header.date_gregorian", "التاريخ الميلادي"), ("header.volume", "رقم الجلد"), ("header.page", "رقم الصفحة")]
    hdr_table = table([(k, h) for k, h in hdr_cols], [{k: k for k, _ in hdr_cols}], {"header.deed_number": "1.3fr"},
                      "border:1px solid #C9D3CF;border-left:0;border-bottom:0;text-align:center")
    court = f'<span style="font-weight:500;color:#3D4844">{v("header.court")}</span>' if filled("header.court") else ""
    title = (f'<h1 style="margin:0;font-size:24px;font-weight:700;color:#0B5A3C">{v("header.title")}</h1>'
             if filled("header.title") else "")

    # ---- body sections
    basic = section("البيانات الأساسية", kv([
        ("basic.deed_type", "نوع الصك / العملية"), ("basic.city", "المدينة"), ("basic.prev_deed_number", "رقم الصك السابق"),
        ("basic.prev_deed_date", "تاريخ الصك السابق"), ("basic.district", "الحي"), ("basic.status", "حالة الصك"),
        ("basic.restrictions", "القيود على الصك")], {"basic.restrictions"}))
    parties = section("الأطراف / المُلّاك", table(
        PARTY_FIELDS, [{f: f"parties.{i}.{f}" for f, _ in PARTY_FIELDS} for i in range(MAX_PARTIES)],
        {"role": "0.9fr", "name": "1.7fr", "id_number": "1.5fr", "nationality": "0.8fr", "share": "0.7fr", "agency": "1.2fr"},
        "border:1px solid #C9D3CF;border-left:0;border-top:0").replace('class="tb"', 'class="box tb"', 1))
    prop = section("بيانات العقار", kv([
        ("property.real_estate_id", "رقم الهوية العقارية"), ("property.type", "نوع العقار"), ("property.usage", "نوع الاستخدام"),
        ("property.plot", "رقم القطعة"), ("property.plan", "رقم المخطط"), ("property.district_city", "الحي والمدينة"),
        ("property.area_number", "المساحة رقماً"), ("property.area_words", "المساحة كتابةً"),
        ("property.deal_value", "قيمة الصفقة"), ("property.description", "وصف العقار وموقعه")],
        {"property.deal_value", "property.description"}))
    bounds_rows = [{"side": None, "description": f"bounds.{s}.description", "length": f"bounds.{s}.length", "_ar": a}
                   for s, a in SIDES]
    bounds_rows = [r for r in bounds_rows if filled(r["description"]) or filled(r["length"])]
    bounds = ""
    if bounds_rows:
        has_len = any(filled(r["length"]) for r in bounds_rows)
        has_desc = any(filled(r["description"]) for r in bounds_rows)
        cols = ["1fr"] + (["3fr"] if has_desc else []) + (["1fr"] if has_len else [])
        head = '<span class="h">الجهة</span>' + ('<span class="h">وصف الحد</span>' if has_desc else "") + \
               ('<span class="h">الطول (م)</span>' if has_len else "")
        body = "".join(f"<span>{r['_ar']}</span>"
                       + (f"<span>{v(r['description']) if filled(r['description']) else ''}</span>" if has_desc else "")
                       + (f"<span>{v(r['length']) if filled(r['length']) else ''}</span>" if has_len else "")
                       for r in bounds_rows)
        bounds = section("الحدود والأطوال", f'<div class="box tb" style="grid-template-columns:{" ".join(cols)};'
                                           f'border-left:0">{head}{body}</div>')

    def people(group: str, title: str) -> str:
        t = table(PERSON_FIELDS, [{f: f"text.{group}.{i}.{f}" for f, _ in PERSON_FIELDS} for i in range(MAX_PEOPLE)],
                  {"name": "2fr", "id_number": "1.5fr", "role": "1fr"},
                  "border:1px solid #C9D3CF;border-left:0;border-bottom:0;margin-top:4px")
        return f'<div class="para"><span class="t">{title}</span>{t}</div>' if t else ""

    text_parts = "".join([para("text.opening", "عبارة الافتتاح"), para("text.judge", "القاضي وصفته"),
                          people("attendees", "الحاضرون / المقرّون"), people("witnesses", "المعرّفون / الشهود"),
                          para("text.subject", "موضوع الصك (الإقرار / الدعوى / الطلب)"),
                          para("text.ownership_doc", "مستند التملك")])
    text = section("نص الصك", f'<div class="box">{text_parts}</div>' if text_parts else "")

    special = []
    conds = "".join(f"<li><b>{CONDITION_ORDINALS[i]}:</b> {v(f'waqf.conditions.{i}')}</li>"
                    for i in range(MAX_CONDITIONS) if filled(f"waqf.conditions.{i}"))
    waqf_items = "".join([li("waqf.name", "اسم الوقف"), li("waqf.beneficiaries", "الموقوف عليهم"),
                          f'<li>شروط الوقف:<ol class="conds">{conds}</ol></li>' if conds else "",
                          li("waqf.revenue", "مصارف الريع"), li("waqf.nazir", "الناظر وشروط النظارة"),
                          li("waqf.nazir_share", "نسبة الناظر من الريع"),
                          li("waqf.on_extinction", "عند انقطاع الموقوف عليهم")])
    if waqf_items:
        special.append(f'<div class="sub" style="grid-column:1/-1"><h3>صك وقف</h3><ul>{waqf_items}</ul></div>')
    inherit = "".join([li("inherit.deceased", "المتوفى"), li("inherit.death_date", "تاريخ الوفاة"),
                       li("inherit.heirs", "الورثة وصلتهم وهوياتهم"), li("inherit.shares", "الأنصبة")])
    if inherit:
        special.append(f'<div class="sub"><h3>صك حصر ورثة</h3><ul>{inherit}</ul></div>')
    sale = "".join([li("sale.seller", "البائع"), li("sale.buyer", "المشتري"), li("sale.price", "الثمن"),
                    li("sale.payment", "طريقة السداد")])
    if sale:
        special.append(f'<div class="sub"><h3>صك بيع / إفراغ</h3><ul>{sale}</ul></div>')
    mortgage = "".join([li("mortgage.mortgagee", "الجهة المرتهنة"), li("mortgage.amount", "مبلغ الرهن")])
    if mortgage:
        special.append(f'<div class="sub" style="grid-column:1/-1"><h3>صك رهن</h3><ul>{mortgage}</ul></div>')
    if len(special) == 1:  # a lone block takes the full width
        special[0] = special[0].replace('<div class="sub">', '<div class="sub" style="grid-column:1/-1">', 1)
    special_html = section("بنود خاصة بنوع الصك",
                           f'<div class="box" style="padding:14px;display:grid;grid-template-columns:1fr 1fr;gap:12px">'
                           f'{"".join(special)}</div>' if special else "")

    closing_parts = "".join([para("closing.verdict", "عبارة الحكم أو الإثبات"), para("closing.date", "تاريخ التحرير"),
                             para("closing.judge_name", "القاضي / كاتب العدل"),
                             para("closing.margins", "الهوامش والتهميشات"),
                             para("closing.appeal_head", "رئيس محكمة التمييز")])
    closing = section("الخاتمة والتوثيق", f'<div class="box">{closing_parts}</div>' if closing_parts else "")

    low_count = sum(1 for f in fields_list if f.get("low") and _filled(F, f["key"]))
    banner = (f'<div class="notice">{count_ar(low_count, "highlighted_field")} بالأصفر، تحقق منها مقابل الأصل.</div>'
              if low_count else "")
    body = "".join([basic, parties, prop, bounds, text, special_html, closing])
    if not body:
        body = '<div class="notice">لم تُعبَّأ أي حقول بعد.</div>'

    return f'''<!DOCTYPE html>
<html lang="ar" dir="rtl"><head><meta charset="utf-8"><title>النسخة الرقمية - {html.escape(source_name)}</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Arabic:wght@400;500;600;700&display=swap">
<style>{css}
.page{{--accent:#0B5A3C;--tint:#EAF2EE;width:794px;min-height:600px;box-sizing:border-box;padding:40px 44px;margin:0 auto;background:#FFFFFF;font-family:'IBM Plex Sans Arabic',sans-serif;font-size:13px;color:#1C2321;display:flex;flex-direction:column;gap:20px}}
mark{{background:#FFF3A3;padding:0 2px;border-radius:2px}}
.notice{{background:#FFFAEB;border:1px solid #FEDF89;color:#B54708;border-radius:6px;padding:8px 12px;font-size:12px}}
.stamp{{font-size:12px;color:#0B5A3C;font-weight:700}} .conds{{margin:4px 0 0;padding-inline-start:18px}}
.box .para:last-child{{border-bottom:0}}
@media print{{.notice{{display:none}} body{{background:#fff}} .page{{width:auto;padding:24px}}}}
body{{background:#F3F4F6}}
</style></head><body>
<div class="page">
<header style="display:flex;flex-direction:column;gap:14px;padding-bottom:16px;border-bottom:2px solid #0B5A3C">
<div style="display:grid;grid-template-columns:1fr auto 1fr;align-items:center;gap:16px">
<div style="display:flex;flex-direction:column;gap:2px;font-weight:600;font-size:14px;line-height:1.7">
<span>المملكة العربية السعودية</span><span>وزارة العدل</span>{court}</div>
<div style="display:flex;flex-direction:column;align-items:center;gap:4px;text-align:center">{title}
<span class="stamp">نسخة رقمية</span></div>
<div style="text-align:left;font-size:11px;color:#5B6762">المصدر: {html.escape(source_name)}<br>
تاريخ المعالجة: {gregorian_today_ar()}<br>{hijri_today_ar()}</div></div>
{hdr_table}
</header>
{banner}
{body}
<div style="margin-top:auto;padding-top:12px;border-top:1px solid #C9D3CF;font-size:11px;color:#5B6762;text-align:center">
<p style="margin:0;font-weight:600;color:#8A3B12">نسخة رقمية مستخرجة آلياً من صورة الأصل لأغراض المراجعة، وليست مستنداً رسمياً ولا بديلاً عن الأصل.</p>
<p style="margin:4px 0 0">رقم الطلب: {html.escape(contract_id)}</p></div>
</div></body></html>'''
