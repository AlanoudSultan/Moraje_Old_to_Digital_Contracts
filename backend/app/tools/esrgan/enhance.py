"""Super-resolution of small page photos with Real-ESRGAN, before OCR.

Only pages whose longest side is under config.ENHANCE_MIN_SIDE are processed: the model is for
low-resolution photos (a 370 px phone shot of a deed), not for 200 DPI renders. The page is
upscaled 4x in overlapping tiles, then sized down to at most config.ENHANCE_TARGET_SIDE.

Super-resolution reconstructs plausible detail, so the enhanced page is only what the OCR reads.
The original stays the record: the page view, stamp detection and the Word/PDF copy use it.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

import cv2
import numpy as np

from ... import config

log = logging.getLogger("enhance")

RELEASES = "https://github.com/xinntao/Real-ESRGAN/releases/download"
MODELS = {
    # name: (release file, network, state key)
    "x4plus": ("v0.1.0/RealESRGAN_x4plus.pth", "rrdb", "params_ema"),
    "general-x4v3": ("v0.2.5.0/realesr-general-x4v3.pth", "compact", "params"),
}
SCALE = 4
TILE = 256
TILE_PAD = 12


def weights_path(name: str) -> Path:
    """Official release file, downloaded once into ~/.cache/realesrgan."""
    import urllib.request

    rel, _, _ = MODELS[name]
    dst = Path.home() / ".cache" / "realesrgan" / Path(rel).name
    if not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_suffix(".part")
        urllib.request.urlretrieve(f"{RELEASES}/{rel}", tmp)
        tmp.rename(dst)
    return dst


class RealESRGAN:
    def __init__(self, name: str) -> None:
        import torch

        from .arch import RRDBNet, SRVGGNetCompact

        if name not in MODELS:
            raise ValueError(f"unknown Real-ESRGAN model: {name}")
        self.torch = torch
        self.name = name
        if torch.cuda.is_available():
            self.device = torch.device("cuda")
        elif torch.backends.mps.is_available():
            self.device = torch.device("mps")
        else:
            self.device = torch.device("cpu")
        t0 = time.time()
        _, net, key = MODELS[name]
        model = RRDBNet() if net == "rrdb" else SRVGGNetCompact()
        # weights_only: the .pth holds plain tensors; refuse anything that could run code.
        state = torch.load(weights_path(name), map_location="cpu", weights_only=True)[key]
        model.load_state_dict(state, strict=True)
        self.model = model.to(self.device).eval()  # float32: half precision is unreliable on MPS
        self.lock = threading.Lock()
        log.info("loaded Real-ESRGAN %s on %s in %.1fs", name, self.device, time.time() - t0)

    def upscale(self, bgr: np.ndarray) -> np.ndarray:
        """4x super-resolution in overlapping tiles (keeps memory flat on large inputs)."""
        torch = self.torch
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        img = torch.from_numpy(rgb.transpose(2, 0, 1)).unsqueeze(0).to(self.device)
        _, _, h, w = img.shape
        out = torch.zeros((1, 3, h * SCALE, w * SCALE), device=self.device)
        with self.lock, torch.no_grad():
            for y in range(0, h, TILE):
                for x in range(0, w, TILE):
                    x0, y0 = max(x - TILE_PAD, 0), max(y - TILE_PAD, 0)
                    x1, y1 = min(x + TILE + TILE_PAD, w), min(y + TILE + TILE_PAD, h)
                    pred = self.model(img[:, :, y0:y1, x0:x1])
                    # Keep only the tile's own area; the padding just gives the network context.
                    ty0, tx0 = (y - y0) * SCALE, (x - x0) * SCALE
                    th, tw = min(TILE, h - y) * SCALE, min(TILE, w - x) * SCALE
                    out[:, :, y * SCALE:y * SCALE + th, x * SCALE:x * SCALE + tw] = pred[:, :, ty0:ty0 + th, tx0:tx0 + tw]
        res = out.clamp(0, 1)[0].permute(1, 2, 0).cpu().numpy()
        return cv2.cvtColor((res * 255.0).round().astype(np.uint8), cv2.COLOR_RGB2BGR)


_lock = threading.Lock()
_models: dict[str, RealESRGAN] = {}


def get_model(name: str) -> RealESRGAN:
    with _lock:
        if name not in _models:
            _models[name] = RealESRGAN(name)
        return _models[name]


def enhance_file(src_png: str, dst_png: str) -> dict | None:
    """Write the enhanced page and return what was done, or None when the page is large enough
    already (nothing is written; the OCR reads the original)."""
    im = cv2.imread(src_png, cv2.IMREAD_COLOR)
    h, w = im.shape[:2]
    if max(h, w) >= config.ENHANCE_MIN_SIDE:
        return None
    t0 = time.time()
    out = get_model(config.ENHANCE_MODEL).upscale(im)
    target = config.ENHANCE_TARGET_SIDE
    if max(out.shape[:2]) > target:
        f = target / max(out.shape[:2])
        out = cv2.resize(out, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
    cv2.imwrite(dst_png, out)
    return {"model": f"Real-ESRGAN {config.ENHANCE_MODEL}", "seconds": round(time.time() - t0, 1),
            "from_size": [int(w), int(h)], "size": [int(out.shape[1]), int(out.shape[0])]}
