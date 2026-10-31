"""FastAPI routes: upload, SSE progress, results, downloads, reviewer queue, rules."""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import uuid
from pathlib import Path
from typing import Literal

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, db, graph
from .tools import kraken_seg

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
app = FastAPI(title="Arabic Contract Review")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])

SAMPLES = {
    "clean": ("clean_service_agreement.pdf", "عقد خدمات تقنية (سليم)"),
    "risky": ("risky_supply_contract.pdf", "عقد توريد (مخاطر عالية)"),
    "scanned": ("old_scanned_lease.jpg", "عقد إيجار قديم ممسوح ضوئياً"),
}
MEDIA = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
         ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}  # our outputs


def _start(background: BackgroundTasks, file_name: str, write: callable) -> dict:
    contract_id = uuid.uuid4().hex[:12]
    folder = config.CONTRACTS_DIR / contract_id
    folder.mkdir(parents=True, exist_ok=True)
    # Keep the extension (it helps pick a reader); the renderer still sniffs the content.
    suffix = Path(file_name).suffix.lower()
    dest = folder / ("source" + (suffix if suffix.isascii() and 1 < len(suffix) <= 10 else ".bin"))
    write(dest)
    db.create_contract(contract_id, file_name, config.mock_mode())
    background.add_task(_run, contract_id, str(dest), file_name)
    return {"id": contract_id}


def _run(contract_id: str, path: str, file_name: str) -> None:
    try:
        graph.run_pipeline(contract_id, path, file_name)
    except Exception:
        pass  # already recorded and emitted by run_pipeline


@app.get("/api/health")
def health():
    return {"ok": True, "mock_mode": config.mock_mode(), "models": config.MODELS, "ocr_engine": config.OCR_ENGINE,
            "economy_mode": config.ECONOMY_MODE, "spent_usd": round(db.total_spend(exclude="gemini"), 4),
            "gemini_key_set": bool(config.GEMINI_API_KEY), "gemini_spent_usd": round(db.total_spend(prefix="gemini"), 4),
            "gemini_budget_usd": config.GEMINI_BUDGET_USD, "gemini_model": config.GEMINI_MODEL,
            "gemini_free_tier": config.GEMINI_FREE_TIER,
            "segmenter": "kraken" if kraken_seg.available() else "classic",
            "budget_usd": config.CLAUDE_BUDGET_USD}


@app.post("/api/contracts")
async def upload(background: BackgroundTasks, file: UploadFile = File(...)):
    name = Path(file.filename or "upload").name
    data = await file.read()
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(413, "حجم الملف يتجاوز ٢٠ ميجابايت")
    if not data:
        raise HTTPException(400, "الملف فارغ")
    return _start(background, name, lambda dest: dest.write_bytes(data))


@app.get("/api/samples")
def samples():
    return [{"key": k, "file_name": f, "label_ar": label} for k, (f, label) in SAMPLES.items()
            if (config.SAMPLES_DIR / f).exists()]


@app.post("/api/samples/{key}")
def run_sample(key: str, background: BackgroundTasks):
    if key not in SAMPLES:
        raise HTTPException(404, "العقد التجريبي غير موجود")
    file_name = SAMPLES[key][0]
    src = config.SAMPLES_DIR / file_name
    if not src.exists():
        raise HTTPException(404, "العقد التجريبي غير موجود")
    return _start(background, file_name, lambda dest: shutil.copyfile(src, dest))


@app.post("/api/contracts/{contract_id}/rerun")
def rerun(contract_id: str, background: BackgroundTasks):
    """Process the same uploaded file again as a new request (the old one stays as it was)."""
    row = db.get_contract(contract_id)
    if not row:
        raise HTTPException(404, "الطلب غير موجود")
    sources = sorted((config.CONTRACTS_DIR / contract_id).glob("source.*"))
    if not sources:
        raise HTTPException(404, "الملف الأصلي لهذا الطلب غير موجود")
    src = sources[0]
    return _start(background, row["file_name"], lambda dest: shutil.copyfile(src, dest))


@app.get("/api/contracts")
def list_all():
    return db.list_contracts()


@app.get("/api/contracts/{contract_id}/events")
async def events(contract_id: str, request: Request):
    if not db.get_contract(contract_id):
        raise HTTPException(404, "الطلب غير موجود")
    last = request.headers.get("last-event-id")
    after = int(last) if last and last.isdigit() else 0

    async def stream():
        nonlocal after
        idle = 0
        while True:
            if await request.is_disconnected():
                return
            batch = db.events_after(contract_id, after)
            for seq, payload in batch:
                after = seq
                yield f"id: {seq}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
            if batch:
                idle = 0
            else:
                idle += 1
                if idle % 30 == 0:
                    yield ": keep-alive\n\n"
            await asyncio.sleep(0.4)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _public_state(contract_id: str) -> dict:
    row = db.get_contract(contract_id)
    if not row:
        raise HTTPException(404, "الطلب غير موجود")
    state = graph.get_state(contract_id)
    state.pop("text_layer", None)
    state.pop("file_path", None)
    pages = state.pop("page_images", []) or []
    outputs = state.get("outputs") or {}
    return {
        "contract": row,
        "state": state,
        "page_count": len(pages),
        "downloads": {**{k: bool(outputs.get(f"{k}_path")) for k in ("docx", "pdf", "report")},
                      "embeddings": db.count_embeddings(contract_id) > 0},
        "usage": db.usage_summary(contract_id),
        "events": [p for _, p in db.events_after(contract_id, 0)],
    }


@app.get("/api/contracts/{contract_id}")
def get_contract(contract_id: str):
    return _public_state(contract_id)


class DeedEdit(BaseModel):
    key: str
    value: str | None = None
    status: Literal["edited", "skipped", "confirmed", "auto"]


def _deed_fields(contract_id: str) -> tuple[dict, list[dict]]:
    from . import deed

    if not db.get_contract(contract_id):
        raise HTTPException(404, "الطلب غير موجود")
    state = graph.get_state(contract_id)
    return state, deed.current_fields(contract_id, state.get("deed_fields") or [])


@app.get("/api/contracts/{contract_id}/deed")
def deed_fields(contract_id: str):
    """Template fields with confidence and the reviewer's edits applied; the full key list for adding."""
    from . import deed

    state, fields = _deed_fields(contract_id)
    return {"fields": fields, "keys": [{"key": k, "section": s, "label": l} for k, (s, l) in deed.KEYS.items()],
            "sections": [{"id": i, "label": l} for i, l in deed.SECTIONS],
            "extracted": "deed_fields" in state}


@app.put("/api/contracts/{contract_id}/deed")
def deed_edit(contract_id: str, body: DeedEdit):
    from . import deed

    _deed_fields(contract_id)
    if body.key not in deed.KEYS:
        raise HTTPException(400, "حقل غير معروف في نموذج الصك")
    db.set_deed_edit(contract_id, body.key, (body.value or "").strip() if body.status == "edited" else None, body.status)
    return {"ok": True}


@app.get("/api/contracts/{contract_id}/deed.html")
def deed_html(contract_id: str, download: bool = False):
    """The digital copy: the user's deed template filled with the current values."""
    from fastapi.responses import HTMLResponse

    from . import deed

    state, fields = _deed_fields(contract_id)
    body = deed.render_html(contract_id, fields, db.get_contract(contract_id)["file_name"])
    headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{contract_id}_deed.html"} if download else {}
    return HTMLResponse(body, headers=headers)


@app.get("/api/search")
def search(q: str, k: int = 10, contract_id: str | None = None):
    """Semantic search over the embedded clauses and key fields of all reviewed contracts."""
    from . import embeddings

    if not config.EMBEDDINGS_ENABLED:
        raise HTTPException(400, "التضمين متوقف في الإعدادات")
    return embeddings.search(q, k=max(1, min(k, 50)), contract_id=contract_id)


@app.get("/api/contracts/{contract_id}/download/{kind}")
def download(contract_id: str, kind: Literal["docx", "pdf", "report", "embeddings"]):
    if kind == "embeddings":
        from . import embeddings

        out = config.CONTRACTS_DIR / contract_id / "outputs" / "embeddings.jsonl"
        out.parent.mkdir(parents=True, exist_ok=True)
        if not embeddings.export_jsonl(contract_id, out):
            raise HTTPException(404, "لا توجد بيانات مضمّنة لهذا الطلب")
        return FileResponse(out, filename=f"{contract_id}_البيانات_المضمنة.jsonl", media_type="application/x-ndjson")
    state = graph.get_state(contract_id)
    path = (state.get("outputs") or {}).get(f"{kind}_path")
    if not path or not Path(path).exists():
        raise HTTPException(404, "الملف غير متوفر")
    names = {"docx": "النسخة_الرقمية.docx", "pdf": "النسخة_الرقمية.pdf", "report": "تقرير_المراجعة.docx"}
    return FileResponse(path, filename=f"{contract_id}_{names[kind]}", media_type=MEDIA[Path(path).suffix])


@app.get("/api/contracts/{contract_id}/pages/{n}")
def page_image(contract_id: str, n: int, variant: Literal["original", "enhanced"] = "original"):
    from .agents.digitization import enhanced_path

    state = graph.get_state(contract_id)
    pages = state.get("page_images") or []
    if n < 1 or n > len(pages):
        raise HTTPException(404, "الصفحة غير موجودة")
    path = pages[n - 1]
    if variant == "enhanced":
        path = enhanced_path(path)
        if not Path(path).exists():
            raise HTTPException(404, "لا توجد نسخة محسّنة لهذه الصفحة")
    return FileResponse(path, media_type="image/png")


@app.get("/api/contracts/{contract_id}/stamps/{stamp_id}")
def stamp_image(contract_id: str, stamp_id: str):
    path = config.CONTRACTS_DIR / contract_id / "outputs" / "stamps" / f"{Path(stamp_id).name}.png"
    if not path.exists():
        raise HTTPException(404, "غير موجود")
    return FileResponse(path, media_type="image/png")


@app.get("/api/review-queue")
def review_queue():
    items = []
    for row in db.list_contracts("awaiting_review"):
        state = graph.get_state(row["id"])
        items.append({**row, "decision_reasons": state.get("decision_reasons", []),
                      "reviewer_summary_ar": state.get("reviewer_summary_ar", "")})
    return items


class ReviewIn(BaseModel):
    decision: Literal["approve", "reject", "request_changes"]
    notes: str = ""
    reviewer: str = ""


@app.post("/api/contracts/{contract_id}/review")
def review(contract_id: str, body: ReviewIn, background: BackgroundTasks):
    row = db.get_contract(contract_id)
    if not row:
        raise HTTPException(404, "الطلب غير موجود")
    if row["status"] != "awaiting_review":
        raise HTTPException(409, "هذا الطلب ليس بانتظار المراجعة")
    db.update_contract(contract_id, status="reviewing")
    background.add_task(graph.resume_review, contract_id, body.model_dump())
    return {"ok": True}


@app.get("/api/rules")
def rules():
    return config.load_rules()


# Serve the built frontend when present (single-port deployment).
_dist = config.ROOT / "frontend" / "dist"
if _dist.exists():
    app.mount("/assets", StaticFiles(directory=_dist / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404)
        candidate = _dist / path
        if path and candidate.is_file():
            return FileResponse(candidate)
        # Always revalidate the page, so a rebuilt UI (new hashed assets) shows on the next refresh.
        return FileResponse(_dist / "index.html", headers={"Cache-Control": "no-cache"})
