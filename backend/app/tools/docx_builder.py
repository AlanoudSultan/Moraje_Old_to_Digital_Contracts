"""Deterministic reconstruction: DigitalContract JSON -> RTL .docx (and .pdf). No LLM.

Spec: agent_skills/reconstruction/SKILL.md
"""
from __future__ import annotations

import html
import shutil
import subprocess
from datetime import date
from pathlib import Path

import pymupdf
from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_COLOR_INDEX
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor
from PIL import Image

from .. import config
from .arabic_text import gregorian_today_ar, hijri_today_ar, to_arabic_digits

GREEN = RGBColor(0x1B, 0x83, 0x54)
GRAY = RGBColor(0x6C, 0x73, 0x7F)
STAMP_CAPTIONS = {"stamp": "ختم (صورة من الأصل)", "seal": "ختم (صورة من الأصل)",
                  "signature": "توقيع (صورة من الأصل)", "fingerprint": "بصمة (صورة من الأصل)"}
DISCLAIMER = "هذه نسخة رقمية مستخرجة آلياً وليست بديلاً عن الأصل"


def pick_font() -> str:
    dirs = [Path.home() / "Library/Fonts", Path("/Library/Fonts"), Path("/usr/share/fonts"),
            Path("/usr/local/share/fonts"), Path.home() / ".fonts", Path("C:/Windows/Fonts")]
    for d in dirs:
        if d.exists() and any(d.rglob("IBMPlexSansArabic*")):
            return "IBM Plex Sans Arabic"
    return "Arial"


# ---------- low-level RTL helpers ----------

def _insert_ordered(parent, tag: str, successors: tuple[str, ...]) -> None:
    """Insert an empty element before its first schema successor so Word accepts the XML."""
    if parent.find(qn(tag)) is not None:
        return
    el = OxmlElement(tag)
    for succ in successors:
        found = parent.find(qn(succ))
        if found is not None:
            found.addprevious(el)
            return
    parent.append(el)


_PPR_AFTER_BIDI = ("w:adjustRightInd", "w:snapToGrid", "w:spacing", "w:ind", "w:contextualSpacing",
                   "w:mirrorIndents", "w:suppressOverlap", "w:jc", "w:textDirection", "w:textAlignment",
                   "w:textboxTightWrap", "w:outlineLvl", "w:divId", "w:cnfStyle", "w:rPr", "w:sectPr",
                   "w:pPrChange")


def _set_bidi(paragraph) -> None:
    _insert_ordered(paragraph._p.get_or_add_pPr(), "w:bidi", _PPR_AFTER_BIDI)


def _style_run(run, font: str, size: float, bold: bool = False, color: RGBColor | None = None) -> None:
    # python-docx inserts these in schema order; only szCs needs manual placement (right after sz).
    run.font.name = font
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.cs_bold = bold
    run.font.rtl = True
    if color is not None:
        run.font.color.rgb = color
    rpr = run._r.get_or_add_rPr()
    fonts = rpr.get_or_add_rFonts()
    for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        fonts.set(qn(attr), font)
    sz = rpr.find(qn("w:sz"))
    szcs = OxmlElement("w:szCs")
    szcs.set(qn("w:val"), str(int(size * 2)))
    sz.addnext(szcs)


def add_par(container, text: str, font: str, size: float = 13, bold: bool = False,
            align=WD_ALIGN_PARAGRAPH.JUSTIFY, color: RGBColor | None = None, highlight: bool = False,
            space_after: float = 6):
    p = container.add_paragraph()
    _set_bidi(p)
    p.alignment = align
    p.paragraph_format.space_after = Pt(space_after)
    if text:
        r = p.add_run(text)
        _style_run(r, font, size, bold, color)
        if highlight:
            r.font.highlight_color = WD_COLOR_INDEX.YELLOW
    return p


def add_run(p, text: str, font: str, size: float = 13, bold: bool = False, color=None, highlight=False,
            superscript=False):
    r = p.add_run(text)
    _style_run(r, font, size, bold, color)
    if highlight:
        r.font.highlight_color = WD_COLOR_INDEX.YELLOW
    if superscript:
        r.font.superscript = True
    return r


def _table_rtl(table) -> None:
    _insert_ordered(table._tbl.tblPr, "w:bidiVisual",
                    ("w:tblStyleRowBandSize", "w:tblStyleColBandSize", "w:tblW", "w:jc", "w:tblCellSpacing",
                     "w:tblInd", "w:tblBorders", "w:shd", "w:tblLayout", "w:tblCellMar", "w:tblLook"))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER


def _section_rtl(section) -> None:
    _insert_ordered(section._sectPr, "w:bidi", ("w:rtlGutter", "w:docGrid", "w:printerSettings", "w:sectPrChange"))


def _page_field(paragraph, font: str) -> None:
    run = paragraph.add_run()
    _style_run(run, font, 9)
    for tag, attrs in (("w:fldChar", {"w:fldCharType": "begin"}), ("w:instrText", None),
                       ("w:fldChar", {"w:fldCharType": "end"})):
        el = OxmlElement(tag)
        if attrs:
            for k, v in attrs.items():
                el.set(qn(k), v)
        else:
            el.set(qn("xml:space"), "preserve")
            el.text = "PAGE"
        run._r.append(el)


def _shade(cell, hex_fill: str) -> None:
    tcpr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_fill)
    tcpr.append(shd)


def _cell_text(cell, text: str, font: str, size: float, bold=False, color=None, highlight=False) -> None:
    p = cell.paragraphs[0]
    _set_bidi(p)
    add_run(p, text, font, size, bold=bold, color=color, highlight=highlight)


def group_key_fields(key_fields: list[dict]) -> list[tuple[str, list[dict]]]:
    groups: dict[str, list[dict]] = {}
    for kf in key_fields:
        groups.setdefault(kf.get("section") or "البيانات الأساسية", []).append(kf)
    return list(groups.items())


def add_key_fields(doc, key_fields: list[dict], font: str, highlight_below: float) -> None:
    """Electronic-deed layout: a green band per section, then label/value pairs two per row."""
    white = RGBColor(0xFF, 0xFF, 0xFF)
    for section, fields in group_key_fields(key_fields):
        pairs = [fields[i:i + 2] for i in range(0, len(fields), 2)]
        t = doc.add_table(rows=1 + len(pairs), cols=4)
        t.style = "Table Grid"
        _table_rtl(t)
        band = t.rows[0].cells[0].merge(t.rows[0].cells[3])
        _shade(band, "1B8354")
        _cell_text(band, section, font, 11, bold=True, color=white)
        for row, pair in zip(t.rows[1:], pairs):
            for j, kf in enumerate(pair):
                label_cell, value_cell = row.cells[j * 2], row.cells[j * 2 + 1]
                _shade(label_cell, "E7F4EC")
                _cell_text(label_cell, kf["label"], font, 10, bold=True, color=GREEN)
                _cell_text(value_cell, kf["value"], font, 10.5, highlight=kf.get("confidence", 1) < highlight_below)
        add_par(doc, "", font, 4, space_after=2)


# ---------- stamps ----------

def crop_stamp(page_png: str, bbox: list[float], out_path: Path, pad: float = 0.02) -> Path:
    with Image.open(page_png) as im:
        w, h = im.size
        x0, y0, x1, y1 = bbox
        box = (max(0, int((x0 - pad) * w)), max(0, int((y0 - pad) * h)),
               min(w, int((x1 + pad) * w)), min(h, int((y1 + pad) * h)))
        if box[2] - box[0] < 4 or box[3] - box[1] < 4:
            box = (0, 0, w, h)
        im.crop(box).save(out_path)
    return out_path


# ---------- main builder ----------

def build_docx(digital: dict, page_images: list[str], source_name: str, out_dir: Path,
               highlight_below: float, today: date | None = None) -> tuple[Path, list[dict]]:
    """Returns (docx path, list of stamp crops {id, path, caption, near_block_id})."""
    font = pick_font()
    doc = Document()
    sec = doc.sections[0]
    sec.page_height, sec.page_width = Cm(29.7), Cm(21.0)
    for side in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
        setattr(sec, side, Cm(2.5))
    _section_rtl(sec)

    # Header on every page.
    hp = sec.header.paragraphs[0]
    _set_bidi(hp)
    hp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    add_run(hp, "نسخة رقمية", font, 12, bold=True, color=GREEN)
    add_par(sec.header, f"المصدر: {source_name}", font, 9, align=WD_ALIGN_PARAGRAPH.CENTER, color=GRAY,
            space_after=0)
    add_par(sec.header, f"تاريخ المعالجة: {gregorian_today_ar(today)} الموافق {hijri_today_ar(today)}", font, 9,
            align=WD_ALIGN_PARAGRAPH.CENTER, color=GRAY, space_after=0)

    # Footer: page number + disclaimer.
    fp = sec.footer.paragraphs[0]
    _set_bidi(fp)
    fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    add_run(fp, "صفحة ", font, 9, color=GRAY)
    _page_field(fp, font)
    add_par(sec.footer, DISCLAIMER, font, 8, align=WD_ALIGN_PARAGRAPH.CENTER, color=GRAY, space_after=0)

    title = digital.get("title") or "عقد"
    add_par(doc, title, font, 18, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER, space_after=12)

    parties = digital.get("parties") or []
    if parties:
        t = doc.add_table(rows=len(parties), cols=2)
        t.style = "Table Grid"
        _table_rtl(t)
        for row, party in zip(t.rows, parties):
            row.cells[0].paragraphs[0].text = ""
            p0 = row.cells[0].paragraphs[0]
            _set_bidi(p0)
            add_run(p0, party["role_ar"], font, 12, bold=True)
            p1 = row.cells[1].paragraphs[0]
            _set_bidi(p1)
            add_run(p1, party["name"] + (f" - {party['id_number']}" if party.get("id_number") else ""), font, 12)
        add_par(doc, "", font, 6)

    if digital.get("key_fields"):
        add_key_fields(doc, digital["key_fields"], font, highlight_below)
        add_par(doc, "النص الكامل", font, 13, bold=True, color=GREEN, space_after=4)

    # Stamp crops, placed after the block they sit near.
    crops_dir = out_dir / "stamps"
    crops_dir.mkdir(parents=True, exist_ok=True)
    stamps_by_block: dict[str | None, list[dict]] = {}
    crops = []
    for s in digital.get("stamps", []):
        if s["page"] > len(page_images):
            continue
        path = crop_stamp(page_images[s["page"] - 1], s["bbox"], crops_dir / f"{s['id']}.png")
        item = {"id": s["id"], "path": str(path), "caption": STAMP_CAPTIONS.get(s["kind"], STAMP_CAPTIONS["stamp"]),
                "near_block_id": s.get("near_block_id")}
        crops.append(item)
        stamps_by_block.setdefault(s.get("near_block_id"), []).append(item)

    blocks = {b["id"]: b for b in digital.get("blocks", [])}
    notes: list[str] = []
    placed: set[str] = set()

    def place_stamps(block_id: str | None):
        for item in stamps_by_block.get(block_id, []):
            if item["id"] in placed:
                continue
            placed.add(item["id"])
            with Image.open(item["path"]) as im:
                ratio = im.height / max(1, im.width)
            width = Cm(4) if ratio <= 1.5 else Cm(4 / ratio * 1.5)
            pic_p = add_par(doc, "", font, 10, align=WD_ALIGN_PARAGRAPH.CENTER, space_after=0)
            pic_p.add_run().add_picture(item["path"], width=width)
            add_par(doc, item["caption"], font, 9, align=WD_ALIGN_PARAGRAPH.CENTER, color=GRAY)

    def note_marker(p, conf: float):
        notes.append(f"يحتاج تحقق (الثقة: {to_arabic_digits(f'{conf:.2f}')})")
        add_run(p, f" [{to_arabic_digits(len(notes))}]", font, 9, color=GRAY, superscript=True)

    def add_block(b: dict, size: float):
        """One paragraph per block; with per-line confidences only the doubtful lines are highlighted."""
        lines = b["text"].split("\n")
        confs = b.get("line_confidences") or []
        if len(confs) != len(lines):
            low = b["confidence"] < highlight_below
            p = add_par(doc, b["text"], font, size, highlight=low)
            if low:
                note_marker(p, b["confidence"])
            return
        p = add_par(doc, "", font, size)
        for i, (line, conf) in enumerate(zip(lines, confs)):
            if i:
                p.add_run().add_break()
            add_run(p, line, font, size, highlight=conf < highlight_below)
            if conf < highlight_below:
                note_marker(p, conf)

    in_clause: set[str] = set()
    for c in digital.get("clauses", []):
        in_clause.update(c["block_ids"])
        label = " ".join(x for x in [c.get("number_raw"), c.get("heading")] if x)
        if c.get("number_raw") and c.get("heading"):
            label = f"{c['number_raw']}: {c['heading']}"
        if label:
            add_par(doc, label, font, 13, bold=True, align=WD_ALIGN_PARAGRAPH.JUSTIFY, space_after=2)
        for bid in c["block_ids"]:
            b = blocks.get(bid)
            if not b:
                continue
            add_block(b, 13)
            place_stamps(bid)

    # Blocks outside clauses (signature areas etc.) keep their place at the end.
    for b in digital.get("blocks", []):
        if b["id"] in in_clause or b["kind"] == "title":
            place_stamps(b["id"])
            continue
        add_block(b, 12)
        place_stamps(b["id"])
    place_stamps(None)
    for item in crops:  # anything not yet placed
        if item["id"] not in placed:
            stamps_by_block.setdefault("__rest__", []).append(item)
    place_stamps("__rest__")

    # Extracted tables.
    for tbl in digital.get("tables", []):
        rows = tbl.get("rows") or []
        if not rows:
            continue
        ncols = max(len(r) for r in rows)
        t = doc.add_table(rows=len(rows), cols=ncols)
        t.style = "Table Grid"
        _table_rtl(t)
        for r_i, r in enumerate(rows):
            for c_i in range(ncols):
                cell = t.rows[r_i].cells[c_i]
                if r_i == 0:
                    _shade(cell, "1B8354")
                _cell_text(cell, r[c_i] if c_i < len(r) else "", font, 11, bold=r_i == 0,
                           color=RGBColor(0xFF, 0xFF, 0xFF) if r_i == 0 else None,
                           highlight=r_i > 0 and tbl["confidence"] < highlight_below)

    if notes:
        add_par(doc, "حواشي التحقق", font, 12, bold=True, space_after=2)
        for i, n in enumerate(notes, start=1):
            add_par(doc, f"[{to_arabic_digits(i)}] {n}", font, 10, color=GRAY, space_after=0)

    # Appendix: original page images.
    doc.add_page_break()
    add_par(doc, "ملحق: صور الصفحات الأصلية", font, 14, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER)
    for i, img in enumerate(page_images, start=1):
        p = add_par(doc, "", font, 10, align=WD_ALIGN_PARAGRAPH.CENTER, space_after=0)
        p.add_run().add_picture(img, width=Cm(12))
        add_par(doc, f"الصفحة {to_arabic_digits(i)}", font, 9, align=WD_ALIGN_PARAGRAPH.CENTER, color=GRAY)

    out = out_dir / "digital_copy.docx"
    doc.save(out)
    return out, crops


# ---------- PDF ----------

def docx_to_pdf_libreoffice(docx_path: Path) -> Path | None:
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    mac_app = Path("/Applications/LibreOffice.app/Contents/MacOS/soffice")
    if not soffice and mac_app.exists():
        soffice = str(mac_app)
    if not soffice:
        return None
    try:
        subprocess.run([soffice, "--headless", "--convert-to", "pdf", "--outdir", str(docx_path.parent),
                        str(docx_path)], check=True, capture_output=True, timeout=180)
    except (subprocess.SubprocessError, OSError):
        return None
    pdf = docx_path.with_suffix(".pdf")
    return pdf if pdf.exists() else None


def build_pdf_fallback(digital: dict, crops: list[dict], page_images: list[str], source_name: str,
                       out_dir: Path, highlight_below: float, today: date | None = None) -> Path:
    """Same content typeset with PyMuPDF when LibreOffice is not installed."""
    from .pdf_render import plex_css

    css, _ = plex_css()
    css += (".b{margin-bottom:8px;text-align:justify;font-size:12px;line-height:1.7}"
            ".h{font-weight:bold;font-size:13px;margin-top:6px}.low{background-color:#FFF3A3}"
            ".cap{font-size:9px;color:#6C737F;text-align:center}.t{text-align:center;font-size:18px;font-weight:bold}"
            "td{border:1px solid #999;padding:4px;font-size:11px}"
            ".band{background-color:#1B8354;color:#FFFFFF;font-weight:bold}"
            ".lbl{background-color:#E7F4EC;color:#1B8354;font-weight:bold}")
    archive = pymupdf.Archive()
    archive.add(pymupdf.Archive(str(config.FONTS_DIR)))
    archive.add(pymupdf.Archive(str(out_dir)))
    blocks = {b["id"]: b for b in digital.get("blocks", [])}
    by_block: dict = {}
    for c in crops:
        by_block.setdefault(c.get("near_block_id"), []).append(c)

    def stamp_html(bid):
        out = ""
        for c in by_block.pop(bid, []):
            rel = Path(c["path"]).relative_to(out_dir).as_posix()
            out += f'<p style="text-align:center"><img src="{rel}" width="110"/></p><p class="cap">{c["caption"]}</p>'
        return out

    def block_html(b):
        lines, confs = b["text"].split("\n"), b.get("line_confidences") or []
        if len(confs) == len(lines) and len(lines) > 1:
            parts_ = []
            for line, c in zip(lines, confs):
                note = (f' <span style="font-size:8px;color:#6C737F">({to_arabic_digits(format(c, ".2f"))})</span>'
                        if c < highlight_below else "")
                cls_ = ' class="low"' if c < highlight_below else ""
                parts_.append(f"<span{cls_}>{html.escape(line)}</span>{note}")
            return f'<p class="b">{"<br/>".join(parts_)}</p>' + stamp_html(b["id"])
        low = b["confidence"] < highlight_below
        cls = "b low" if low else "b"
        conf = to_arabic_digits(format(b["confidence"], ".2f"))
        note = f' <span style="font-size:8px;color:#6C737F">(يحتاج تحقق - الثقة: {conf})</span>' if low else ""
        return f'<p class="{cls}">{html.escape(b["text"]).replace(chr(10), "<br/>")}{note}</p>' + stamp_html(b["id"])

    parts = [f'<p class="t">{html.escape(digital.get("title") or "عقد")}</p>']
    if digital.get("parties"):
        rows = "".join(f"<tr><td><b>{html.escape(p['role_ar'])}</b></td><td>{html.escape(p['name'])}"
                       f"{' - ' + html.escape(p['id_number']) if p.get('id_number') else ''}</td></tr>"
                       for p in digital["parties"])
        parts.append(f'<table style="width:100%;border-collapse:collapse">{rows}</table><p></p>')
    for section, fields in group_key_fields(digital.get("key_fields") or []):
        rows = f'<tr><td class="band" colspan="4">{html.escape(section)}</td></tr>'
        for i in range(0, len(fields), 2):
            cells = ""
            for kf in fields[i:i + 2]:
                low = ' class="low"' if kf.get("confidence", 1) < highlight_below else ""
                cells += f'<td class="lbl">{html.escape(kf["label"])}</td><td{low}>{html.escape(kf["value"])}</td>'
            rows += f"<tr>{cells}</tr>"
        parts.append(f'<table style="width:100%;border-collapse:collapse">{rows}</table><p></p>')
    used = set()
    for c in digital.get("clauses", []):
        label = f"{c['number_raw']}: {c['heading']}" if c.get("number_raw") and c.get("heading") else \
            (c.get("number_raw") or c.get("heading") or "")
        if label:
            parts.append(f'<p class="h">{html.escape(label)}</p>')
        for bid in c["block_ids"]:
            if bid in blocks:
                used.add(bid)
                parts.append(block_html(blocks[bid]))
    for b in digital.get("blocks", []):
        if b["id"] not in used and b["kind"] != "title":
            parts.append(block_html(b))
    for key in list(by_block):
        parts.append(stamp_html(key))
    for tbl in digital.get("tables", []):
        rows = ""
        for r_i, r in enumerate(tbl.get("rows") or []):
            cls = ' class="band"' if r_i == 0 else ""
            rows += "<tr>" + "".join(f"<td{cls}>{html.escape(c)}</td>" for c in r) + "</tr>"
        if rows:
            parts.append(f'<table style="width:100%;border-collapse:collapse">{rows}</table><p></p>')

    story = pymupdf.Story(f'<div dir="rtl">{"".join(parts)}</div>', user_css=css, archive=archive)
    tmp = out_dir / "_body.pdf"
    writer = pymupdf.DocumentWriter(str(tmp))
    rect = pymupdf.paper_rect("a4")
    where = rect + (71, 95, -71, -80)
    more = True
    while more:
        dev = writer.begin_page(rect)
        more, _ = story.place(where)
        story.draw(dev)
        writer.end_page()
    writer.close()

    doc = pymupdf.open(tmp)
    head_css = css + ".hd{text-align:center;font-size:9px;color:#6C737F}.g{color:#1B8354;font-weight:bold;font-size:12px}"
    head = (f'<div dir="rtl" class="hd"><span class="g">نسخة رقمية</span><br/>المصدر: {html.escape(source_name)}<br/>'
            f'تاريخ المعالجة: {gregorian_today_ar(today)} الموافق {hijri_today_ar(today)}</div>')
    total = doc.page_count
    for i, page in enumerate(doc, start=1):
        page.insert_htmlbox(pymupdf.Rect(71, 20, rect.width - 71, 90), head, css=head_css, archive=archive)
        foot = (f'<div dir="rtl" class="hd">صفحة {to_arabic_digits(i)} من {to_arabic_digits(total)}<br/>'
                f'{DISCLAIMER}</div>')
        page.insert_htmlbox(pymupdf.Rect(71, rect.height - 60, rect.width - 71, rect.height - 15), foot,
                            css=head_css, archive=archive)
    # Appendix: page images.
    for i, img in enumerate(page_images, start=1):
        page = doc.new_page(width=rect.width, height=rect.height)
        page.insert_htmlbox(pymupdf.Rect(71, 30, rect.width - 71, 70),
                            f'<div dir="rtl" class="hd"><span class="g">ملحق: صور الصفحات الأصلية - الصفحة '
                            f'{to_arabic_digits(i)}</span></div>', css=head_css, archive=archive)
        page.insert_image(pymupdf.Rect(60, 80, rect.width - 60, rect.height - 40), filename=img, keep_proportion=True)
    out = out_dir / "digital_copy.pdf"
    doc.save(out, garbage=3, deflate=True)
    doc.close()
    tmp.unlink(missing_ok=True)
    return out
