"""Gemini OCR engine with a fake client: drawn boxes, box and confidence mapping, key and budget guards, pipeline."""
from types import SimpleNamespace

import pytest

from backend.app import config, db, graph
from backend.app.tools import gemini_ocr
from backend.app.tools.gemini_ocr import (GeminiError, GeminiLine, GeminiPage, PageLine, annotated_jpeg, parse_page,
                                          to_lines)
from backend.app.tools.ocr_structure import _place_by_box


def _tiny_png(path):
    from PIL import Image

    Image.new("RGB", (40, 60), (255, 255, 255)).save(path)


def test_sent_image_is_recorded(monkeypatch, tmp_path):
    """The engine records which file it sent, its size, and whether it was the enhanced page."""
    eng, _ = _engine_with([_ok_response()], monkeypatch)
    png = tmp_path / "page_1_enhanced.png"
    _tiny_png(png)
    eng.page(str(png))
    assert eng.last_sent["file"] == "page_1_enhanced.png" and eng.last_sent["enhanced"]
    assert eng.last_sent["size"] == [40, 60]


def test_confidence_mapping():
    page = GeminiPage(lines=[
        GeminiLine(box=1, text="عقد إيجار محل تجاري", legibility="clear"),
        GeminiLine(box=0, text="مدة الإيجار [؟] هجرية", legibility="clear"),
        GeminiLine(box=3, text="كلام 广大 مكتوب", legibility="unsure"),
        GeminiLine(box=4, text="   ", legibility="unreadable"),
    ])
    lines = to_lines(page)
    assert len(lines) == 3  # empty line dropped
    assert lines[0].confidence == min(0.88, config.OCR_CONFIDENCE_CAP)
    assert lines[1].confidence <= 0.5  # [؟] lowers confidence
    assert lines[2].text == "كلام مكتوب" and lines[2].confidence == 0.55  # foreign script stripped
    assert [l.box for l in lines] == [1, 0, 3]


def test_schema_asks_for_box_number_not_coordinates():
    schema = GeminiPage.model_json_schema()["$defs"]["GeminiLine"]
    assert set(schema["properties"]) == {"box", "text", "legibility"}  # a box number, no coordinates: fast answers
    assert set(schema["required"]) == {"box", "text", "legibility"}


def test_boxes_are_drawn_beside_the_page(tmp_path):
    """The copy sent to Gemini has the boxes drawn on it and a numbers strip added on the right."""
    from PIL import Image

    png = tmp_path / "p.png"
    Image.new("RGB", (400, 600), (255, 255, 255)).save(png)
    data, size, im = annotated_jpeg(str(png), [[0.1, 0.1, 0.9, 0.15], [0.1, 0.2, 0.9, 0.25]])
    assert size[1] == 600 and size[0] > 400 and data[:2] == b"\xff\xd8"
    r, g, b = im.getpixel((200, 60))  # top edge of box 1: tinted blue over white
    assert b > r and b > g
    assert im.getpixel((200, 75)) == (255, 255, 255)  # inside the box (y 60 to 90) the page is untouched


def test_drawn_boxes_go_with_the_request(monkeypatch, tmp_path):
    seen = []

    def generate_content(**k):
        seen.append(k)
        return _ok_response()

    eng = gemini_ocr.GeminiEngine.__new__(gemini_ocr.GeminiEngine)
    eng.client = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    png = tmp_path / "page_1_enhanced.png"
    _tiny_png(png)
    eng.page(str(png), boxes=[[0.1, 0.1, 0.9, 0.3]])
    assert "numbered" in seen[0]["config"].system_instruction
    assert eng.last_sent["boxes"] == 1 and eng.last_sent["annotated_file"] == "page_1_enhanced_sent.jpg"
    assert (tmp_path / "page_1_enhanced_sent.jpg").exists()
    assert eng.last_sent["size"][0] > 40  # page plus the numbers strip


def test_lines_are_placed_on_their_boxes():
    seg = [[0.1, 0.1, 0.9, 0.2], [0.1, 0.3, 0.9, 0.4], [0.1, 0.6, 0.9, 0.7]]
    out = _place_by_box([1, 2, 2, 0, 3], seg)
    assert out[0] == seg[0]
    assert out[1] == [0.1, 0.3, 0.9, 0.35] and out[2] == [0.1, 0.35, 0.9, 0.4]  # two lines share box 2
    assert 0.4 <= out[3][1] < out[3][3] <= 0.6  # a missed line fills the gap between boxes 2 and 3
    assert out[4] == seg[2]
    assert _place_by_box([0, 0], seg) == [seg[0], seg[1]]  # nothing tagged: order-based fallback
    assert _place_by_box([9], seg)[0] == seg[0]  # a number past the last box counts as untagged


def test_missing_key_is_a_clear_error(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    with pytest.raises(GeminiError, match="GEMINI_API_KEY"):
        gemini_ocr.GeminiEngine()


def test_budget_blocks_before_calling(monkeypatch, tmp_path):
    eng = gemini_ocr.GeminiEngine.__new__(gemini_ocr.GeminiEngine)
    eng.client = SimpleNamespace(models=SimpleNamespace(generate_content=lambda **k: pytest.fail("must not call")))
    monkeypatch.setattr(config, "GEMINI_BUDGET_USD", 0.0)
    png = tmp_path / "p.png"
    _tiny_png(png)
    with pytest.raises(GeminiError, match="ميزانية"):
        eng.page(str(png))


def test_response_is_parsed_and_cost_logged(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "GEMINI_FREE_TIER", False)  # paid tier: real prices count
    payload = GeminiPage(lines=[GeminiLine(box=0, text="أولاً: يلتزم الطرف الثاني",
                                           legibility="clear")]).model_dump_json()
    resp = SimpleNamespace(
        text=payload,
        usage_metadata=SimpleNamespace(prompt_token_count=3000, candidates_token_count=400, thoughts_token_count=600,
                                       cached_content_token_count=0),
        candidates=[SimpleNamespace(finish_reason=SimpleNamespace(value="STOP"))],
    )
    calls = []
    eng = gemini_ocr.GeminiEngine.__new__(gemini_ocr.GeminiEngine)
    eng.client = SimpleNamespace(models=SimpleNamespace(generate_content=lambda **k: (calls.append(k), resp)[1]))
    png = tmp_path / "p.png"
    _tiny_png(png)
    before = db.total_spend(prefix="gemini")
    lines = eng.page(str(png), contract_id="t-gemini")
    assert [l.text for l in lines] == ["أولاً: يلتزم الطرف الثاني"]
    assert calls[0]["model"] == config.GEMINI_MODEL
    price_in, price_out = config.price(config.GEMINI_MODEL)
    assert db.total_spend(prefix="gemini") - before == pytest.approx((3000 * price_in + 1000 * price_out) / 1e6)
    assert db.total_spend(exclude="gemini") >= 0  # Claude budget does not include Gemini spend


def test_pipeline_with_gemini(new_contract, monkeypatch):
    class FakeGemini:
        def __init__(self):
            self.boxes = None

        def page(self, png, contract_id=None, boxes=None, polygons=None):
            self.boxes = boxes
            return [PageLine(text=f"البند {i}: نص مقروء بوضوح من الصفحة", confidence=0.88, box=i) for i in range(1, 6)]

    fake = FakeGemini()

    monkeypatch.setattr(config, "OCR_ENGINE", "gemini")
    monkeypatch.setattr(gemini_ocr, "get_engine", lambda: fake)
    cid, path, name = new_contract("old_scanned_lease.jpg")
    state = graph.run_pipeline(cid, path, name)
    rec = state["ocr_pages"][0]
    assert state["digital"]["ocr_engine"] == config.GEMINI_LABELS[config.GEMINI_MODEL]
    assert len(rec["lines"]) == 5 and all(l["source"] == "gemini" for l in rec["lines"])
    assert fake.boxes == rec["boxes"] and len(rec["boxes"]) >= 5  # the segmentation is sent with the page
    assert [l["bbox"] for l in rec["lines"]] == rec["boxes"][:5]  # each line sits on the box Gemini named
    assert [l["box"] for l in rec["lines"]] == [1, 2, 3, 4, 5]
    assert rec["stamps"]  # stamps still found by image analysis on the original
    assert state["decision"] == "escalate"  # capped confidence never auto-approves


def test_gemini_error_message_reaches_the_tracker(new_contract, monkeypatch):
    def broken():
        raise GeminiError("لم يُضبط مفتاح GEMINI_API_KEY في ملف .env، فتعذرت قراءة الصفحة بنموذج Gemini")

    monkeypatch.setattr(config, "OCR_ENGINE", "gemini")
    monkeypatch.setattr(gemini_ocr, "get_engine", broken)
    cid, path, name = new_contract("old_scanned_lease.jpg")
    state = graph.run_pipeline(cid, path, name)
    assert state["decision"] == "escalate" and state["errors"]
    errs = [p for _, p in db.events_after(cid, 0) if p["status"] == "error" and p["agent"] == "digitize"]
    assert "GEMINI_API_KEY" in errs[0]["message_ar"]


def test_default_model_is_free_flash_lite():
    assert config.GEMINI_MODEL == "gemini-3.5-flash-lite" and config.GEMINI_FREE_TIER
    assert config.GEMINI_LABELS["gemini-3.5-flash-lite"] == "Gemini 3.5 Flash-Lite"
    assert config.price("gemini-3.5-flash-lite") == (0.0, 0.0)  # free tier costs nothing
    assert config.price("gemma-4-26b-a4b-it") == (0.0, 0.0)


def test_lenient_parsing_of_answers():
    """Models without a strict JSON mode wrap the answer in fences or reasoning."""
    body = '{"lines": [{"box": 2, "text": "سطر", "legibility": "clear"}]}'
    for text in (body, f"```json\n{body}\n```", f"Here is the transcription:\n{body}\nDone."):
        assert parse_page(text).lines[0].box == 2
    with pytest.raises(ValueError):
        parse_page("no json here")


def test_gemma_settings(monkeypatch, tmp_path):
    """Gemma gets no high-resolution setting and the "minimal" thinking level."""
    monkeypatch.setattr(config, "GEMINI_THINKING", "medium")
    eng, seen = _engine_with([_ok_response()], monkeypatch, model="gemma-4-26b-a4b-it")
    png = tmp_path / "p.png"
    _tiny_png(png)
    assert eng.page(str(png))
    assert seen[0] is None and str(eng.thinking_seen[0].thinking_level).lower().endswith("minimal")


def test_rejected_json_format_falls_back(monkeypatch, tmp_path):
    from google.genai import errors

    bad = errors.ClientError(400, {"error": {"code": 400, "message": "JSON mode is not enabled for this model"}})
    eng, _ = _engine_with([bad, _ok_response()], monkeypatch)
    png = tmp_path / "p.png"
    _tiny_png(png)
    assert eng.page(str(png))
    assert eng.mime_seen[0] == "application/json" and eng.mime_seen[1] is None  # retried with instructions only


def test_paid_flash_prices(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_FREE_TIER", False)
    assert config.price("gemini-3.6-flash") == config.price("gemini-3.8-flash") != (0.0, 0.0)
    assert config.price("gemini-3.5-flash-lite") == (0.30, 2.50)
    assert config.price("gemini-2.5-pro") == (1.25, 10.00)
    assert config.price("gemini-2.5-flash-lite") == (0.10, 0.40)


def _engine_with(responses, monkeypatch, model="gemini-3.8-flash"):
    """Engine whose client returns or raises the given items in order; sleeps are skipped."""
    monkeypatch.setattr(config, "GEMINI_MODEL", model)
    monkeypatch.setattr(gemini_ocr, "RETRY_WAITS", (0, 0, 0))
    monkeypatch.setattr(gemini_ocr.time, "sleep", lambda s: None)
    seen = []

    def generate_content(**k):
        seen.append(k["config"].media_resolution)
        thinking.append(k["config"].thinking_config)
        mime.append(k["config"].response_mime_type)
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    thinking, mime = [], []
    eng = gemini_ocr.GeminiEngine.__new__(gemini_ocr.GeminiEngine)
    eng.client = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    eng.thinking_seen, eng.mime_seen = thinking, mime
    return eng, seen


def _ok_response():
    payload = GeminiPage(lines=[GeminiLine(box=0, text="سطر مقروء", legibility="clear")])
    return SimpleNamespace(text=payload.model_dump_json(), usage_metadata=None,
                           candidates=[SimpleNamespace(finish_reason=SimpleNamespace(value="STOP"))])


def test_rate_limit_is_retried_then_succeeds(monkeypatch, tmp_path):
    from google.genai import errors

    rate = errors.ClientError(429, {"error": {"code": 429, "message": "Resource exhausted"}})
    eng, seen = _engine_with([rate, rate, _ok_response()], monkeypatch)
    png = tmp_path / "p.png"
    _tiny_png(png)
    assert [l.text for l in eng.page(str(png))] == ["سطر مقروء"]
    assert len(seen) == 3


def test_rate_limit_gives_up_with_clear_message(monkeypatch, tmp_path):
    from google.genai import errors

    rate = errors.ClientError(429, {"error": {"code": 429, "message": "Resource exhausted"}})
    eng, _ = _engine_with([rate, rate, rate, rate], monkeypatch)
    png = tmp_path / "p.png"
    _tiny_png(png)
    with pytest.raises(GeminiError, match="حد الاستخدام"):
        eng.page(str(png))


def test_rejected_resolution_falls_back(monkeypatch, tmp_path):
    from google.genai import errors

    bad = errors.ClientError(400, {"error": {"code": 400, "message": "media_resolution is not supported"}})
    eng, seen = _engine_with([bad, _ok_response()], monkeypatch)
    png = tmp_path / "p.png"
    _tiny_png(png)
    assert eng.page(str(png))
    assert seen[0] is not None and seen[1] is None  # second try without the high-resolution setting



def test_rejected_thinking_level_falls_back(monkeypatch, tmp_path):
    from google.genai import errors

    bad = errors.ClientError(400, {"error": {"code": 400, "message": "thinking_level medium is not supported"}})
    eng, _ = _engine_with([bad, _ok_response()], monkeypatch)
    png = tmp_path / "p.png"
    _tiny_png(png)
    assert eng.page(str(png))
    assert eng.thinking_seen[0] is not None and eng.thinking_seen[1] is None  # retried without it


def test_unavailable_model_names_it(monkeypatch, tmp_path):
    from google.genai import errors

    gone = errors.ClientError(404, {"error": {"code": 404, "message": "This model is no longer available to new users."}})
    eng, _ = _engine_with([gone], monkeypatch, model="gemini-2.5-flash-lite")
    png = tmp_path / "p.png"
    _tiny_png(png)
    with pytest.raises(GeminiError, match="gemini-2.5-flash-lite غير متاح"):
        eng.page(str(png))


def test_service_unavailable_is_retried(monkeypatch, tmp_path):
    from google.genai import errors

    busy = errors.ServerError(503, {"error": {"code": 503, "message": "The model is overloaded"}})
    eng, seen = _engine_with([busy, _ok_response()], monkeypatch)
    png = tmp_path / "p.png"
    _tiny_png(png)
    assert eng.page(str(png)) and len(seen) == 2
