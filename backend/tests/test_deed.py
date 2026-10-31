"""Deed template: field validation, reviewer edits, rendering, pipeline routing, API."""
from fastapi.testclient import TestClient

from backend.app import db, deed, graph, mock_llm
from backend.app.deed import ExtractedField, current_fields, render_html, validate

LINES = [
    {"id": "p1_l1", "text": "المملكة العربية السعودية وزارة العدل المحكمة الكبرى بالمدينة المنورة", "confidence": 0.88},
    {"id": "p1_l2", "text": "رقم التسجيل ٢/٧/١١ وتاريخ ١٤٠٦/٢/١١هـ", "confidence": 0.7},
    {"id": "p1_l3", "text": "أولاً: ما يبدأ به من الغلة تعمير الوقف وترميمه", "confidence": 0.88},
]


def test_validate_keeps_quotes_and_flags_inventions():
    out = validate([
        ExtractedField(key="header.court", value="المحكمة الكبرى بالمدينة المنورة", line_ids=["p1_l1"]),
        ExtractedField(key="header.deed_number", value="٢/٧/١١", line_ids=["p1_l2"]),
        ExtractedField(key="sale.price", value="٥٠٠٬٠٠٠ ريال", line_ids=["p1_l2"]),  # not on the page
        ExtractedField(key="basic.deed_type", value="وقف", line_ids=["p1_l3"]),  # classification word
        ExtractedField(key="not.a.key", value="x", line_ids=["p1_l1"]),
        ExtractedField(key="header.court", value="مكرر", line_ids=["p1_l1"]),  # duplicate key
    ], LINES)
    by = {f["key"]: f for f in out}
    assert set(by) == {"header.court", "header.deed_number", "sale.price", "basic.deed_type"}
    assert by["header.court"]["confidence"] == 0.88 and by["header.court"]["quoted"]
    assert by["header.deed_number"]["confidence"] == 0.7  # weakest cited line
    assert by["sale.price"]["confidence"] <= 0.3 and not by["sale.price"]["quoted"]
    assert by["basic.deed_type"]["confidence"] == 0.88  # allowed as a standard word


def test_edits_skips_confirm_and_undo(new_contract):
    cid, _, _ = new_contract("clean_service_agreement.pdf")
    extracted = validate([ExtractedField(key="header.deed_number", value="٢/٧/١١", line_ids=["p1_l2"]),
                          ExtractedField(key="header.court", value="المحكمة الكبرى بالمدينة المنورة", line_ids=["p1_l1"])],
                         LINES)
    f = {x["key"]: x for x in current_fields(cid, extracted)}
    assert f["header.deed_number"]["low"] and not f["header.court"]["low"]

    db.set_deed_edit(cid, "header.deed_number", "٢/٧/١١٠", "edited")
    db.set_deed_edit(cid, "header.court", None, "skipped")
    db.set_deed_edit(cid, "closing.judge_name", "حسن بن عبد العزيز", "edited")  # added by the reviewer
    f = {x["key"]: x for x in current_fields(cid, extracted)}
    assert f["header.deed_number"]["value"] == "٢/٧/١١٠" and not f["header.deed_number"]["low"]
    assert f["header.court"]["status"] == "skipped"
    assert f["closing.judge_name"]["value"] == "حسن بن عبد العزيز"

    db.set_deed_edit(cid, "header.deed_number", None, "auto")  # undo
    f = {x["key"]: x for x in current_fields(cid, extracted)}
    assert f["header.deed_number"]["value"] == "٢/٧/١١" and f["header.deed_number"]["low"]
    db.set_deed_edit(cid, "header.deed_number", None, "confirmed")
    f = {x["key"]: x for x in current_fields(cid, extracted)}
    assert not f["header.deed_number"]["low"] and f["header.deed_number"]["value"] == "٢/٧/١١"


def test_render_fills_template_marks_low_and_skipped():
    fields = [
        {"key": "header.court", "value": "المحكمة الكبرى", "status": "auto", "low": False},
        {"key": "header.deed_number", "value": "٢/٧/١١", "status": "auto", "low": True},
        {"key": "basic.city", "value": "المدينة المنورة", "status": "skipped", "low": False},
        {"key": "waqf.conditions.0", "value": "ما يبدأ به من الغلة تعمير الوقف", "status": "auto", "low": False},
        {"key": "parties.0.name", "value": "حسن بن عثمان", "status": "edited", "low": False},
    ]
    page = render_html("abc", fields, "deed.jpeg")
    assert "المحكمة الكبرى" in page and "حسن بن عثمان" in page
    assert '<mark title="يحتاج تحقق">٢/٧/١١</mark>' in page
    assert "المدينة المنورة" not in page  # skipped fields are left out
    assert "البيانات الأساسية" not in page  # a section with nothing filled is removed
    assert "الحدود والأطوال" not in page and "الخاتمة والتوثيق" not in page
    assert "الجنسية" not in page  # empty table columns are removed
    assert "التاريخ الهجري" not in page and "رقم الصك (التسجيل)" in page  # header table: filled columns only
    assert "—" not in page  # no placeholder dashes
    assert "صك وقف" in page and "أولاً" in page  # the special section appears only when filled
    assert "صك رهن" not in page
    assert "نسخة رقمية" in page and "ليست مستنداً رسمياً" in page
    assert "#0B5A3C" in page  # the template's colours


def test_keys_follow_template_sections():
    sections = {s for s, _ in deed.KEYS.values()}
    assert sections == {s for s, _ in deed.SECTIONS}
    assert "bounds.north.length" in deed.KEYS and "parties.5.share" in deed.KEYS and "waqf.conditions.11" in deed.KEYS


def _deed_state(new_contract, monkeypatch):
    original = mock_llm.intake

    def court_record(*a, **k):
        return {**original(*a, **k), "doc_type": "court_record"}

    monkeypatch.setattr(mock_llm, "intake", court_record)
    cid, path, name = new_contract("old_scanned_lease.jpg")
    return cid, graph.run_pipeline(cid, path, name)


def test_deed_goes_to_template_not_contract_rules(new_contract, monkeypatch):
    cid, state = _deed_state(new_contract, monkeypatch)
    assert "deed_fields" in state and "policy_results" not in state
    assert state["decision"] == "escalate"
    assert any(p["agent"] == "deed" and p["status"] == "done" for _, p in db.events_after(cid, 0))


def test_deed_api(new_contract, monkeypatch):
    from backend.app.main import app

    cid, _ = _deed_state(new_contract, monkeypatch)
    client = TestClient(app)
    data = client.get(f"/api/contracts/{cid}/deed").json()
    assert data["extracted"] and data["keys"] and data["sections"]
    assert client.put(f"/api/contracts/{cid}/deed", json={"key": "header.title", "value": "صك وقف", "status": "edited"}).status_code == 200
    assert client.put(f"/api/contracts/{cid}/deed", json={"key": "nope", "status": "skipped"}).status_code == 400
    page = client.get(f"/api/contracts/{cid}/deed.html")
    assert page.status_code == 200 and "صك وقف" in page.text
    assert "attachment" in client.get(f"/api/contracts/{cid}/deed.html?download=true").headers["content-disposition"]
