"""Line segmentation with Kraken's baseline model (blla), for tilted and curved handwriting.

Kraken lives in its own environment (KRAKEN_PYTHON, default .venv-kraken) because it pins older
PyTorch, safetensors and click than the app. One long-lived worker process (kraken_worker.py) loads
the model once and serves pages over stdin/stdout; calls are serialized with a lock. Each line comes
back as a polygon that follows its slope, plus its baseline, normalized 0..1 to the page image.

Any failure (environment missing, worker crash, timeout) returns None, and the caller falls back to
the classical segmentation in page_layout.py.
"""
from __future__ import annotations

import json
import logging
import queue
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

from .. import config

log = logging.getLogger("ocr")

MARK = "@@KRAKEN@@ "
WORKER = Path(__file__).with_name("kraken_worker.py")
START_TIMEOUT_S = 180  # first start imports PyTorch and loads the model
PAGE_TIMEOUT_S = 120


@dataclass
class KLine:
    polygon: list[list[float]]   # boundary, normalized (x, y) points
    baseline: list[list[float]]  # normalized (x, y) points
    bbox: list[float]            # x0, y0, x1, y1 around the polygon


def available() -> bool:
    return config.SEGMENTER == "kraken" and Path(config.KRAKEN_PYTHON).exists()


def to_lines(result: dict) -> list[KLine]:
    """Worker output in pixels -> normalized lines; drops degenerate polygons."""
    w, h = result["size"]
    out = []
    for l in result.get("lines", []):
        poly = [[round(min(max(x / w, 0.0), 1.0), 4), round(min(max(y / h, 0.0), 1.0), 4)] for x, y in l["boundary"]]
        if len(poly) < 3:
            continue
        xs, ys = [p[0] for p in poly], [p[1] for p in poly]
        if max(xs) - min(xs) < 0.005 or max(ys) - min(ys) < 0.002:
            continue
        base = [[round(x / w, 4), round(y / h, 4)] for x, y in l.get("baseline", [])]
        out.append(KLine(polygon=poly, baseline=base, bbox=[min(xs), min(ys), max(xs), max(ys)]))
    return out


def log_path() -> Path:
    return config.DATA_DIR / "kraken_worker.log"


def _log_tail(n: int = 3) -> str:
    """Last lines of the worker's error output, to say why it failed."""
    try:
        lines = [l for l in log_path().read_text(errors="replace").splitlines() if l.strip()]
    except OSError:
        return ""
    return " | ".join(lines[-n:])[-600:]


class _Worker:
    def __init__(self) -> None:
        log_path().parent.mkdir(parents=True, exist_ok=True)
        self.err = open(log_path(), "w")  # the worker's stderr: import errors, tracebacks
        self.proc = subprocess.Popen([config.KRAKEN_PYTHON, str(WORKER)], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=self.err, text=True, bufsize=1)
        self.replies: queue.Queue = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()
        if not self._reply(START_TIMEOUT_S).get("ready"):
            raise RuntimeError("kraken worker did not start")

    def _read(self) -> None:
        for line in self.proc.stdout:  # type: ignore[union-attr]
            if line.startswith(MARK):
                self.replies.put(json.loads(line[len(MARK):]))
        self.replies.put({"error": "kraken worker exited"})

    def _reply(self, timeout: float) -> dict:
        try:
            return self.replies.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError("kraken worker did not answer in time") from None

    def segment(self, png: str) -> dict:
        self.proc.stdin.write(json.dumps({"path": str(Path(png).resolve())}) + "\n")  # type: ignore[union-attr]
        self.proc.stdin.flush()  # type: ignore[union-attr]
        return self._reply(PAGE_TIMEOUT_S)

    def close(self) -> None:
        try:
            self.proc.kill()
        except OSError:
            pass
        self.err.close()


_lock = threading.Lock()
_worker: _Worker | None = None


def segment(png: str) -> list[KLine] | None:
    """Kraken lines for one page image, or None if Kraken is unavailable or failed."""
    global _worker
    if not available():
        return None
    with _lock:
        for attempt in (1, 2):  # one restart if the worker died
            try:
                if _worker is None or _worker.proc.poll() is not None:
                    _worker = _Worker()
                result = _worker.segment(png)
                if "error" in result:
                    raise RuntimeError(result["error"])
                return to_lines(result)
            except Exception as exc:
                if _worker is not None:
                    _worker.close()
                _worker = None
                log.warning("kraken segmentation failed (attempt %d): %s. Worker log (%s): %s",
                            attempt, exc, log_path(), _log_tail() or "empty")
    return None
