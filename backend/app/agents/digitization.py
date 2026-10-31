"""Agent 2: Digitization. Reads every page, then a deterministic merge.

Who reads the pages is config.OCR_ENGINE:
- gemini: Google Gemini reads each page (cloud); historic / qari: a local Arabic OCR model (tools/arabic_ocr.py) reads the text; Claude, when
  available, only groups the OCR lines (tools/ocr_structure.py).
- claude: one Claude vision call per page (concurrency 4).
- mock: bundled ground truth (tests, offline demo of the samples).
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from statistics import mean

import pymupdf

from .. import config, mock_llm
from ..events import emit
from ..llm import call_structured, image_block, text_block
from ..state import (Clause, ClauseRegion, ContractState, DigitalContract, KeyField, OcrConfidence, PageBlock,
                     PageDigitization, PageStamp, PageTable, ParsedDate, Party)
from ..tools.arabic_text import normalize, parse_clause_number, parse_date

CLAUSE_START_KINDS = {"clause"}
APPEND_KINDS = {"subclause", "paragraph", "table"}
SKIP_KINDS = {"title", "signature_area", "other"}


def _clamp_bbox(b: list[float]) -> list[float]:
    if len(b) != 4:
        return [0.0, 0.0, 1.0, 1.0]
    x0, y0, x1, y1 = (min(1.0, max(0.0, float(v))) for v in b)
    return [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]


def _pdf_text_geometry(file_path: str, page_no: int) -> list[tuple]:
    """Text-layer blocks with normalized bboxes (mock mode only)."""
    if not file_path.lower().endswith(".pdf"):
        return []
    from ..tools.arabic_text import clean_text_layer

    with pymupdf.open(file_path) as doc:
        page = doc[page_no - 1]
        w, h = page.rect.width, page.rect.height
        return [((x0 / w, y0 / h, x1 / w, y1 / h), clean_text_layer(t))
                for x0, y0, x1, y1, t, *_ in page.get_text("blocks")]


def digitize_page(state: ContractState, page_no: int, sha: str) -> PageDigitization:
    image = state["page_images"][page_no - 1]
    texts = state.get("text_layer", [])
    layer = texts[page_no - 1] if page_no <= len(texts) else ""
    instructions = (
        f"رقم الصفحة: {page_no} من {len(state['page_images'])}.\n"
        f"استخدم المعرفات بالصيغة p{page_no}_b1، p{page_no}_b2 ... بالترتيب.\n"
    )
    if layer.strip():
        instructions += "الطبقة النصية المستخرجة من ملف PDF (للتأكيد فقط، والصورة هي المرجع):\n" + layer[:6000]
    content = [image_block(image), text_block(instructions)]
    return call_structured(
        "digitization", "digitization", content, PageDigitization, contract_id=state["contract_id"],
        mock=lambda: mock_llm.digitize_page(sha, page_no, layer, _pdf_text_geometry(state["file_path"], page_no)),
    )


def _near_block(stamp_bbox: list[float], blocks: list[PageBlock]) -> str | None:
    if not blocks:
        return None
    cy = (stamp_bbox[1] + stamp_bbox[3]) / 2

    def dist(b: PageBlock) -> float:
        if b.bbox[1] <= cy <= b.bbox[3]:
            return 0.0
        return min(abs(cy - b.bbox[1]), abs(cy - b.bbox[3]))

    return min(blocks, key=dist).id


def merge(pages: list[PageDigitization]) -> DigitalContract:
    headers = {normalize(h) for p in pages for h in p.page_header_footer if h.strip()}
    blocks: list[PageBlock] = []
    stamps: list[PageStamp] = []
    tables: list[PageTable] = []
    dates: list[ParsedDate] = []
    parties: list[Party] = []
    key_fields: list[KeyField] = []
    title = None

    for page_no, p in enumerate(pages, start=1):
        title = title or p.title
        for party in p.parties:
            if all(normalize(party.name) != normalize(x.name) for x in parties):
                parties.append(party)
        page_blocks = []
        for i, b in enumerate(p.blocks, start=1):
            if normalize(b.text) in headers:
                continue
            bid = b.id if b.id.startswith(f"p{page_no}_") else f"p{page_no}_b{i}"
            pb = PageBlock(**{**b.model_dump(), "id": bid, "bbox": _clamp_bbox(b.bbox)}, page=page_no,
                           normalized=normalize(b.text))
            page_blocks.append(pb)
        blocks.extend(page_blocks)
        for kf in p.key_fields:
            if all((normalize(kf.label), normalize(kf.value)) != (normalize(x.label), normalize(x.value))
                   for x in key_fields):
                key_fields.append(kf)
        for s in p.stamps:
            bbox = _clamp_bbox(s.bbox)
            stamps.append(PageStamp(**{**s.model_dump(), "bbox": bbox}, id=f"s{len(stamps) + 1}", page=page_no,
                                    near_block_id=_near_block(bbox, page_blocks)))
        for t in p.tables:
            tables.append(PageTable(**{**t.model_dump(), "bbox": _clamp_bbox(t.bbox)}, page=page_no))
        for d in p.dates:
            calendar, iso = parse_date(d.raw, d.calendar)
            dates.append(ParsedDate(raw=d.raw, calendar=calendar if d.calendar == "unknown" else d.calendar,
                                    block_id=d.block_id, iso=iso))

    clauses = _build_clauses(blocks)
    return DigitalContract(title=title, parties=parties, key_fields=key_fields, clauses=clauses, blocks=blocks, tables=tables,
                           dates=dates, stamps=stamps, headers_footers=sorted(headers))


def _build_clauses(blocks: list[PageBlock]) -> list[Clause]:
    clauses: list[dict] = []
    prev_kind = None
    for b in blocks:
        kind = b.kind
        number = b.number_raw
        if kind in APPEND_KINDS and not number and b.kind == "paragraph":
            detected, _ = parse_clause_number(b.text)
            if detected:
                kind, number = "clause", detected
        continues = b.continues_from_previous_page and clauses
        if kind in SKIP_KINDS:
            prev_kind = kind
            continue
        if continues or (kind in APPEND_KINDS and clauses and prev_kind not in SKIP_KINDS) or (
                kind == "preamble" and prev_kind == "preamble" and clauses):
            c = clauses[-1]
            c["text"] += "\n" + b.text
            c["block_ids"].append(b.id)
            c["confs"].append(b.confidence)
            if b.page not in c["pages"]:
                c["pages"].append(b.page)
            c["regions"].append({"page": b.page, "bbox": b.bbox})
        else:
            clauses.append({
                "number_raw": number, "heading": b.heading or ("التمهيد" if kind == "preamble" else None),
                "text": b.text, "block_ids": [b.id], "confs": [b.confidence], "pages": [b.page],
                "regions": [{"page": b.page, "bbox": b.bbox}],
            })
        prev_kind = kind
    out = []
    for i, c in enumerate(clauses, start=1):
        out.append(Clause(
            clause_id=f"c{i}", number_raw=c["number_raw"], heading=c["heading"], text=c["text"],
            normalized=normalize(c["text"]), block_ids=c["block_ids"], pages=c["pages"],
            regions=[ClauseRegion(**r) for r in c["regions"]], confidence=round(min(c["confs"]), 3),
        ))
    return out


def ocr_confidence(digital: DigitalContract) -> OcrConfidence:
    th = config.thresholds()["block_highlight_below"]
    # Per text line when the OCR reported lines; otherwise per block. A block's own confidence is its
    # weakest line, so averaging blocks would let one bad line pull the mean down to the minimum.
    confs = [c for b in digital.blocks for c in (b.line_confidences or [b.confidence])]
    if not confs:
        return OcrConfidence(min=0.0, mean=0.0, low_conf_block_ids=[])
    return OcrConfidence(min=round(min(confs), 3), mean=round(mean(confs), 3),
                         low_conf_block_ids=[b.id for b in digital.blocks if b.confidence < th])


ENGINE_LABELS = {"gemini": config.GEMINI_LABELS.get(config.GEMINI_MODEL, config.GEMINI_MODEL), "historic": "Historic-Arabic-OCR", "qari": "Qari-OCR v0.3", "claude": "Claude (رؤية)",
                 "mock": "بيانات مرجعية (وضع تجريبي)"}


def _local_pages(state: ContractState, engine: str) -> tuple[list[PageDigitization], list[dict]]:
    """Returns the structured pages and, per page, the raw segmentation and OCR lines for the UI."""
    from ..tools.arabic_text import count_ar, to_arabic_digits
    from ..tools.ocr_structure import read_page, structure

    cid = state["contract_id"]
    n = len(state["page_images"])
    pages, records = [], []
    for i, png in enumerate(state["page_images"], start=1):
        ocr_png, enhanced = _enhance(cid, png, i, n)
        def on_layout(rec, i=i):
            emit(cid, "digitize", "info",
                 f"تقسيم الصفحة {to_arabic_digits(i)} من {to_arabic_digits(n)}: "
                 f"{count_ar(len(rec['boxes']), 'line')} و{count_ar(len(rec['stamps']), 'stamp')}",
                 {"kind": "layout", **rec, "enhanced": enhanced,
                  "read_from": "enhanced" if enhanced else "original"})
        rec = read_page(ocr_png, i, engine, on_layout, original_png=png, contract_id=cid)
        rec["enhanced"] = enhanced
        rec["read_from"] = "enhanced" if enhanced else "original"
        emit(cid, "digitize", "info",
             f"قراءة الصفحة {to_arabic_digits(i)} من {to_arabic_digits(n)} بنموذج {ENGINE_LABELS[engine]}: "
             f"{count_ar(len(rec['lines']), 'line')}", {"kind": "ocr", "page": i, "lines": rec["lines"]})
        pages.append(structure(rec["lines"], rec["stamps"], i, cid))
        records.append(rec)
    return pages, records


def enhanced_path(png: str) -> str:
    return png[: -len(".png")] + "_enhanced.png" if png.endswith(".png") else png + "_enhanced.png"


def _enhance(cid: str, png: str, page: int, n: int) -> tuple[str, dict | None]:
    """Real-ESRGAN before OCR, for small photos only. Optional: any failure falls back to the
    original page with a warning."""
    if not config.ENHANCE_IMAGES:
        return png, None
    from ..tools.arabic_text import to_arabic_digits

    try:
        from ..tools.esrgan.enhance import enhance_file

        dst = enhanced_path(png)
        info = enhance_file(png, dst)
    except Exception as exc:
        emit(cid, "digitize", "info", f"تعذر تحسين الصفحة {to_arabic_digits(page)}، ستُقرأ الصورة الأصلية",
             {"kind": "enhance", "page": page, "error": f"{type(exc).__name__}: {str(exc)[:200]}"})
        return png, None
    if info is None:
        emit(cid, "digitize", "info", f"الصفحة {to_arabic_digits(page)} بدقة كافية، لم تُحسَّن",
             {"kind": "enhance", "page": page, "skipped": True})
        return png, None
    emit(cid, "digitize", "info",
         f"تحسين دقة الصفحة {to_arabic_digits(page)} من {to_arabic_digits(n)} بنموذج {info['model']} "
         f"({to_arabic_digits(info['seconds'])} ث)", {"kind": "enhance", "page": page, **info})
    return dst, info


def _records_from_blocks(digital: DigitalContract) -> list[dict]:
    """Claude/mock engines have no separate segmentation; show their blocks as the boxes."""
    out: dict[int, dict] = {}
    for b in digital.blocks:
        rec = out.setdefault(b.page, {"page": b.page, "skew": 0.0, "boxes": [], "stamps": [], "lines": []})
        rec["boxes"].append(b.bbox)
        rec["lines"].append({"id": b.id, "text": b.text, "confidence": b.confidence, "bbox": b.bbox})
    for s in digital.stamps:
        out.setdefault(s.page, {"page": s.page, "skew": 0.0, "boxes": [], "stamps": [], "lines": []})["stamps"].append(
            {"bbox": s.bbox, "overlaps_text": s.overlaps_text})
    return [out[k] for k in sorted(out)]


def run(state: ContractState) -> dict:
    engine = config.OCR_ENGINE
    n = len(state["page_images"])
    records = None
    if engine in ("gemini", "historic", "qari"):
        pages, records = _local_pages(state, engine)
    elif engine == "mock":
        sha = mock_llm.file_sha256(state["file_path"])
        texts = state.get("text_layer", [])
        pages = [PageDigitization.model_validate(mock_llm.digitize_page(
            sha, i, texts[i - 1] if i <= len(texts) else "", _pdf_text_geometry(state["file_path"], i)))
            for i in range(1, n + 1)]
    else:
        sha = mock_llm.file_sha256(state["file_path"]) if config.mock_mode() else ""
        with ThreadPoolExecutor(max_workers=config.DIGITIZATION_CONCURRENCY) as pool:
            pages = list(pool.map(lambda i: digitize_page(state, i, sha), range(1, n + 1)))
    digital = merge(pages)
    digital.ocr_engine = ENGINE_LABELS.get(engine, engine)
    return {"digital": digital.model_dump(), "ocr_confidence": ocr_confidence(digital).model_dump(),
            "ocr_pages": records if records is not None else _records_from_blocks(digital)}
