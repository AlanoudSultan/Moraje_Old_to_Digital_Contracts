"""Turn any upload into page PNGs at RENDER_DPI plus a per-page text layer.

Handled, by content rather than trusting the extension:
- images: PNG, JPEG, TIFF (every frame), BMP, GIF, WebP, HEIC/HEIF (iPhone photos), ...
- documents PyMuPDF opens natively: PDF, XPS, EPUB, MOBI, FB2, CBZ, SVG
- Word .docx (LibreOffice when installed, else a plain RTL re-typeset of the paragraphs)
- other office files (.doc, .rtf, .odt, .xls/.xlsx, .ppt/.pptx, ...): LibreOffice, when installed
- text (.txt, .md, .csv, .json, .html, .xml, or any UTF-8 file): typeset as an RTL page
"""
from __future__ import annotations

import html
import shutil
import subprocess
from pathlib import Path

import pymupdf
from PIL import Image, ImageOps, ImageSequence, UnidentifiedImageError

from .. import config
from .arabic_text import clean_text_layer

MAX_IMAGE_SIDE = 2400
MAX_PAGES = 50

try:  # iPhone photos
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:  # pragma: no cover - optional
    pass

PYMUPDF_DOCS = {".pdf", ".xps", ".oxps", ".epub", ".mobi", ".fb2", ".cbz", ".svg"}
OFFICE_DOCS = {".doc", ".docm", ".dot", ".dotx", ".rtf", ".odt", ".ott", ".pages", ".wpd",
               ".xls", ".xlsx", ".xlsm", ".ods", ".csv_office", ".ppt", ".pptx", ".odp", ".key"}
TEXT_DOCS = {".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".xml", ".html", ".htm", ".log"}


class UnreadableFile(ValueError):
    """Raised with an Arabic message the UI can show as-is."""
    user_facing = True


def plex_css() -> tuple[str, pymupdf.Archive]:
    archive = pymupdf.Archive(str(config.FONTS_DIR))
    css = (
        "@font-face{font-family:plex;src:url(IBMPlexSansArabic-Regular.ttf);}"
        "@font-face{font-family:plex;src:url(IBMPlexSansArabic-Bold.ttf);font-weight:bold;}"
        "*{font-family:plex;}"
    )
    return css, archive


def _render_pymupdf(path: Path, out_dir: Path, filetype: str | None = None) -> tuple[list[str], list[str]]:
    images, texts = [], []
    with pymupdf.open(path, filetype=filetype) as doc:
        if doc.is_encrypted and not doc.authenticate(""):
            raise UnreadableFile("الملف محمي بكلمة مرور")
        for i, page in enumerate(doc, start=1):
            if i > MAX_PAGES:
                break
            out = out_dir / f"page_{i}.png"
            page.get_pixmap(dpi=config.RENDER_DPI).save(out)
            images.append(str(out))
            raw = page.get_text()
            texts.append(clean_text_layer(raw) if raw.strip() else "")
    if not images:
        raise UnreadableFile("الملف لا يحتوي على صفحات")
    return images, texts


def _render_image(img_path: Path, out_dir: Path) -> tuple[list[str], list[str]]:
    """Every frame of multi-page images (TIFF), EXIF rotation applied, longest side capped."""
    images = []
    with Image.open(img_path) as im:
        for i, frame in enumerate(ImageSequence.Iterator(im), start=1):
            if i > MAX_PAGES:
                break
            fr = ImageOps.exif_transpose(frame.copy())
            if fr.mode in ("RGBA", "LA", "P"):  # flatten transparency onto white paper
                fr = fr.convert("RGBA")
                bg = Image.new("RGB", fr.size, (255, 255, 255))
                bg.paste(fr, mask=fr.split()[-1])
                fr = bg
            fr = fr.convert("RGB")
            scale = MAX_IMAGE_SIDE / max(fr.size)
            if scale < 1:
                fr = fr.resize((int(fr.width * scale), int(fr.height * scale)), Image.LANCZOS)
            out = out_dir / f"page_{i}.png"
            fr.save(out)
            images.append(str(out))
            if getattr(im, "format", "") == "GIF":  # animated GIFs: the first frame is the document
                break
    return images, [""] * len(images)


def _typeset(body_html: str, out_dir: Path, name: str) -> Path:
    css, archive = plex_css()
    story = pymupdf.Story(f'<div dir="rtl" style="font-size:12pt;line-height:1.6">{body_html}</div>',
                          user_css=css, archive=archive)
    pdf = out_dir / f"{name}_converted.pdf"
    writer = pymupdf.DocumentWriter(str(pdf))
    rect = pymupdf.paper_rect("a4")
    more, pages = True, 0
    while more and pages < MAX_PAGES:
        dev = writer.begin_page(rect)
        more, _ = story.place(rect + (60, 60, -60, -60))
        story.draw(dev)
        writer.end_page()
        pages += 1
    writer.close()
    return pdf


def _libreoffice(src: Path, out_dir: Path) -> Path | None:
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    mac_app = Path("/Applications/LibreOffice.app/Contents/MacOS/soffice")
    if not soffice and mac_app.exists():
        soffice = str(mac_app)
    if not soffice:
        return None
    try:
        subprocess.run([soffice, "--headless", "--convert-to", "pdf", "--outdir", str(out_dir), str(src)],
                       check=True, capture_output=True, timeout=180)
    except (subprocess.SubprocessError, OSError):
        return None
    pdf = out_dir / (src.stem + ".pdf")
    return pdf if pdf.exists() else None


def _docx_to_pdf(docx_path: Path, out_dir: Path) -> Path:
    """LibreOffice when present, otherwise a plain RTL re-typeset of the paragraphs and tables."""
    pdf = _libreoffice(docx_path, out_dir)
    if pdf:
        return pdf
    import docx

    d = docx.Document(str(docx_path))
    parts = [f"<p>{html.escape(p.text)}</p>" for p in d.paragraphs if p.text.strip()]
    for t in d.tables:
        rows = "".join("<tr>" + "".join(f"<td>{html.escape(c.text)}</td>" for c in r.cells) + "</tr>" for r in t.rows)
        parts.append(f'<table border="1" style="border-collapse:collapse;width:100%">{rows}</table>')
    return _typeset("".join(parts), out_dir, docx_path.stem)


def _text_to_pdf(path: Path, out_dir: Path) -> Path:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-16", "cp1256"):  # cp1256: Windows Arabic
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise UnreadableFile("تعذر قراءة الملف النصي")
    if path.suffix.lower() in {".html", ".htm"}:
        body = text
    else:
        body = "".join(f"<p>{html.escape(line) or '&nbsp;'}</p>" for line in text.splitlines())
    return _typeset(body, out_dir, path.stem)


def _is_image(path: Path) -> bool:
    try:
        with Image.open(path) as im:
            im.verify()
        return True
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
        return False


def _is_text(path: Path) -> bool:
    chunk = path.read_bytes()[:4096]
    if b"\x00" in chunk:
        return False
    try:
        chunk.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def render(file_path: str, out_dir: str) -> tuple[list[str], list[str], str]:
    """Returns (page image paths, per-page text layer, file type)."""
    src = Path(file_path)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    ext = src.suffix.lower()

    if ext in PYMUPDF_DOCS:
        return (*_render_pymupdf(src, out), ext.lstrip("."))
    if ext == ".docx":
        return (*_render_pymupdf(_docx_to_pdf(src, out), out), "docx")
    if ext in OFFICE_DOCS:
        pdf = _libreoffice(src, out)
        if pdf is None:
            raise UnreadableFile(f"قراءة ملفات {ext} تتطلب تثبيت LibreOffice (brew install --cask libreoffice)، "
                                 "أو احفظ الملف بصيغة PDF أو DOCX")
        return (*_render_pymupdf(pdf, out), ext.lstrip("."))
    if ext in TEXT_DOCS:
        return (*_render_pymupdf(_text_to_pdf(src, out), out), "text")

    # Unknown or missing extension: sniff the content.
    if _is_image(src):
        return (*_render_image(src, out), "image")
    try:
        return (*_render_pymupdf(src, out), ext.lstrip(".") or "document")
    except UnreadableFile:
        raise
    except Exception:
        pass
    if _is_text(src):
        return (*_render_pymupdf(_text_to_pdf(src, out), out), "text")
    pdf = _libreoffice(src, out)
    if pdf:
        return (*_render_pymupdf(pdf, out), ext.lstrip(".") or "document")
    raise UnreadableFile(f"نوع الملف {ext or 'غير المعروف'} غير قابل للقراءة. جرّب PDF أو صورة أو DOCX")
