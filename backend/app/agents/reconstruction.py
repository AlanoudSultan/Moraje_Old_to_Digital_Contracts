"""Agent 3: Reconstruction. Plain code, no LLM: RTL .docx plus a .pdf copy."""
from __future__ import annotations

from pathlib import Path

from .. import config
from ..state import ContractState, Outputs
from ..tools.docx_builder import build_docx, build_pdf_fallback, docx_to_pdf_libreoffice


def run(state: ContractState) -> dict:
    out_dir = Path(state["file_path"]).parent / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    highlight = config.thresholds()["block_highlight_below"]
    docx_path, crops = build_docx(state["digital"], state["page_images"], state["file_name"], out_dir, highlight)
    warnings = []
    pdf_path = docx_to_pdf_libreoffice(docx_path)
    if pdf_path is None:
        warnings.append("LibreOffice غير متوفر، تم إنشاء نسخة PDF بمحرك بديل")
        try:
            pdf_path = build_pdf_fallback(state["digital"], crops, state["page_images"], state["file_name"],
                                          out_dir, highlight)
        except Exception as exc:  # the PDF copy is optional; never fail the pipeline for it
            warnings.append(f"تعذر إنشاء نسخة PDF: {type(exc).__name__}")
            pdf_path = None
    outputs = Outputs(docx_path=str(docx_path), pdf_path=str(pdf_path) if pdf_path else None, warnings=warnings)
    return {"outputs": outputs.model_dump()}
