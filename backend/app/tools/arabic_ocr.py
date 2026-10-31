"""Local Arabic OCR engines, loaded once and run on CPU, CUDA, or Apple MPS.


- historic: Qwen2-VL-2B-Instruct + Historic-Arabic-OCR LoRA (Shaza2004). Line-level; trained on
  historic manuscripts with CLAHE and greedy decoding (NakbaNLP 2026).
- qari: Qari-OCR v0.3 (NAMAA-Space), merged weights, Apache-2.0. Page-level; printed text only.

Confidence comes from the model's own token probabilities, which is more honest than a
self-reported score. These models were not trained on Saudi court script, so the result is
capped (config.OCR_CONFIDENCE_CAP) and such documents never auto-approve on OCR alone.
"""
from __future__ import annotations

import logging
import math
import re
import threading
import time
from dataclasses import dataclass

from PIL import Image

from .. import config

log = logging.getLogger("ocr")

HISTORIC_PROMPT = "Return the plain text in this image. Do not add anything"
QARI_PROMPT = ("Below is the image of one page of a document, as well as some raw textual content that was "
               "previously extracted for it. Just return the plain text representation of this document as if "
               "you were reading it naturally. Do not hallucinate.")

_LATIN = re.compile(r"[A-Za-z]+")
# Anything that is not Arabic script, digits, whitespace, or common punctuation (e.g. CJK or Thai that a
# multilingual base model sometimes emits on hard crops).
_FOREIGN = re.compile(r"[^\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF"
                      r"0-9A-Za-z\s.,:;()\[\]{}\-/\\«»\"'!?%=+…–—_*#&<>|~]")


@dataclass
class OcrText:
    text: str
    confidence: float


def clean(text: str) -> str:
    """Paper's post-processing: drop stray Latin letters, collapse whitespace. Digits and Arabic stay."""
    text = _LATIN.sub("", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def _degenerate(text: str) -> bool:
    """Generation loops ('و و و و ...') are a known VLM failure; treat them as unreadable."""
    words = text.split()
    if len(words) >= 8:
        top = max(words.count(w) for w in set(words))
        if top / len(words) > 0.5:
            return True
    return bool(re.search(r"(.{2,12}?)\1{5,}", text))


def token_confidence(probs: list[float]) -> float:
    """Blend of geometric-mean and minimum token probability, so one doubtful character still shows."""
    if not probs:
        return 0.0
    geo = math.exp(sum(math.log(max(p, 1e-9)) for p in probs) / len(probs))
    return 0.6 * geo + 0.4 * min(probs)


class _Engine:
    name = ""
    model_id = ""

    def __init__(self) -> None:
        import torch
        from transformers import AutoProcessor

        self.torch = torch
        half = torch.bfloat16 if self.prefer_bf16 else torch.float16
        if torch.cuda.is_available():
            self.device, dtype = "cuda", half
        elif torch.backends.mps.is_available():
            self.device, dtype = "mps", half
        else:
            self.device, dtype = "cpu", torch.float32
        t0 = time.time()
        self.processor = AutoProcessor.from_pretrained(
            self.processor_id(), min_pixels=self.min_pixels, max_pixels=self.max_pixels)
        self.processor.tokenizer.padding_side = "left"
        model = self.model_class().from_pretrained(self.base_id(), dtype=dtype)
        self.model = self.adapt(model).to(self.device).eval()
        self.lock = threading.Lock()  # one generation at a time per device
        log.info("loaded %s on %s in %.1fs", self.name, self.device, time.time() - t0)

    min_pixels = 32 * 28 * 28
    max_pixels = 1024 * 28 * 28
    max_new_tokens = 128
    prefer_bf16 = False
    gen_kwargs: dict = {}

    def model_class(self):
        from transformers import Qwen2VLForConditionalGeneration
        return Qwen2VLForConditionalGeneration

    def base_id(self) -> str:
        return self.model_id

    def processor_id(self) -> str:
        return self.base_id()

    def adapt(self, model):
        return model

    def _finish(self, ids: list[int], probs: list[float]) -> OcrText:
        raw = self.processor.tokenizer.decode(ids, skip_special_tokens=True)
        foreign = len(_FOREIGN.findall(raw))
        text = clean(_FOREIGN.sub("", raw))
        conf = token_confidence(probs)
        if foreign:  # a script switch mid-line is a hallucination signal
            conf = min(conf, 0.3)
        if not text or _degenerate(text):
            text, conf = "[؟]", min(conf, 0.3)
        return OcrText(text=text, confidence=round(min(conf, config.OCR_CONFIDENCE_CAP), 3))

    def _generate(self, images: list[Image.Image], prompt: str, split_lines: bool = False) -> list[list[OcrText]]:
        """One list of OcrText per image: a single item, or one per output line when split_lines."""
        torch = self.torch
        messages = [[{"role": "user", "content": [{"type": "image"}, {"type": "text", "text": prompt}]}] for _ in images]
        texts = [self.processor.apply_chat_template(m, tokenize=False, add_generation_prompt=True) for m in messages]
        inputs = self.processor(text=texts, images=images, return_tensors="pt", padding=True).to(self.device)
        with self.lock, torch.inference_mode():
            out = self.model.generate(
                **inputs, max_new_tokens=self.max_new_tokens, do_sample=False, num_beams=1,
                output_scores=True, return_dict_in_generate=True, **self.gen_kwargs,
            )
        start = inputs["input_ids"].shape[1]
        seqs = out.sequences[:, start:]
        # Probability of each chosen token, from the per-step logits.
        step_probs = [torch.softmax(s.float(), dim=-1) for s in out.scores]
        special = set(self.processor.tokenizer.all_special_ids)
        tok = self.processor.tokenizer
        stop = {tok.eos_token_id, tok.pad_token_id}
        results = []
        for i in range(seqs.shape[0]):
            segments, ids, probs = [], [], []
            for t, tid in enumerate(seqs[i].tolist()):
                if tid in special:
                    if tid in stop:
                        break
                    continue
                piece = tok.decode([tid])
                if split_lines and "\n" in piece:
                    if ids:
                        segments.append(self._finish(ids, probs))
                    ids, probs = [], []
                    continue
                ids.append(tid)
                probs.append(float(step_probs[t][i, tid]))
            if ids or not segments:
                segments.append(self._finish(ids, probs))
            results.append([s for s in segments if s.text != "[؟]" or not split_lines] or segments)
        return results

    def page_lines(self, page: Image.Image) -> list[OcrText]:
        raise NotImplementedError

    def lines(self, crops: list[Image.Image]) -> list[OcrText]:
        raise NotImplementedError


class HistoricEngine(_Engine):
    """Line-level: one crop per text line, batched."""
    name = "Historic-Arabic-OCR"
    adapter_id = "Shaza2004/Historic-Arabic-OCR"
    model_id = "Qwen/Qwen2-VL-2B-Instruct"
    max_pixels = 512 * 28 * 28
    batch_size = 4

    def adapt(self, model):
        from peft import PeftModel

        # The adapter was trained on a 4-bit copy of the same base; full precision weights share module
        # names, and bitsandbytes is not available on Apple Silicon.
        model = PeftModel.from_pretrained(model, self.adapter_id)
        return model.merge_and_unload()

    def lines(self, crops: list[Image.Image]) -> list[OcrText]:
        out: list[OcrText] = []
        for i in range(0, len(crops), self.batch_size):
            out.extend(r[0] for r in self._generate(crops[i:i + self.batch_size], HISTORIC_PROMPT))
        return out


class QariEngine(_Engine):
    """Page-level: one call per page, output split into lines."""
    name = "Qari-OCR v0.3"
    model_id = "NAMAA-Space/Qari-OCR-v0.3-VL-2B-Instruct"
    max_pixels = 1600 * 28 * 28
    max_new_tokens = 2000

    def page_lines(self, page: Image.Image) -> list[OcrText]:
        return self._generate([page], QARI_PROMPT, split_lines=True)[0]


ENGINES = {"historic": HistoricEngine, "qari": QariEngine}
# Engines that read one segmented line at a time (the rest read whole pages).
LINE_ENGINES = {"historic"}


_load_lock = threading.Lock()
_engines: dict[str, _Engine] = {}


def get_engine(name: str) -> _Engine:
    """Load once per process; digitization threads share the instance."""
    if name not in ENGINES:
        raise ValueError(f"unknown OCR engine: {name}")
    with _load_lock:
        if name not in _engines:
            _engines[name] = ENGINES[name]()
        return _engines[name]
