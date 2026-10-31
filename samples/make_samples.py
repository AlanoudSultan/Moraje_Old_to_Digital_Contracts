"""Generate the three synthetic demo contracts plus ground-truth JSON (used by offline mock mode).

Run from the repo root:  .venv/bin/python samples/make_samples.py
"""
from __future__ import annotations

import hashlib
import html
import json
import random
import sys
from pathlib import Path

import pymupdf
from PIL import Image, ImageEnhance, ImageFilter

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from contracts_text import CLEAN_SERVICE, OLD_LEASE, RISKY_SUPPLY  # noqa: E402

FONTS = HERE.parent / "backend" / "assets" / "fonts"
CSS = (
    "@font-face{font-family:plex;src:url(IBMPlexSansArabic-Regular.ttf);}"
    "@font-face{font-family:plex;src:url(IBMPlexSansArabic-Bold.ttf);font-weight:bold;}"
    "*{font-family:plex;}"
)
W, H = pymupdf.paper_size("a4")
MARGIN = 60


def _html(kind, number, heading, text, size=12):
    body = html.escape(text).replace("\n", "<br>")
    if kind == "title":
        return f'<div dir="rtl" style="text-align:center;font-size:20px;font-weight:bold">{body}</div>'
    head = ""
    if number or heading:
        label = " ".join(x for x in [number, heading] if x)
        if number and heading:
            label = f"{number}: {heading}"
        head = f"<b>{html.escape(label)}</b><br>" if heading else f"<b>{html.escape(label)}</b> "
    return f'<div dir="rtl" style="font-size:{size}px;line-height:1.7;text-align:justify">{head}{body}</div>'


def layout(spec, size=12, gap=12, footer=True):
    """Lay out blocks top-down. Returns (doc, pages_truth) with normalized bboxes per block."""
    archive = pymupdf.Archive(str(FONTS))
    doc = pymupdf.open()
    pages: list[dict] = []

    def new_page():
        page = doc.new_page(width=W, height=H)
        pages.append({"title": None, "parties": [], "key_fields": [], "blocks": [], "tables": [], "dates": [], "stamps": [],
                      "page_header_footer": []})
        if footer:
            n = len(pages)
            label = f"صفحة {str(n).translate(str.maketrans('0123456789', '٠١٢٣٤٥٦٧٨٩'))}"
            page.insert_htmlbox(pymupdf.Rect(MARGIN, H - 40, W - MARGIN, H - 20),
                                f'<div dir="rtl" style="text-align:center;font-size:9px;color:#666">{label}</div>',
                                css=CSS, archive=archive)
            pages[-1]["page_header_footer"].append(label)
        return page

    page = new_page()
    y = MARGIN
    items = [("title", None, None, spec["title"])] + list(spec["blocks"])
    for kind, number, heading, text in items:
        snippet = _html(kind, number, heading, text, size)
        rect = pymupdf.Rect(MARGIN, y, W - MARGIN, H - 60)
        spare, _ = page.insert_htmlbox(rect, snippet, css=CSS, archive=archive, scale_low=1)
        if spare < 0:
            page = new_page()
            y = MARGIN
            rect = pymupdf.Rect(MARGIN, y, W - MARGIN, H - 60)
            spare, _ = page.insert_htmlbox(rect, snippet, css=CSS, archive=archive, scale_low=1)
        used = rect.height - spare
        bbox = [round(MARGIN / W, 4), round(y / H, 4), round((W - MARGIN) / W, 4), round((y + used) / H, 4)]
        p = pages[-1]
        bid = f"p{len(pages)}_b{len(p['blocks']) + 1}"
        if kind == "title":
            p["title"] = text
        p["blocks"].append({
            "id": bid, "kind": kind, "number_raw": number.rstrip(":") if number else None, "heading": heading,
            "text": text, "continues_from_previous_page": False, "continues_to_next_page": False,
            "confidence": round(random.uniform(0.96, 0.995), 3), "bbox": bbox,
        })
        y += used + gap
    pages[0]["parties"] = spec["parties"]
    return doc, pages


def add_dates(pages, raw_dates):
    for p in pages:
        for b in p["blocks"]:
            for raw, cal in raw_dates:
                if raw in b["text"]:
                    p["dates"].append({"raw": raw, "calendar": cal, "block_id": b["id"]})


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_truth(path: Path, pages):
    truth = {"sha256": sha256(path), "file": path.name, "pages": pages}
    (HERE / f"{path.stem}.truth.json").write_text(json.dumps(truth, ensure_ascii=False, indent=1), encoding="utf-8")


def make_pdf(spec, dates):
    doc, pages = layout(spec)
    add_dates(pages, dates)
    out = HERE / spec["file"]
    doc.set_metadata({"title": spec["title"], "creator": "synthetic demo sample"})
    doc.save(out, garbage=3, deflate=True)
    write_truth(out, pages)
    print("wrote", out, len(pages), "page(s)")


def make_scanned_lease():
    random.seed(7)
    doc, pages = layout(OLD_LEASE, size=13, gap=16, footer=False)
    page = doc[0]
    blocks = pages[0]["blocks"]
    by_num = {b["number_raw"]: b for b in blocks if b["number_raw"]}

    # Official-looking round stamp partly over clause "خامساً" (synthetic, no real entity).
    target = by_num["خامساً"]
    cx, cy = W * 0.30, H * (target["bbox"][1] + target["bbox"][3]) / 2
    r = 52
    blue = (0.12, 0.25, 0.65)
    page.draw_circle((cx, cy), r, color=blue, width=2.2, stroke_opacity=0.75)
    page.draw_circle((cx, cy), r - 9, color=blue, width=1, stroke_opacity=0.75)
    page.insert_htmlbox(pymupdf.Rect(cx - r + 12, cy - 18, cx + r - 12, cy + 22),
                        '<div dir="rtl" style="text-align:center;font-size:9px;color:#2340a6;font-weight:bold">'
                        'مكتب العقار النموذجي<br>جدة</div>',
                        css=CSS, archive=pymupdf.Archive(str(FONTS)), opacity=0.8)
    stamp_bbox = [round((cx - r) / W, 4), round((cy - r) / H, 4), round((cx + r) / W, 4), round((cy + r) / H, 4)]

    # Hand signature scribble in the signature area.
    sig = blocks[-1]
    sy = H * sig["bbox"][3] + 18
    pts = [(W * 0.62 + i * 6, sy + 10 * ((-1) ** i) * random.uniform(0.3, 1)) for i in range(16)]
    page.draw_polyline(pts, color=(0.05, 0.05, 0.3), width=1.4)
    sig_bbox = [round(W * 0.6 / W, 4), round((sy - 14) / H, 4), round((W * 0.62 + 100) / W, 4), round((sy + 14) / H, 4)]

    # Ground truth reflects what a careful reader can still see on a degraded scan.
    for b in blocks:
        b["confidence"] = round(random.uniform(0.80, 0.9), 3)
    target["text"] = target["text"].replace("مدة شهر", "مدة [؟]")
    target["confidence"] = 0.58
    blocks[0]["confidence"] = 0.74
    pages[0]["stamps"] = [
        {"bbox": stamp_bbox, "kind": "stamp", "text_if_any": "مكتب العقار النموذجي جدة", "overlaps_text": True},
        {"bbox": sig_bbox, "kind": "signature", "text_if_any": None, "overlaps_text": False},
    ]
    add_dates(pages, [("٠٥/٠٩/١٤٢٨هـ", "hijri"), ("٠١/١٠/١٤٢٨هـ", "hijri"), ("٣٠/٠٩/١٤٢٩هـ", "hijri")])

    pix = page.get_pixmap(dpi=120)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    # Aged paper tone, skew, blur, speckle, low-quality JPEG.
    tint = Image.new("RGB", img.size, (236, 224, 196))
    img = Image.blend(img, tint, 0.28)
    img = img.rotate(1.6, resample=Image.BICUBIC, fillcolor=(222, 210, 182))
    img = img.filter(ImageFilter.GaussianBlur(1.1))
    px = img.load()
    for _ in range(img.width * img.height // 60):
        x, y = random.randrange(img.width), random.randrange(img.height)
        v = random.randint(90, 170)
        px[x, y] = (v, v - 10, v - 25)
    img = ImageEnhance.Contrast(img).enhance(0.85)
    out = HERE / OLD_LEASE["file"]
    img.save(out, "JPEG", quality=55)
    write_truth(out, pages)
    print("wrote", out)


if __name__ == "__main__":
    random.seed(42)
    make_pdf(CLEAN_SERVICE, [("١٢/٠٧/١٤٤٧هـ", "hijri"), ("٠١/٠١/٢٠٢٦م", "gregorian"), ("٣١/١٢/٢٠٢٦م", "gregorian")])
    make_pdf(RISKY_SUPPLY, [("١٥/٠٣/٢٠٢٦م", "gregorian")])
    make_scanned_lease()
