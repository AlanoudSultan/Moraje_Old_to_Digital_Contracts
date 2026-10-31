"""Kraken line segmentation: worker protocol (with a fake worker), normalization, fallback, drawing, pipeline."""
import sys
import textwrap

from backend.app import config, graph
from backend.app.tools import gemini_ocr, kraken_seg
from backend.app.tools.gemini_ocr import PageLine, annotated_jpeg
from backend.app.tools.kraken_seg import KLine, to_lines

FAKE_WORKER = """
import json, sys
print("library chatter without the marker")
print("@@KRAKEN@@ " + json.dumps({"ready": True}), flush=True)
for raw in sys.stdin:
    path = json.loads(raw)["path"]
    if path.endswith("bad.png"):
        print("@@KRAKEN@@ " + json.dumps({"error": "cannot read"}), flush=True)
        continue
    print("@@KRAKEN@@ " + json.dumps({"size": [200, 100], "lines": [
        {"baseline": [[180, 30], [20, 40]], "boundary": [[190, 20], [10, 30], [10, 45], [190, 35]]},
        {"baseline": [[180, 70], [20, 70]], "boundary": [[1, 1], [2, 1], [2, 1]]}]}), flush=True)
"""


def _fake_worker(monkeypatch, tmp_path):
    script = tmp_path / "fake_worker.py"
    script.write_text(textwrap.dedent(FAKE_WORKER))
    monkeypatch.setattr(config, "SEGMENTER", "kraken")
    monkeypatch.setattr(config, "KRAKEN_PYTHON", sys.executable)
    monkeypatch.setattr(kraken_seg, "WORKER", script)
    monkeypatch.setattr(kraken_seg, "_worker", None)


def test_to_lines_normalizes_and_drops_degenerate():
    lines = to_lines({"size": [200, 100], "lines": [
        {"baseline": [[180, 30], [20, 40]], "boundary": [[190, 20], [10, 30], [10, 45], [190, 35]]},
        {"baseline": [], "boundary": [[1, 1], [2, 1], [2, 1]]},  # a speck: dropped
    ]})
    assert len(lines) == 1
    assert lines[0].polygon[0] == [0.95, 0.2] and lines[0].bbox == [0.05, 0.2, 0.95, 0.45]
    assert lines[0].baseline == [[0.9, 0.3], [0.1, 0.4]]


def test_worker_protocol_and_reuse(monkeypatch, tmp_path):
    _fake_worker(monkeypatch, tmp_path)
    first = kraken_seg.segment(str(tmp_path / "p1.png"))
    worker = kraken_seg._worker
    second = kraken_seg.segment(str(tmp_path / "p2.png"))
    assert first and second and len(first) == 1 and isinstance(first[0], KLine)
    assert kraken_seg._worker is worker  # one long-lived worker serves every page
    worker.close()


def test_worker_error_falls_back(monkeypatch, tmp_path):
    _fake_worker(monkeypatch, tmp_path)
    assert kraken_seg.segment(str(tmp_path / "bad.png")) is None  # caller uses classical boxes


def test_off_or_missing_environment(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SEGMENTER", "classic")
    assert kraken_seg.segment("p.png") is None
    monkeypatch.setattr(config, "SEGMENTER", "kraken")
    monkeypatch.setattr(config, "KRAKEN_PYTHON", str(tmp_path / "missing" / "python"))
    assert not kraken_seg.available() and kraken_seg.segment("p.png") is None


def test_tilted_outline_is_drawn(tmp_path):
    from PIL import Image

    png = tmp_path / "p.png"
    Image.new("RGB", (400, 600), (255, 255, 255)).save(png)
    poly = [[0.9, 0.1], [0.1, 0.2], [0.1, 0.25], [0.9, 0.15]]  # slopes down to the left
    _, size, im = annotated_jpeg(str(png), [[0.1, 0.1, 0.9, 0.25]], [poly])
    assert size[0] > 400
    r, g, b = im.getpixel((360, 60))  # right end of the top edge (0.9, 0.1)
    assert b > r and b > g
    assert im.getpixel((40, 60)) == (255, 255, 255)  # a level box would have drawn here; the tilted outline does not


def test_pipeline_uses_kraken_polygons(new_contract, monkeypatch):
    polys = [[[0.9, 0.1 + i * 0.1], [0.1, 0.12 + i * 0.1], [0.1, 0.17 + i * 0.1], [0.9, 0.15 + i * 0.1]] for i in range(3)]
    klines = [KLine(polygon=p, baseline=[], bbox=[0.1, min(y for _, y in p), 0.9, max(y for _, y in p)]) for p in polys]

    class FakeGemini:
        def page(self, png, contract_id=None, boxes=None, polygons=None):
            self.polygons = polygons
            return [PageLine(text="السطر الأول من الصك مقروء", confidence=0.88, box=1),
                    PageLine(text="السطر الثاني الجزء الأول", confidence=0.88, box=2),
                    PageLine(text="السطر الثاني الجزء الثاني", confidence=0.88, box=2),
                    PageLine(text="السطر الثالث من الصك", confidence=0.88, box=3)]

    fake = FakeGemini()
    monkeypatch.setattr(config, "OCR_ENGINE", "gemini")
    monkeypatch.setattr(gemini_ocr, "get_engine", lambda: fake)
    monkeypatch.setattr(kraken_seg, "segment", lambda png: klines)
    cid, path, name = new_contract("old_scanned_lease.jpg")
    rec = graph.run_pipeline(cid, path, name)["ocr_pages"][0]
    assert rec["segmenter"] == "kraken" and rec["polygons"] == polys and fake.polygons == polys
    assert rec["boxes"] == [k.bbox for k in klines]
    lines = rec["lines"]
    assert lines[0]["polygon"] == polys[0] and lines[3]["polygon"] == polys[2]
    assert "polygon" not in lines[1] and "polygon" not in lines[2]  # two lines share box 2: split boxes instead
    assert lines[1]["bbox"][3] <= lines[2]["bbox"][1] + 1e-9


def test_classic_boxes_when_kraken_is_off(new_contract, monkeypatch):
    class FakeGemini:
        def page(self, png, contract_id=None, boxes=None, polygons=None):
            self.polygons = polygons
            return [PageLine(text="سطر مقروء من الصفحة", confidence=0.88, box=1)]

    fake = FakeGemini()
    monkeypatch.setattr(config, "OCR_ENGINE", "gemini")
    monkeypatch.setattr(gemini_ocr, "get_engine", lambda: fake)
    cid, path, name = new_contract("old_scanned_lease.jpg")
    rec = graph.run_pipeline(cid, path, name)["ocr_pages"][0]  # conftest sets SEGMENTER=classic
    assert rec["segmenter"] == "classic" and "polygons" not in rec and fake.polygons is None


def test_failed_kraken_is_flagged(new_contract, monkeypatch):
    class FakeGemini:
        def page(self, png, contract_id=None, boxes=None, polygons=None):
            return [PageLine(text="سطر مقروء من الصفحة", confidence=0.88, box=1)]

    monkeypatch.setattr(config, "OCR_ENGINE", "gemini")
    monkeypatch.setattr(config, "SEGMENTER", "kraken")
    monkeypatch.setattr(gemini_ocr, "get_engine", lambda: FakeGemini())
    monkeypatch.setattr(kraken_seg, "segment", lambda png: None)  # worker missing or crashed
    cid, path, name = new_contract("old_scanned_lease.jpg")
    rec = graph.run_pipeline(cid, path, name)["ocr_pages"][0]
    assert rec["segmenter"] == "classic" and rec["segmenter_fallback"] is True  # the UI says Kraken failed
