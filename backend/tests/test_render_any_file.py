"""Uploads of any type become page images (or a clear Arabic error)."""
import pytest
from PIL import Image

from backend.app.tools.pdf_render import UnreadableFile, render


def _img(size=(400, 300), color=(240, 240, 240), mode="RGB"):
    return Image.new(mode, size, color)


def test_multipage_tiff_gives_every_page(tmp_path):
    src = tmp_path / "scan.tiff"
    _img().save(src, save_all=True, append_images=[_img(color=(200, 200, 200)), _img(color=(100, 100, 100))])
    pages, texts, kind = render(str(src), str(tmp_path / "out"))
    assert len(pages) == 3 and kind == "image" and texts == ["", "", ""]


@pytest.mark.parametrize("name,fmt", [("a.bmp", "BMP"), ("a.webp", "WEBP"), ("a.gif", "GIF"), ("a.png", "PNG")])
def test_common_image_formats(tmp_path, name, fmt):
    src = tmp_path / name
    _img(mode="RGBA", color=(0, 0, 0, 0)).save(src, fmt)  # transparency flattened onto white
    pages, _, kind = render(str(src), str(tmp_path / "out"))
    assert len(pages) == 1 and kind == "image"
    with Image.open(src) as saved:  # Pillow 13 writes BMP without the alpha channel
        kept_alpha = saved.mode in ("RGBA", "LA", "P")
    if kept_alpha:
        assert Image.open(pages[0]).getpixel((5, 5)) == (255, 255, 255)


def test_image_with_wrong_extension_is_sniffed(tmp_path):
    src = tmp_path / "photo.dat"
    _img().save(src, "JPEG")
    pages, _, kind = render(str(src), str(tmp_path / "out"))
    assert len(pages) == 1 and kind == "image"


@pytest.mark.parametrize("name,content", [
    ("contract.txt", "عقد إيجار\nالبند الأول: موضوع العقد"),
    ("contract.html", "<h1>عقد</h1><p>البند الأول</p>"),
    ("notes", "نص عربي بدون امتداد للملف"),
])
def test_text_files_are_typeset(tmp_path, name, content):
    src = tmp_path / name
    src.write_text(content, encoding="utf-8")
    pages, texts, kind = render(str(src), str(tmp_path / "out"))
    assert len(pages) >= 1 and kind in ("text", "document")


def test_unreadable_bytes_raise_a_clear_error(tmp_path, monkeypatch):
    from backend.app.tools import pdf_render

    monkeypatch.setattr(pdf_render, "_libreoffice", lambda src, out: None)
    src = tmp_path / "blob.xyz"
    src.write_bytes(bytes(range(256)) * 20)
    with pytest.raises(UnreadableFile, match="غير قابل للقراءة"):
        render(str(src), str(tmp_path / "out"))


def test_office_file_without_libreoffice_says_so(tmp_path, monkeypatch):
    from backend.app.tools import pdf_render

    monkeypatch.setattr(pdf_render, "_libreoffice", lambda src, out: None)
    src = tmp_path / "old.doc"
    src.write_bytes(b"\xd0\xcf\x11\xe0fake ole")
    with pytest.raises(UnreadableFile, match="LibreOffice"):
        render(str(src), str(tmp_path / "out"))
