"""Classical page analysis for the local OCR engines: deskew, CLAHE, text-line boxes, stamp blobs.

Line-level OCR models (Historic-Arabic-OCR) need one image per text line, and the rest of the
pipeline needs boxes for the side-by-side view and the stamp crops. No ML here, only OpenCV.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image


@dataclass
class LineBox:
    bbox: tuple[float, float, float, float]  # normalized on the ORIGINAL page image
    crop: Image.Image                        # deskewed, contrast-enhanced RGB crop for the OCR model


@dataclass
class StampBlob:
    bbox: tuple[float, float, float, float]
    overlaps_text: bool


@dataclass
class PageLayout:
    lines: list[LineBox]
    stamps: list[StampBlob]
    skew_degrees: float
    enhanced: Image.Image


def _estimate_skew(binary: np.ndarray, limit: float = 6.0) -> float:
    """Angle that maximizes the variance of the horizontal ink profile (text rows line up)."""
    small = cv2.resize(binary, None, fx=0.35, fy=0.35, interpolation=cv2.INTER_AREA)
    h, w = small.shape
    center = (w / 2, h / 2)
    best, best_score = 0.0, -1.0
    for angle in np.arange(-limit, limit + 0.01, 0.25):
        m = cv2.getRotationMatrix2D(center, angle, 1.0)
        rotated = cv2.warpAffine(small, m, (w, h), flags=cv2.INTER_NEAREST, borderValue=0)
        score = float(np.var(rotated.sum(axis=1)))
        if score > best_score:
            best, best_score = float(angle), score
    return best


def _remove_rules(binary: np.ndarray) -> np.ndarray:
    """Drop printed rules (ruled paper, form borders) and decorative page frames."""
    h, w = binary.shape
    horiz = cv2.morphologyEx(binary, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (max(40, w // 6), 1)))
    vert = cv2.morphologyEx(binary, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(40, h // 6))))
    out = cv2.subtract(binary, cv2.bitwise_or(horiz, vert))
    # Frames survive the opening when slightly skewed: huge but hollow components.
    n, labels, stats, _ = cv2.connectedComponentsWithStats(out)
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if (bw > 0.5 * w or bh > 0.5 * h) and area / max(1, bw * bh) < 0.06:
            out[labels == i] = 0
    return out


def _binarize(gray: np.ndarray) -> np.ndarray:
    """Ink mask. Local threshold for uneven lighting, gated by a global Otsu threshold so paper grain
    (which CLAHE would amplify) never counts as ink."""
    h, w = gray.shape
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    block = max(31, (w // 40) | 1)
    local = cv2.adaptiveThreshold(blur, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, block, 12)
    otsu, _ = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    dark = (blur < min(250, otsu + 15)).astype(np.uint8) * 255
    return cv2.bitwise_and(local, dark)


def _denoise(binary: np.ndarray) -> np.ndarray:
    """Remove paper grain and scanner speckle: median filter, then drop tiny components."""
    h, w = binary.shape
    out = cv2.medianBlur(binary, 3)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(out)
    min_area = max(10, int((w / 1600) ** 2 * 18))
    small = np.where(stats[1:, cv2.CC_STAT_AREA] < min_area)[0] + 1
    if len(small):
        out[np.isin(labels, small)] = 0
    return out



def _stamp_mask(bgr: np.ndarray) -> np.ndarray:
    """Coloured ink (blue, purple, red, green) as opposed to black/grey text and paper."""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    sat, val = hsv[..., 1], hsv[..., 2]
    b, r = bgr[..., 0].astype(np.int16), bgr[..., 2].astype(np.int16)
    # Faded blue/purple stamp ink on yellowed paper has low saturation but is clearly bluer than red.
    bluish = (b - r > 14) & (val < 235)
    vivid = (sat > 70) & (val > 40) & (val < 245)
    mask = (bluish | vivid).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
    return cv2.morphologyEx(mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15)))


def _ring_score(binary: np.ndarray, cx: float, cy: float, rad: float) -> float:
    """Share of the circle's perimeter that has ink nearby; real seals have an (almost) closed ring."""
    h, w = binary.shape
    hits = 0
    angles = np.linspace(0, 2 * np.pi, 72, endpoint=False)
    for a in angles:
        x, y = int(cx + rad * np.cos(a)), int(cy + rad * np.sin(a))
        if 0 <= x < w and 0 <= y < h and binary[max(0, y - 2):y + 3, max(0, x - 2):x + 3].any():
            hits += 1
    return hits / len(angles)


def _find_circles(gray: np.ndarray, binary: np.ndarray) -> list[tuple[int, int, int, int]]:
    """Round seals in black ink (court and notary stamps)."""
    h, w = gray.shape
    blur = cv2.medianBlur(gray, 5)
    circles = cv2.HoughCircles(blur, cv2.HOUGH_GRADIENT, dp=1.5, minDist=w // 8, param1=120, param2=60,
                               minRadius=int(0.035 * w), maxRadius=int(0.12 * w))
    out = []
    if circles is not None:
        for cx, cy, rad in circles[0][:8]:
            if _ring_score(binary, cx, cy, rad) >= 0.7:
                out.append((int(cx - rad), int(cy - rad), int(cx + rad), int(cy + rad)))
    return out


def _find_stamps(mask: np.ndarray) -> list[tuple[int, int, int, int]]:
    h, w = mask.shape
    n, _, stats, _ = cv2.connectedComponentsWithStats(mask)
    found = []
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if bw * bh < 0.004 * w * h or bw > 0.6 * w or bh > 0.5 * h:
            continue
        ratio = bw / max(1, bh)
        if 0.4 <= ratio <= 2.5 and area / (bw * bh) > 0.08:
            found.append((int(x), int(y), int(x + bw), int(y + bh)))
    return found



def _line_pitch(profile: np.ndarray, h: int) -> int:
    """Typical distance between text-line centres, from the row-ink autocorrelation."""
    x = profile - profile.mean()
    ac = np.correlate(x, x, mode="full")[len(x) - 1:]
    lo, hi = max(6, h // 150), max(12, h // 8)
    if hi >= len(ac) or ac[0] <= 0:
        return 0
    seg = ac[lo:hi]
    peaks = [i for i in range(1, len(seg) - 1)
             if seg[i] >= seg[i - 1] and seg[i] >= seg[i + 1] and seg[i] > 0.1 * ac[0]]
    return lo + peaks[0] if peaks else 0


def _valley_bands(profile: np.ndarray, pitch: int) -> list[tuple[int, int]]:
    """Cut between lines at profile minima at least ~0.6 pitch apart; works for dense handwriting."""
    k = max(3, pitch // 4)
    smooth = np.convolve(profile, np.ones(k) / k, mode="same")
    n = len(smooth)
    win = max(2, pitch // 2)
    cuts = []
    for i in np.argsort(smooth):
        local_max = smooth[max(0, i - win):min(n, i + win + 1)].max()
        if local_max <= 0 or smooth[i] > 0.55 * local_max:
            continue
        if all(abs(int(i) - c) >= 0.6 * pitch for c in cuts):
            cuts.append(int(i))
    cuts = sorted(set([0, *cuts, n - 1]))
    bands = []
    peak = float(np.percentile(smooth[smooth > 0], 95)) if smooth.any() else 0.0
    for a, b in zip(cuts, cuts[1:]):
        seg = profile[a:b + 1]
        if seg.size == 0 or seg.max() < 0.12 * peak:
            continue
        rows = np.where(seg > 0.1 * seg.max())[0]
        bands.append((a + int(rows.min()), a + int(rows.max())))
    return bands




def _text_line_boxes(text_bin: np.ndarray) -> list[tuple[int, int, int, int]]:
    """Run-length smearing: dilate ink horizontally so each text line becomes one blob, keep blobs that
    are wide and ink-dense, split blobs that hold several touching lines, then merge same-row pieces."""
    h, w = text_bin.shape
    kx = max(15, w // 45)
    smear = cv2.dilate(text_bin, cv2.getStructuringElement(cv2.MORPH_RECT, (kx, 3)))
    n, _, stats, _ = cv2.connectedComponentsWithStats(smear)
    comps = []
    for i in range(1, n):
        x, y, bw, bh, _ = stats[i]
        if bw < 0.05 * w or bh < 5:
            continue
        ink = int(np.count_nonzero(text_bin[y:y + bh, x:x + bw]))
        comps.append([int(x), int(y), int(x + bw), int(y + bh), ink / float(bw * bh)])
    if not comps:
        return []
    # Line height from the clearly textual blobs (wide ones), then drop speckle clusters: narrow,
    # sparse blobs, or blobs much shorter than a line.
    wide = [c for c in comps if c[2] - c[0] > 0.2 * w] or comps
    heights = sorted(c[3] - c[1] for c in wide)
    mh = heights[len(heights) // 2]
    # Touching lines make every blob tall; the row-ink period is then the better line height.
    pitch = _line_pitch(text_bin.sum(axis=1).astype(np.float32) / 255.0, h)
    if pitch and mh > 1.8 * pitch:
        mh = pitch
    comps = [c[:4] for c in comps
             if (c[2] - c[0] > 0.15 * w and c[4] > 0.05) or (c[4] > 0.12 and c[3] - c[1] > 0.45 * mh)]

    # Dense handwriting: one blob covers several lines. Cut it at row-ink minima ~one line apart.
    split = []
    for x0, y0, x1, y1 in comps:
        if y1 - y0 <= 1.8 * mh:
            split.append([x0, y0, x1, y1])
            continue
        profile = text_bin[y0:y1, x0:x1].sum(axis=1).astype(np.float32) / 255.0
        for a, b in _valley_bands(profile, max(8, mh)):
            cols = np.where(text_bin[y0 + a:y0 + b + 1, x0:x1].sum(axis=0) > 0)[0]
            if len(cols) and cols.max() - cols.min() > 0.04 * w:
                split.append([x0 + int(cols.min()), y0 + a, x0 + int(cols.max()), y0 + b])

    # Pieces on the same row (words separated by wide gaps, diacritic rows) join into one line.
    split.sort(key=lambda c: (c[1] + c[3]) / 2)
    merged: list[list[int]] = []
    for c in split:
        if merged:
            m = merged[-1]
            overlap = min(m[3], c[3]) - max(m[1], c[1])
            thin = min(m[3] - m[1], c[3] - c[1])
            if overlap > 0.35 * thin or (c[3] - c[1] < 0.4 * mh and c[1] - m[3] < 0.3 * mh):
                merged[-1] = [min(m[0], c[0]), min(m[1], c[1]), max(m[2], c[2]), max(m[3], c[3])]
                continue
        merged.append(c)
    return [tuple(m) for m in merged if m[3] - m[1] >= 0.35 * mh]


def _rotate_box_back(box: tuple[int, int, int, int], inv: np.ndarray, w: int, h: int) -> tuple[float, ...]:
    x0, y0, x1, y1 = box
    pts = np.array([[x0, y0, 1], [x1, y0, 1], [x0, y1, 1], [x1, y1, 1]], dtype=np.float64)
    mapped = pts @ inv.T
    xs, ys = mapped[:, 0], mapped[:, 1]
    return (
        round(float(max(0.0, xs.min() / w)), 4), round(float(max(0.0, ys.min() / h)), 4),
        round(float(min(1.0, xs.max() / w)), 4), round(float(min(1.0, ys.max() / h)), 4),
    )


def analyze(page_png: str) -> PageLayout:
    bgr = cv2.imread(page_png, cv2.IMREAD_COLOR)
    # Small phone photos (a few hundred pixels wide) are upscaled so strokes survive thresholding.
    if bgr.shape[1] < 1400:
        f = 1600 / bgr.shape[1]
        bgr = cv2.resize(bgr, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC)
    h, w = bgr.shape[:2]
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    binary = _binarize(gray)
    binary = _denoise(_remove_rules(binary))

    stamps_mask = _stamp_mask(bgr)
    stamp_boxes = _find_stamps(stamps_mask)
    for c in _find_circles(gray, binary):
        if all(min(c[2], s[2]) - max(c[0], s[0]) <= 0 or min(c[3], s[3]) - max(c[1], s[1]) <= 0 for s in stamp_boxes):
            stamp_boxes.append(c)

    angle = _estimate_skew(binary)
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    inv = cv2.invertAffineTransform(m)
    rot_bin = cv2.warpAffine(binary, m, (w, h), flags=cv2.INTER_NEAREST, borderValue=0)
    rot_enh = cv2.warpAffine(clahe, m, (w, h), flags=cv2.INTER_LINEAR, borderValue=255)
    # Stamps are segmented separately; keep them out of the text-line profile.
    rot_stamp = cv2.warpAffine(stamps_mask, m, (w, h), flags=cv2.INTER_NEAREST, borderValue=0)
    text_bin = cv2.bitwise_and(rot_bin, cv2.bitwise_not(rot_stamp))

    # Ignore a thin page border where scan edges create false ink.
    margin_x, margin_y = int(0.015 * w), int(0.01 * h)
    text_bin[:, :margin_x] = 0
    text_bin[:, w - margin_x:] = 0
    text_bin[:margin_y, :] = 0
    text_bin[h - margin_y:, :] = 0

    boxes = _text_line_boxes(text_bin)
    lines: list[LineBox] = []
    for x0, y0, x1, y1 in boxes:
        pad = max(4, (y1 - y0) // 5)
        y0, y1 = max(0, y0 - pad), min(h - 1, y1 + pad)
        x0, x1 = max(0, x0 - pad), min(w - 1, x1 + pad)
        crop = Image.fromarray(rot_enh[y0:y1 + 1, x0:x1 + 1]).convert("RGB")
        lines.append(LineBox(bbox=_rotate_box_back((x0, y0, x1, y1), inv, w, h), crop=crop))

    stamps = []
    for sx0, sy0, sx1, sy1 in stamp_boxes:
        nb = (round(sx0 / w, 4), round(sy0 / h, 4), round(sx1 / w, 4), round(sy1 / h, 4))
        overlaps = any(not (lb.bbox[2] < nb[0] or lb.bbox[0] > nb[2] or lb.bbox[3] < nb[1] or lb.bbox[1] > nb[3])
                       for lb in lines)
        stamps.append(StampBlob(bbox=nb, overlaps_text=bool(overlaps)))
    return PageLayout(lines=lines, stamps=stamps, skew_degrees=float(angle),
                      enhanced=Image.fromarray(rot_enh).convert("RGB"))

