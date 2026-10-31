"""Page OCR with Google Gemini (default gemini-3.5-flash-lite, free tier; Gemma models are handled too) through the google-genai SDK.

One call per page: the model transcribes every line exactly, in reading order, with a legibility
flag, as JSON validated against a schema. The app's own segmentation is drawn on the copy sent to
Gemini (thin numbered boxes, numbers in a margin strip so they never cover ink), and Gemini answers
with the box number of each line, so every line lands on a known box. It returns no coordinates:
asking for a box per line made answers long and slow on the free tier. Gemini returns no per-character
probabilities here, so line confidence comes from its own legibility flag and from "[؟]" marks; it
is capped at OCR_CONFIDENCE_CAP like every OCR engine, so OCR alone never clears a contract.

Pages go to Google, and on the free tier Google uses the content to improve its products. Needs
GEMINI_API_KEY. Spend is logged with the other usage and capped separately by GEMINI_BUDGET_USD.
Free-tier rate limits (HTTP 429) are retried with backoff before giving up with a clear message.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

RETRY_WAITS = (5, 15, 30)  # seconds between attempts after a 429, 5xx or timeout
REQUEST_TIMEOUT_S = 120     # per attempt; the SDK waits forever by default
UPLOAD_MAX_SIDE = 2048      # longest side sent to Gemini (higher adds bytes, not legibility)
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from .. import config, db
from .arabic_ocr import _FOREIGN, clean

log = logging.getLogger("ocr")

SYSTEM = """You transcribe Arabic documents (contracts, court deeds, letters; printed or handwritten) for a legal review.
Rules:
1. Transcribe every text line exactly as written, in reading order: right to left, top to bottom. One entry per physical line.
2. Never correct spelling, grammar or wording. Never complete or guess. Keep Arabic-Indic digits (٠-٩) and punctuation as written.
3. Replace any word you cannot read with [؟]. Missing is better than wrong.
4. legibility: "clear" if you are sure of every character, "unsure" if some characters are doubtful, "unreadable" if mostly guessed.
5. Include text inside stamps and seals only if you can read it; mark it with legibility accordingly.
6. box: 0, unless the image shows numbered boxes (see below).
Return only JSON, no other text: {"lines": [{"box": <int>, "text": "<line>", "legibility": "clear" | "unsure" | "unreadable"}]}"""

BOX_RULES = """
The image has blue outlines drawn by an automatic line detector around each text line (they may be tilted or
curved to follow the writing), numbered in the white strip on the right edge (the number sits level with the
right end of its line, where the Arabic line starts). They are hints, not rules:
- For each box, transcribe the text inside it and put its number in `box`. Keep the order top to bottom.
- If one box holds two written lines, return two entries with the same box number.
- If a written line is not inside any box (missed by the detector), return it with box 0, in its place in reading order.
- Skip boxes with no text. Never transcribe the box numbers, the blue lines or the white strip.
- Read the real line of ink, even if a box is slightly off."""

PROMPT = "اقرأ هذه الصفحة سطراً سطراً كما هو مكتوب تماماً، دون تصحيح أو إكمال."

CONFIDENCE = {"clear": 0.88, "unsure": 0.55, "unreadable": 0.25}


class GeminiLine(BaseModel):
    box: int = Field(description="Number of the drawn box this line is in; 0 if it is in no box")
    text: str
    legibility: Literal["clear", "unsure", "unreadable"]


class GeminiPage(BaseModel):
    lines: list[GeminiLine]


@dataclass
class PageLine:
    text: str
    confidence: float
    box: int = 0  # 1-based segmentation box the model put this line in; 0 = none


class GeminiError(RuntimeError):
    """Message in Arabic, shown in the tracker as-is."""
    user_facing = True


def to_lines(page: GeminiPage) -> list[PageLine]:
    out = []
    for l in page.lines:
        text = clean(_FOREIGN.sub("", l.text))
        if not text:
            continue
        conf = CONFIDENCE[l.legibility]
        if "[؟]" in text:
            conf = min(conf, 0.5)
        out.append(PageLine(text=text, confidence=round(min(conf, config.OCR_CONFIDENCE_CAP), 3), box=max(0, l.box)))
    return out


def parse_page(text: str) -> GeminiPage:
    """The model's answer as a GeminiPage. Tolerates what models without a strict JSON mode (Gemma) add
    around it: code fences, reasoning before or after. Raises ValueError if no valid JSON is found."""
    import json
    import re

    text = (text or "").strip()
    try:
        return GeminiPage.model_validate_json(text)
    except ValueError:
        pass
    text = re.sub(r"```(?:json)?", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in the answer")
    data = json.loads(text[start:end + 1])
    return GeminiPage.model_validate(data)


def is_gemma(model: str) -> bool:
    """Gemma (open models served on the Gemini API): thinking is only "high" or "minimal", and the
    high-resolution and strict-JSON settings are not documented, so they are handled separately."""
    return model.startswith("gemma")


def thinking_level(model: str) -> str:
    if is_gemma(model):  # "minimal" keeps its reasoning out of the answer; "high" if asked for
        return "high" if config.GEMINI_THINKING == "high" else "minimal"
    return config.GEMINI_THINKING


def _transport_errors() -> tuple[type[BaseException], ...]:
    """The SDK can use httpx or httpx2; catch timeouts and connection drops from either."""
    errs: list[type[BaseException]] = [TimeoutError, ConnectionError]
    for mod in ("httpx", "httpx2"):
        try:
            errs.append(__import__(mod).TransportError)
        except (ImportError, AttributeError):
            pass
    return tuple(errs)


_TRANSPORT_ERRORS = _transport_errors()


def _upload_image(page_png: str):
    """The page in RGB with its longest side capped (higher adds bytes, not legibility)."""
    from PIL import Image

    with Image.open(page_png) as im:
        im = im.convert("RGB")
    if max(im.size) > UPLOAD_MAX_SIDE:
        f = UPLOAD_MAX_SIDE / max(im.size)
        im = im.resize((int(im.width * f), int(im.height * f)), Image.LANCZOS)
    return im


def _jpeg(im) -> bytes:
    import io

    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=92)
    return buf.getvalue()


def page_jpeg(page_png: str) -> tuple[bytes, tuple[int, int]]:
    """The page as a high-quality JPEG, longest side capped: about a tenth of the PNG's bytes.
    Returns the bytes and the (width, height) actually sent."""
    im = _upload_image(page_png)
    return _jpeg(im), im.size


BOX_COLOR = (0, 90, 255, 150)  # semi-transparent blue: visible, but the ink shows through


def _label_y(poly: list[list[float]]) -> float:
    """Height of a line's right end (where an Arabic line starts): its number goes level with it."""
    right = max(p[0] for p in poly)
    near = [p[1] for p in poly if p[0] >= right - 0.03] or [p[1] for p in poly]
    return sum(near) / len(near)


def annotated_jpeg(page_png: str, boxes: list[list[float]],
                   polygons: list[list[list[float]]] | None = None) -> tuple[bytes, tuple[int, int], "object"]:
    """The page with the segmentation drawn on it: a thin outline per line (its polygon when given, so a
    tilted line gets a tilted outline, else its box x0,y0,x1,y1; all normalized) and its 1-based number
    in a white strip added on the right, level with the line's start, so no number covers ink.
    Returns the JPEG bytes, its size and the image."""
    from PIL import Image, ImageDraw, ImageFont

    page = _upload_image(page_png)
    w, h = page.size
    font_px = max(14, min(26, h // 80))
    strip = font_px * 3
    canvas = Image.new("RGB", (w + strip, h), "white")
    canvas.paste(page, (0, 0))
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font = ImageFont.load_default(size=font_px)
    width = max(1, round(h / 1000))
    for i, (x0, y0, x1, y1) in enumerate(boxes, start=1):
        poly = polygons[i - 1] if polygons and i <= len(polygons) else None
        if poly:
            pts = [(round(x * w), round(y * h)) for x, y in poly]
            draw.line(pts + [pts[0]], fill=BOX_COLOR, width=width, joint="curve")
            cy = _label_y(poly) * h
        else:
            box = (round(x0 * w), round(y0 * h), round(x1 * w), round(y1 * h))
            draw.rectangle(box, outline=BOX_COLOR, width=width)
            cy = (box[1] + box[3]) / 2
        draw.text((w + strip / 2, cy), str(i), fill=(0, 70, 220, 255), font=font, anchor="mm")
    canvas = Image.alpha_composite(canvas.convert("RGBA"), overlay).convert("RGB")
    return _jpeg(canvas), canvas.size, canvas


class GeminiEngine:
    name = "Gemini"

    def __init__(self) -> None:
        if not config.GEMINI_API_KEY:
            raise GeminiError("لم يُضبط مفتاح GEMINI_API_KEY في ملف .env، فتعذرت قراءة الصفحة بنموذج Gemini")
        from google import genai

        from google.genai import types

        self.client = genai.Client(api_key=config.GEMINI_API_KEY,
                                   http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_S * 1000))  # ms
        self.lock = threading.Lock()

    def _call(self, errors, contents, make_cfg):
        """One request with recoveries: drop the high-resolution, thinking or strict-JSON setting if the
        model rejects it (400), and wait and retry when the free tier's rate limit is hit (429) or the
        service is temporarily unavailable or overloaded (5xx, e.g. 503)."""
        high_res, thinking, structured, waits = not is_gemma(config.GEMINI_MODEL), True, True, list(RETRY_WAITS)
        while True:
            try:
                return self.client.models.generate_content(model=config.GEMINI_MODEL, contents=contents,
                                                           config=make_cfg(high_res, thinking, structured))
            except errors.ClientError as exc:
                code = getattr(exc, "code", None)
                if code == 429 and waits:
                    wait = waits.pop(0)
                    log.warning("gemini rate limited, retrying in %ss", wait)
                    time.sleep(wait)
                    continue
                if code == 429:
                    raise GeminiError("تجاوزت حد الاستخدام المسموح في Gemini (الطبقة المجانية لها حد في الدقيقة "
                                      "واليوم). انتظر قليلاً ثم أعد المحاولة") from exc
                if code == 400 and high_res and "resolution" in str(exc).lower():
                    high_res = False
                    continue
                if code == 400 and thinking and "thinking" in str(exc).lower():
                    log.warning("gemini rejected thinking level %r, retrying without it", thinking_level(config.GEMINI_MODEL))
                    thinking = False
                    continue
                if code == 400 and structured and any(k in str(exc).lower() for k in ("schema", "json", "mime")):
                    log.warning("model rejected the JSON answer format, retrying with instructions only")
                    structured = False
                    continue
                if code == 404:  # e.g. "no longer available to new users"
                    raise GeminiError(f"النموذج {config.GEMINI_MODEL} غير متاح لهذا المفتاح. اختر نموذجاً آخر في "
                                      "GEMINI_MODEL ثم أعد تشغيل الخادم") from exc
                raise GeminiError(f"رفض Gemini الطلب ({code}): تحقق من المفتاح واسم النموذج") from exc
            except _TRANSPORT_ERRORS as exc:  # timeouts and dropped connections
                if waits:
                    wait = waits.pop(0)
                    log.warning("gemini request timed out or dropped (%s), retrying in %ss", type(exc).__name__, wait)
                    time.sleep(wait)
                    continue
                raise GeminiError("لم يرد Gemini في الوقت المحدد، أعد المحاولة بعد قليل") from exc
            except errors.APIError as exc:  # 5xx (ServerError) and others
                code = getattr(exc, "code", None)
                if isinstance(code, int) and code >= 500 and waits:
                    wait = waits.pop(0)
                    log.warning("gemini unavailable (%s), retrying in %ss", code, wait)
                    time.sleep(wait)
                    continue
                raise GeminiError(f"خدمة Gemini غير متاحة حالياً ({code})، أعد المحاولة بعد قليل") from exc

    def page(self, page_png: str, contract_id: str | None = None, boxes: list[list[float]] | None = None,
             polygons: list[list[list[float]]] | None = None) -> list[PageLine]:
        """Read one page. With `boxes` (the segmentation, normalized; `polygons` give their exact
        shapes), they are drawn on the copy sent and each returned line carries the number of the box
        it was read from."""
        from google.genai import errors, types

        spent = db.total_spend(prefix="gemini")
        if spent >= config.GEMINI_BUDGET_USD:
            raise GeminiError(f"نفدت ميزانية Gemini المحددة ({config.GEMINI_BUDGET_USD} دولار)")
        if boxes:
            data, size, im = annotated_jpeg(page_png, boxes, polygons)
            sent = Path(page_png).with_name(Path(page_png).stem + "_sent.jpg")
            try:
                im.save(sent, "JPEG", quality=92)  # kept next to the page, to see exactly what Gemini got
            except OSError:
                sent = None
        else:
            (data, size), sent = page_jpeg(page_png), None
        # Record exactly what went to Gemini, so the UI can show it and the log can prove it.
        self.last_sent = {"file": Path(page_png).name, "size": list(size), "kb": round(len(data) / 1024),
                          "enhanced": Path(page_png).stem.endswith("_enhanced"),
                          "boxes": len(boxes or []), "annotated_file": sent.name if sent else None}
        log.info("gemini: sending %s as %dx%d JPEG (%d KB)", Path(page_png).name, size[0], size[1], len(data) // 1024)
        image = types.Part.from_bytes(data=data, mime_type="image/jpeg")
        def make_cfg(high_res: bool, thinking: bool = True, structured: bool = True):
            return types.GenerateContentConfig(
                system_instruction=SYSTEM + (BOX_RULES if boxes else ""),
                response_mime_type="application/json" if structured else None,
                response_json_schema=GeminiPage.model_json_schema() if structured else None,
                thinking_config=types.ThinkingConfig(thinking_level=thinking_level(config.GEMINI_MODEL)) if thinking else None,
                media_resolution=types.MediaResolution.MEDIA_RESOLUTION_HIGH if high_res else None,
            )

        t0 = time.time()
        prompt = PROMPT
        if boxes:
            prompt += f"\n(الإطارات الزرقاء المرقّمة من ١ إلى {len(boxes)} أسطر حددها تحليل الصورة؛ اذكر رقم الإطار لكل سطر.)"
        resp = self._call(errors, [image, prompt], make_cfg)
        u = resp.usage_metadata
        if contract_id and u is not None:
            db.add_usage(contract_id, "digitize", config.GEMINI_MODEL,
                         input_tokens=u.prompt_token_count or 0,
                         output_tokens=(u.candidates_token_count or 0) + (u.thoughts_token_count or 0),
                         cache_read=u.cached_content_token_count or 0, cache_write=0)
        cand = (resp.candidates or [None])[0]
        reason = getattr(getattr(cand, "finish_reason", None), "value", None)
        if reason not in (None, "STOP"):
            raise GeminiError(f"لم يُكمل Gemini قراءة الصفحة (السبب: {reason})")
        try:
            page = parse_page(resp.text or "")
        except ValueError as exc:
            raise GeminiError("أعاد Gemini نتيجة غير صالحة لهذه الصفحة") from exc
        log.info("gemini page read in %.1fs, %d lines", time.time() - t0, len(page.lines))
        return to_lines(page)


_lock = threading.Lock()
_engine: GeminiEngine | None = None


def get_engine() -> GeminiEngine:
    global _engine
    with _lock:
        if _engine is None:
            _engine = GeminiEngine()
        return _engine
