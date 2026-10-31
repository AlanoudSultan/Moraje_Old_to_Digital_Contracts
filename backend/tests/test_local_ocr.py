"""Local Arabic OCR path: layout analysis, line grouping guards, confidence cap. The VLM itself is faked."""
from backend.app import config, graph
from backend.app.state import StructuringOutput
from backend.app.tools import ocr_structure
from backend.app.tools.arabic_ocr import OcrText, _degenerate, token_confidence
from backend.app.tools.ocr_structure import _build_page, _rule_grouping, plausible
from backend.app.tools.page_layout import analyze

LEASE = str(config.SAMPLES_DIR / "old_scanned_lease.jpg")


def test_layout_finds_lines_and_stamp_on_degraded_scan():
    layout = analyze(LEASE)
    assert 12 <= len(layout.lines) <= 20  # 16 printed lines incl. title and signature row
    assert abs(layout.skew_degrees) > 0.5  # the sample is rotated 1.6 degrees
    assert any(s.overlaps_text for s in layout.stamps)  # blue stamp over clause "خامساً"
    tops = [l.bbox[1] for l in layout.lines]
    assert tops == sorted(tops)


def test_foreign_script_is_removed_and_flagged():
    from types import SimpleNamespace
    from backend.app.tools.arabic_ocr import _Engine

    eng = _Engine.__new__(_Engine)
    eng.processor = SimpleNamespace(tokenizer=SimpleNamespace(decode=lambda ids, skip_special_tokens: "المنشودة وانما 广大家长 تلبية"))
    out = eng._finish([1, 2, 3], [0.99, 0.99, 0.99])
    assert out.text == "المنشودة وانما تلبية"
    assert out.confidence <= 0.3


def test_confidence_and_degenerate_output():
    assert token_confidence([0.99] * 10) > 0.95
    assert token_confidence([0.99] * 9 + [0.2]) < 0.75  # one doubtful character shows
    assert _degenerate("و و و و و و و و و و")
    assert not _degenerate("يلتزم الطرف الثاني بسداد الأجرة")
    assert not plausible("٣٠ رجب", 1500, 40)  # two words for a full-width line
    assert plausible("يلتزم الطرف الثاني بسداد الأجرة السنوية على قسطين متساويين", 900, 40)


LINES = [
    {"id": "p1_l1", "text": "عقد إيجار محل تجاري", "confidence": 0.8, "bbox": [0.35, 0.05, 0.65, 0.08]},
    {"id": "p1_l2", "text": "إنه في يوم الأحد ٠٥/٠٩/١٤٢٨هـ تم الاتفاق بين", "confidence": 0.7, "bbox": [0.1, 0.12, 0.9, 0.15]},
    {"id": "p1_l3", "text": "المؤجر عبدالله بن سالم والمستأجر مؤسسة الريان", "confidence": 0.6, "bbox": [0.1, 0.155, 0.9, 0.185]},
    {"id": "p1_l4", "text": "أولاً: أجر الطرف الأول المحل التجاري", "confidence": 0.75, "bbox": [0.1, 0.25, 0.9, 0.28]},
    {"id": "p1_l5", "text": "ثانياً: مدة الإيجار سنة هجرية", "confidence": 0.5, "bbox": [0.1, 0.33, 0.9, 0.36]},
]


def test_rule_grouping_splits_on_clause_numbers_and_gaps():
    page = _build_page(LINES, [], 1, _rule_grouping(LINES))
    assert page.title == "عقد إيجار محل تجاري"
    kinds = [(b.kind, b.number_raw) for b in page.blocks]
    assert kinds == [("title", None), ("paragraph", None), ("clause", "أولاً"), ("clause", "ثانياً")]
    assert page.blocks[1].text.count("\n") == 1  # two adjacent lines joined
    assert page.blocks[1].confidence == 0.6  # block takes its weakest line
    assert any(d.raw.startswith("٠٥/٠٩/١٤٢٨") for d in page.dates)


def test_grouping_cannot_introduce_text():
    """Claude's grouping may only point at OCR lines; invented values are dropped, skipped lines kept."""
    g = StructuringOutput.model_validate({
        "title_line_id": "p1_l1",
        "blocks": [{"line_ids": ["p1_l1"], "kind": "title"}, {"line_ids": ["p1_l2", "p1_l3"], "kind": "preamble"},
                   {"line_ids": ["p1_l4", "p1_l99"], "kind": "clause", "number_raw": "البند الأول"}],
        "parties": [{"role_ar": "المؤجر", "name": "عبدالله بن سالم", "id_number": "١٠٢٣٤٥٦٧٨٩", "line_id": "p1_l3"},
                    {"role_ar": "المستأجر", "name": "شركة غير موجودة", "line_id": "p1_l3"}],
        "key_fields": [{"label": "تاريخ العقد", "value": "٠٥/٠٩/١٤٢٨هـ", "line_id": "p1_l2"},
                       {"label": "المساحة", "value": "٧٠٠ م٢", "line_id": "p1_l2"}],
        "dates": [],
    })
    page = _build_page(LINES, [], 1, g)
    texts = "\n".join(b.text for b in page.blocks)
    assert all(l["text"] in texts for l in LINES)  # p1_l5 skipped by grouping, still present
    assert page.blocks[2].number_raw is None  # "البند الأول" is not in the line
    assert [p.name for p in page.parties] == ["عبدالله بن سالم"]
    assert page.parties[0].id_number is None  # fabricated ID dropped
    assert [k.value for k in page.key_fields] == ["٠٥/٠٩/١٤٢٨هـ"]


class FakeLineEngine:
    """Reads one segmented line at a time. Line 3 unreadable; the rest at the cap."""
    def lines(self, crops):
        return [OcrText("[؟]", 0.2) if i == 2 else
                OcrText("يلتزم الطرف الثاني بسداد الأجرة السنوية في موعدها المحدد", config.OCR_CONFIDENCE_CAP)
                for i, _ in enumerate(crops)]


def test_local_engine_pipeline_never_auto_approves_on_ocr(new_contract, monkeypatch):
    monkeypatch.setattr(config, "OCR_ENGINE", "historic")
    monkeypatch.setattr(ocr_structure, "get_engine", lambda name: FakeLineEngine())
    cid, path, name = new_contract("old_scanned_lease.jpg")
    state = graph.run_pipeline(cid, path, name)
    d = state["digital"]
    assert d["ocr_engine"] == "Historic-Arabic-OCR"
    assert len(d["blocks"]) >= 1 and d["stamps"]
    assert state["ocr_confidence"]["mean"] <= config.OCR_CONFIDENCE_CAP
    lines = state["ocr_pages"][0]["lines"]
    assert lines[2]["text"] == "[؟]" and lines[2]["confidence"] <= 0.3  # unread line kept, never guessed
    assert max(l["confidence"] for l in lines) <= config.OCR_CONFIDENCE_CAP
    assert abs(state["ocr_pages"][0]["skew"]) > 0.5  # the sample is rotated about 1.6 degrees
    assert state["decision"] == "escalate"
    rec = state["ocr_pages"][0]
    assert rec["boxes"] and rec["lines"] and rec["stamps"]
    from backend.app import db
    layout_events = [p for _, p in db.events_after(cid, 0) if p["data"].get("kind") == "layout"]
    assert layout_events and layout_events[0]["data"]["boxes"] == rec["boxes"]  # streamed before OCR


def test_ocr_mean_is_over_lines_not_blocks():
    from backend.app.agents.digitization import merge, ocr_confidence

    page = _build_page(LINES, [], 1, _rule_grouping(LINES))
    ocr = ocr_confidence(merge([page]))
    line_confs = [l["confidence"] for l in LINES]
    assert ocr.min == min(line_confs)
    assert abs(ocr.mean - sum(line_confs) / len(line_confs)) < 0.001
