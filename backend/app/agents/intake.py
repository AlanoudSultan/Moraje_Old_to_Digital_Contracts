"""Agent 1: Intake. Scanned or digital, quality, doc type, readable."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageFilter, ImageStat

from .. import config, mock_llm
from ..llm import call_structured, image_block, text_block
from ..state import ContractState, IntakeLLM, IntakeResult
from ..tools.arabic_text import has_real_arabic_text

THUMB_SIDE = 1200


def _thumbnail(path: str) -> str:
    """Intake only triages, so a smaller image keeps the vision call cheap."""
    out = Path(path).with_name(Path(path).stem + "_thumb.png")
    if not out.exists():
        with Image.open(path) as im:
            im.thumbnail((THUMB_SIDE, THUMB_SIDE))
            im.save(out)
    return str(out)


def _sharpness(path: str) -> tuple[float, float]:
    """Rough 0..1 edge-energy score and brightness. Used only by mock mode."""
    with Image.open(path) as im:
        g = im.convert("L")
        g.thumbnail((900, 900))
        edges = ImageStat.Stat(g.filter(ImageFilter.FIND_EDGES)).stddev[0]
        return min(1.0, edges / 60.0), ImageStat.Stat(g).mean[0]


def run(state: ContractState) -> dict:
    pages = state["page_images"]
    texts = state.get("text_layer", [])
    file_type = Path(state["file_path"]).suffix.lower().lstrip(".")
    has_text = any(has_real_arabic_text(t) for t in texts)
    first = pages[: config.INTAKE_MAX_PAGES]

    content = [image_block(_thumbnail(p)) for p in first]
    hint = f"نوع الملف الأصلي: {file_type}. عدد الصفحات: {len(pages)}."
    if has_text:
        hint += "\nيوجد طبقة نصية عربية في الملف (مقتطف):\n" + texts[0][:1500]
    else:
        hint += "\nلا توجد طبقة نصية قابلة للاستخراج."
    content.append(text_block(hint))

    def mock():
        sharp, bright = _sharpness(first[0])
        return mock_llm.intake(file_type, has_text, len(pages), bright, sharp)

    llm = call_structured("intake", "intake", content, IntakeLLM, contract_id=state["contract_id"], mock=mock)
    result = IntakeResult(**llm.model_dump(), page_count=len(pages), has_text_layer=has_text, file_type=file_type)
    # Spec: a real Arabic text layer means born-digital, whatever the model guessed.
    if has_text and file_type in {"pdf", "docx"}:
        result.is_scanned = False
    if result.doc_type == "not_a_contract":
        result.readable = False
    return {"intake": result.model_dump()}
