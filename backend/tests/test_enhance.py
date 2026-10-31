"""Super-resolution plumbing with a fake Real-ESRGAN: who gets enhanced, sizes, fallback, what the OCR reads."""
import cv2
import numpy as np

from backend.app import config, db, graph
from backend.app.tools import ocr_structure
from backend.app.tools.arabic_ocr import OcrText
from backend.app.tools.esrgan import enhance


class FakeESRGAN:
    def __init__(self):
        self.inputs = []

    def upscale(self, bgr):
        self.inputs.append(bgr.shape)
        return cv2.resize(bgr, None, fx=4, fy=4, interpolation=cv2.INTER_NEAREST)


def test_small_page_is_upscaled_and_capped(tmp_path, monkeypatch):
    fake = FakeESRGAN()
    monkeypatch.setattr(enhance, "get_model", lambda name: fake)
    src, dst = tmp_path / "p.png", tmp_path / "p_enhanced.png"
    cv2.imwrite(str(src), np.full((540, 370, 3), 200, np.uint8))
    info = enhance.enhance_file(str(src), str(dst))
    out = cv2.imread(str(dst))
    assert fake.inputs == [(540, 370, 3)]  # the original, not a pre-enlarged copy
    assert max(out.shape[:2]) == min(540 * 4, config.ENHANCE_TARGET_SIDE)
    assert abs(out.shape[1] / out.shape[0] - 370 / 540) < 0.01  # boxes stay valid on the original
    assert info["from_size"] == [370, 540] and info["size"] == [out.shape[1], out.shape[0]]


def test_large_page_is_left_alone(tmp_path, monkeypatch):
    fake = FakeESRGAN()
    monkeypatch.setattr(enhance, "get_model", lambda name: fake)
    src, dst = tmp_path / "p.png", tmp_path / "p_enhanced.png"
    cv2.imwrite(str(src), np.full((config.ENHANCE_MIN_SIDE + 10, 1200, 3), 200, np.uint8))
    assert enhance.enhance_file(str(src), str(dst)) is None
    assert not dst.exists() and fake.inputs == []


def test_tiling_matches_whole_image():
    """Tiles with padding must stitch to the same result as one pass (for a local, convolutional net)."""
    import torch

    class Up(torch.nn.Module):
        def forward(self, x):
            return torch.nn.functional.interpolate(x, scale_factor=4, mode="nearest")

    m = enhance.RealESRGAN.__new__(enhance.RealESRGAN)
    m.torch, m.device, m.model = torch, torch.device("cpu"), Up()
    import threading
    m.lock = threading.Lock()
    im = np.random.default_rng(0).integers(0, 255, (300, 530, 3), dtype=np.uint8)  # several tiles each way
    out = m.upscale(im)
    assert out.shape == (1200, 2120, 3)
    assert np.array_equal(out, cv2.resize(im, None, fx=4, fy=4, interpolation=cv2.INTER_NEAREST))


class RecordingEngine:
    """Line engine that records the size of the page its crops came from (via the crop heights)."""
    def __init__(self):
        self.crop_heights = []

    def lines(self, crops):
        self.crop_heights.append(max(c.size[1] for c in crops) if crops else 0)
        return [OcrText("أولاً: يلتزم الطرف الثاني بسداد الأجرة", 0.8) for _ in crops]


def test_ocr_reads_enhanced_page_and_failure_falls_back(new_contract, monkeypatch):
    reader = RecordingEngine()
    monkeypatch.setattr(config, "OCR_ENGINE", "historic")
    monkeypatch.setattr(config, "ENHANCE_IMAGES", True)
    monkeypatch.setattr(ocr_structure, "get_engine", lambda name: reader)
    read_pages = []
    original_analyze = ocr_structure.analyze
    monkeypatch.setattr(ocr_structure, "analyze", lambda png: (read_pages.append(png), original_analyze(png))[1])
    monkeypatch.setattr(enhance, "get_model", lambda name: FakeESRGAN())
    cid, path, name = new_contract("old_scanned_lease.jpg")  # 992 x 1404: small enough to enhance
    state = graph.run_pipeline(cid, path, name)
    assert read_pages[0].endswith("_enhanced.png")  # lines are segmented and read on the enhanced page
    assert state["page_images"][0].endswith("page_1.png")  # the original stays the record
    assert state["ocr_pages"][0]["enhanced"]["model"].startswith("Real-ESRGAN")
    assert any(p["data"].get("kind") == "enhance" for _, p in db.events_after(cid, 0))

    def broken(name):
        raise RuntimeError("no weights")

    monkeypatch.setattr(enhance, "get_model", broken)
    cid2, path2, name2 = new_contract("old_scanned_lease.jpg")
    state2 = graph.run_pipeline(cid2, path2, name2)
    assert read_pages[-1].endswith("page_1.png") and state2["ocr_pages"][0]["enhanced"] is None
    assert state2["digital"]["blocks"]  # pipeline carried on with the original
