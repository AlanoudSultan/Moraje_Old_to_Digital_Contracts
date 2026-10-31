"""Local OCR page -> PageDigitization.

Text always comes from the OCR model. Claude (text only, when available) only groups line IDs into
blocks and points at key fields; every string it returns is checked to be an exact substring of an
OCR line, so it cannot introduce words. Without Claude, a rule-based grouping is used.
"""
from __future__ import annotations

import json
import logging
import re
from statistics import median

from .. import config
from ..llm import call_structured, text_block
from ..state import Block, DateRef, KeyField, PageDigitization, Party, Stamp, StructuringOutput
from .arabic_ocr import LINE_ENGINES, OcrText, get_engine
from .arabic_text import find_dates, parse_clause_number
from .page_layout import analyze

log = logging.getLogger("ocr")

# Rough characters per line-height of width for Arabic script; used to catch hallucinated short
# outputs on wide crops ("٣٠ رجب" for a full line) and runaway long outputs on short crops.
CHARS_PER_HEIGHT = 2.2


def plausible(text: str, crop_w: int, crop_h: int) -> bool:
    if text == "[؟]" or crop_h <= 0:
        return True
    expected = crop_w / crop_h * CHARS_PER_HEIGHT
    n = len(text.replace(" ", ""))
    return 0.25 * expected <= n <= 3.0 * expected + 10


def read_page(page_png: str, page_no: int, engine_name: str, on_layout=None, original_png: str | None = None,
              contract_id: str | None = None) -> dict:
    """Segment then OCR one page. Returns {page, skew, boxes, stamps, lines}, where `boxes` are all
    detected line boxes and `lines` the OCR result [{id, text, confidence, bbox}].
    `on_layout(record)` is called after segmentation, before the (slow) OCR.
    `page_png` is what the OCR reads (possibly enhanced); stamps are found on `original_png`, because
    enhancement can wash out faint stamp ink."""
    layout = analyze(page_png)
    stamp_layout = analyze(original_png) if original_png and original_png != page_png else layout
    stamps = [{"bbox": list(s.bbox), "overlaps_text": s.overlaps_text} for s in stamp_layout.stamps]
    if engine_name == "gemini":
        from . import kraken_seg

        klines = kraken_seg.segment(page_png)  # None when Kraken is off or failed: classical boxes instead
        return _read_page_gemini(page_png, page_no, stamps, on_layout, layout, contract_id, klines)
    record = {"page": page_no, "skew": round(layout.skew_degrees, 2), "boxes": [list(lb.bbox) for lb in layout.lines],
              "stamps": stamps, "lines": []}
    if on_layout:
        on_layout(record)
    engine = get_engine(engine_name)
    if engine_name in LINE_ENGINES:
        texts = engine.lines([lb.crop for lb in layout.lines]) if layout.lines else []
        boxes = [list(lb.bbox) for lb in layout.lines]
        for i, (t, lb) in enumerate(zip(texts, layout.lines)):
            if not plausible(t.text, *lb.crop.size):
                texts[i] = OcrText(text=t.text, confidence=min(t.confidence, 0.3))
    else:
        texts = engine.page_lines(layout.enhanced)
        boxes = _align_boxes(len(texts), [list(lb.bbox) for lb in layout.lines])
    record["lines"] = [{"id": f"p{page_no}_l{i + 1}", "text": t.text, "confidence": t.confidence, "bbox": b,
                        "source": engine_name}
                       for i, (t, b) in enumerate(zip(texts, boxes)) if t.text.strip()]
    return record


def _read_page_gemini(page_png: str, page_no: int, stamps: list[dict], on_layout, layout,
                      contract_id: str | None, klines=None) -> dict:
    """Gemini reads the text; line shapes come from segmentation: Kraken's polygons (they follow
    tilted and curved lines) when available, else the classical boxes. They are shown before the
    (slow) Gemini call, drawn numbered on the copy Gemini reads, and each returned line is placed on
    the line Gemini says it came from."""
    from .gemini_ocr import get_engine as get_gemini

    if klines:
        seg, polys, segmenter = [k.bbox for k in klines], [k.polygon for k in klines], "kraken"
    else:
        seg, polys, segmenter = [list(lb.bbox) for lb in layout.lines], None, "classic"
    record = {"page": page_no, "skew": round(layout.skew_degrees, 2), "boxes": seg, "stamps": stamps, "lines": [],
              "segmenter": segmenter}
    if segmenter == "classic" and config.SEGMENTER == "kraken":
        record["segmenter_fallback"] = True  # Kraken was meant to run but is missing or failed: see the server log
    if polys:
        record["polygons"] = polys
    if on_layout:
        on_layout(record)
    engine = get_gemini()
    lines = engine.page(page_png, contract_id, boxes=seg or None, polygons=polys)
    record["sent_to_model"] = getattr(engine, "last_sent", None)
    numbers = [l.box if 1 <= l.box <= len(seg) else 0 for l in lines]
    boxes = _place_by_box(numbers, seg)
    out = []
    for i, (l, b) in enumerate(zip(lines, boxes)):
        rec = {"id": f"p{page_no}_l{i + 1}", "text": l.text, "confidence": l.confidence, "bbox": b,
               "box": l.box or None, "source": "gemini"}
        if polys and numbers[i] and numbers.count(numbers[i]) == 1:  # the line owns its polygon
            rec["polygon"] = polys[numbers[i] - 1]
        out.append(rec)
    record["lines"] = out
    return record


def _place_by_box(numbers: list[int], seg: list[list[float]]) -> list[list[float]]:
    """Bboxes for lines the model tagged with a segmentation box number (1-based; 0 = in no box).
    Lines sharing a box split it top to bottom; untagged lines fill the gap between their neighbours.
    Falls back to order-based mapping when the model tagged nothing."""
    n = len(numbers)
    valid = [k if 1 <= k <= len(seg) else 0 for k in numbers]
    if not seg or not any(valid):
        return _align_boxes(n, seg)
    out: list[list[float] | None] = [None] * n
    for k in set(v for v in valid if v):
        idx = [i for i, v in enumerate(valid) if v == k]
        x0, y0, x1, y1 = seg[k - 1]
        step = (y1 - y0) / len(idx)
        for j, i in enumerate(idx):
            out[i] = [x0, round(y0 + j * step, 4), x1, round(y0 + (j + 1) * step, 4)]
    left, right = min(b[0] for b in seg), max(b[2] for b in seg)
    i = 0
    while i < n:
        if out[i] is not None:
            i += 1
            continue
        j = i
        while j < n and out[j] is None:
            j += 1
        top = out[i - 1][3] if i > 0 else 0.02
        bottom = out[j][1] if j < n else 0.98
        if bottom - top < 0.01 * (j - i):  # no room between the neighbours: a thin band just below
            bottom = min(0.99, top + 0.012 * (j - i))
        step = (bottom - top) / (j - i)
        for k in range(i, j):
            out[k] = [left, round(top + (k - i) * step, 4), right, round(top + (k - i + 1) * step, 4)]
        i = j
    return out  # type: ignore[return-value]


def _align_boxes(n: int, boxes: list[list[float]]) -> list[list[float]]:
    """Page-level engines return lines without positions; map them onto detected line boxes in order."""
    if n == 0:
        return []
    if not boxes:
        return [[0.05, 0.05 + 0.9 * i / n, 0.95, 0.05 + 0.9 * (i + 1) / n] for i in range(n)]
    if len(boxes) == n:
        return boxes
    return [boxes[min(len(boxes) - 1, int(i * len(boxes) / n))] for i in range(n)]


# ---------- grouping ----------

def structure(lines: list[dict], stamps: list[dict], page_no: int, contract_id: str | None) -> PageDigitization:
    if not lines:
        return PageDigitization(
            title=None, parties=[], key_fields=[], tables=[], dates=[], page_header_footer=[],
            stamps=[_stamp(s) for s in stamps],
            blocks=[Block(id=f"p{page_no}_b1", kind="other", text="[؟]", confidence=0.2, bbox=[0.05, 0.05, 0.95, 0.95])],
        )
    grouping = None
    if not config.mock_mode():
        try:
            grouping = _claude_grouping(lines, page_no, contract_id)
        except Exception as exc:  # grouping is a convenience; fall back to rules
            log.warning("structuring call failed, using rules: %s", exc)
    if grouping is None:
        grouping = _rule_grouping(lines)
    return _build_page(lines, stamps, page_no, grouping)


def _claude_grouping(lines: list[dict], page_no: int, contract_id: str | None) -> StructuringOutput:
    payload = [{"id": l["id"], "text": l["text"], "confidence": l["confidence"]} for l in lines]
    content = [text_block(json.dumps({"page": page_no, "lines": payload}, ensure_ascii=False, indent=1))]
    return call_structured("structuring", "structuring", content, StructuringOutput, contract_id=contract_id)


def _rule_grouping(lines: list[dict]) -> StructuringOutput:
    heights = [l["bbox"][3] - l["bbox"][1] for l in lines]
    mh = median(heights) if heights else 0.02
    blocks: list[dict] = []
    title_id = None
    first = lines[0]
    cx = (first["bbox"][0] + first["bbox"][2]) / 2
    if len(first["text"].split()) <= 6 and abs(cx - 0.5) < 0.15 and first["confidence"] >= 0.6:
        title_id = first["id"]
    prev = None
    for l in lines:
        number, _ = parse_clause_number(l["text"])
        gap = l["bbox"][1] - prev["bbox"][3] if prev else 0
        if l["id"] == title_id:
            blocks.append({"line_ids": [l["id"]], "kind": "title", "number_raw": None, "heading": None})
        elif number or not blocks or gap > 0.9 * mh or blocks[-1]["kind"] == "title":
            heading = None
            if number:
                rest = l["text"][l["text"].find(number) + len(number):].lstrip(" :：-")
                head = rest.split(":")[0].strip() if ":" in rest[:60] else None
                heading = head if head and len(head.split()) <= 6 else None
            blocks.append({"line_ids": [l["id"]], "kind": "clause" if number else "paragraph",
                           "number_raw": number, "heading": heading})
        else:
            blocks[-1]["line_ids"].append(l["id"])
        prev = l
    dates = [{"raw": raw, "calendar": cal, "line_id": l["id"]} for l in lines for raw, cal in find_dates(l["text"])]
    key_fields = []
    for l in lines:
        m = re.search(r"(?:صك|الصك)\s*(?:رقم|بالرقم)?\s*([0-9٠-٩/]{3,})", l["text"])
        if m:
            key_fields.append({"section": "البيانات الأساسية", "label": "رقم الصك", "value": m.group(1), "line_id": l["id"]})
    return StructuringOutput.model_validate({"title_line_id": title_id, "blocks": blocks, "parties": [],
                                             "key_fields": key_fields, "dates": dates})


def _stamp(s: dict) -> Stamp:
    return Stamp(bbox=s["bbox"], kind="stamp", text_if_any=None, overlaps_text=s["overlaps_text"])


def _build_page(lines: list[dict], stamps: list[dict], page_no: int, g: StructuringOutput) -> PageDigitization:
    """Assemble blocks from line IDs. Every OCR line ends up in exactly one block, in reading order."""
    by_id = {l["id"]: l for l in lines}
    order = {l["id"]: i for i, l in enumerate(lines)}
    used: set[str] = set()
    raw_blocks = []
    for b in g.blocks:
        ids = [i for i in b.line_ids if i in by_id and i not in used]
        if not ids:
            continue
        used.update(ids)
        raw_blocks.append((min(order[i] for i in ids), sorted(ids, key=order.get), b))
    for l in lines:  # lines the grouping skipped become their own paragraphs
        if l["id"] not in used:
            raw_blocks.append((order[l["id"]], [l["id"]], None))
    raw_blocks.sort(key=lambda x: x[0])

    blocks = []
    for n, (_, ids, b) in enumerate(raw_blocks, start=1):
        ls = [by_id[i] for i in ids]
        text = "\n".join(l["text"] for l in ls)
        first = ls[0]["text"]
        number = b.number_raw if b and b.number_raw and b.number_raw in first else None
        heading = b.heading if b and b.heading and b.heading in text else None
        blocks.append(Block(
            id=f"p{page_no}_b{n}", kind=b.kind if b else "paragraph", number_raw=number, heading=heading,
            text=text, confidence=round(min(l["confidence"] for l in ls), 3),
            line_confidences=[l["confidence"] for l in ls],
            bbox=[min(l["bbox"][0] for l in ls), min(l["bbox"][1] for l in ls),
                  max(l["bbox"][2] for l in ls), max(l["bbox"][3] for l in ls)],
        ))
    block_of_line = {i: blk.id for (_, ids, _), blk in zip(raw_blocks, blocks) for i in ids}

    def in_line(value: str | None, line_id: str) -> bool:
        return bool(value) and line_id in by_id and value in by_id[line_id]["text"]

    title = by_id[g.title_line_id]["text"] if g.title_line_id in by_id else None
    parties = [Party(role_ar=p.role_ar, name=p.name, id_number=p.id_number if in_line(p.id_number, p.line_id) else None)
               for p in g.parties if in_line(p.name, p.line_id)]
    key_fields = [KeyField(section=k.section, label=k.label, value=k.value, block_id=block_of_line.get(k.line_id),
                           confidence=by_id[k.line_id]["confidence"])
                  for k in g.key_fields if in_line(k.value, k.line_id)]
    dates = [DateRef(raw=d.raw, calendar=d.calendar, block_id=block_of_line.get(d.line_id))
             for d in g.dates if in_line(d.raw, d.line_id)]
    return PageDigitization(title=title, parties=parties, key_fields=key_fields, blocks=blocks, tables=[],
                            dates=dates, stamps=[_stamp(s) for s in stamps], page_header_footer=[])
