"""Embed each reviewed contract's clauses and key fields for later use (semantic search, export).

Local model (default intfloat/multilingual-e5-base, MIT, 768 dimensions, trained with Arabic):
contract text never leaves the machine and costs nothing. Vectors are L2-normalized and stored in
SQLite next to the text they came from, so they can be searched here or exported as JSONL.

e5 conventions: documents are embedded as "passage: ...", search text as "query: ...", mean
pooling over the last hidden state.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

import numpy as np

from . import config, db

log = logging.getLogger("embeddings")


class Embedder:
    max_length = 512
    batch_size = 16

    def __init__(self, model_id: str) -> None:
        import torch
        from huggingface_hub import snapshot_download
        from transformers import AutoModel, AutoTokenizer

        self.torch = torch
        self.model_id = model_id
        self.device = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
        t0 = time.time()
        path = snapshot_download(model_id, allow_patterns=["*.json", "model.safetensors", "sentencepiece.bpe.model",
                                                           "tokenizer*"])
        self.tok = AutoTokenizer.from_pretrained(path)
        self.model = AutoModel.from_pretrained(path, use_safetensors=True).to(self.device).eval()
        self.lock = threading.Lock()
        log.info("loaded %s on %s in %.1fs", model_id, self.device, time.time() - t0)

    def encode(self, texts: list[str], kind: str) -> np.ndarray:
        """kind: "passage" for stored text, "query" for search text. Returns (n, dim) float32, unit length."""
        torch = self.torch
        out = []
        for i in range(0, len(texts), self.batch_size):
            batch = [f"{kind}: {t}" for t in texts[i:i + self.batch_size]]
            enc = self.tok(batch, max_length=self.max_length, padding=True, truncation=True, return_tensors="pt")
            enc = {k: v.to(self.device) for k, v in enc.items()}
            with self.lock, torch.inference_mode():
                hidden = self.model(**enc).last_hidden_state
            mask = enc["attention_mask"].unsqueeze(-1).to(hidden.dtype)
            pooled = (hidden * mask).sum(1) / mask.sum(1).clamp(min=1e-9)
            out.append(torch.nn.functional.normalize(pooled, p=2, dim=1).float().cpu().numpy())
        return np.concatenate(out) if out else np.zeros((0, 0), np.float32)


_lock = threading.Lock()
_embedder: Embedder | None = None


def get_embedder() -> Embedder:
    global _embedder
    with _lock:
        if _embedder is None:
            _embedder = Embedder(config.EMBED_MODEL)
        return _embedder


def items_for(state: dict) -> list[dict]:
    """What gets embedded: every clause (with its number and heading) and every key field."""
    digital = state.get("digital") or {}
    typing = {t["clause_id"]: t["type"] for t in state.get("clause_types") or []}
    items = []
    for c in digital.get("clauses", []):
        label = " ".join(x for x in [c.get("number_raw"), c.get("heading")] if x)
        text = f"{label}: {c['text']}" if label else c["text"]
        if text.strip() and text.strip() != "[؟]":
            items.append({"kind": "clause", "ref_id": c["clause_id"], "page": (c.get("pages") or [None])[0],
                          "text": text, "meta": {"clause_type": typing.get(c["clause_id"]),
                                                 "confidence": c.get("confidence")}})
    for i, k in enumerate(digital.get("key_fields", []), start=1):
        items.append({"kind": "key_field", "ref_id": f"k{i}", "page": None,
                      "text": f"{k['label']}: {k['value']}", "meta": {"section": k.get("section")}})
    return items


def index_contract(contract_id: str, state: dict) -> int:
    """(Re)build this contract's vectors. Returns how many items were embedded."""
    items = items_for(state)
    db.delete_embeddings(contract_id)
    if not items:
        return 0
    emb = get_embedder()
    vectors = emb.encode([it["text"] for it in items], "passage")
    db.add_embeddings(contract_id, emb.model_id, [
        {**it, "vector": vectors[i].astype(np.float32).tobytes(), "dim": int(vectors.shape[1])}
        for i, it in enumerate(items)
    ])
    return len(items)


def search(query: str, k: int = 10, contract_id: str | None = None) -> list[dict]:
    rows = db.all_embeddings(contract_id)
    if not rows or not query.strip():
        return []
    emb = get_embedder()
    rows = [r for r in rows if r["model"] == emb.model_id]  # vectors from another model are not comparable
    if not rows:
        return []
    q = emb.encode([query], "query")[0]
    mat = np.stack([np.frombuffer(r["vector"], dtype=np.float32) for r in rows])
    scores = mat @ q
    order = np.argsort(-scores)[:k]
    return [{"contract_id": rows[i]["contract_id"], "kind": rows[i]["kind"], "ref_id": rows[i]["ref_id"],
             "page": rows[i]["page"], "text": rows[i]["text"], "score": round(float(scores[i]), 4),
             "file_name": rows[i]["file_name"], "title": rows[i]["title"]} for i in order]


def export_jsonl(contract_id: str, out_path: Path) -> Path | None:
    """One JSON object per embedded item: text, location, metadata, model and the vector itself."""
    rows = db.all_embeddings(contract_id)
    if not rows:
        return None
    with out_path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps({
                "contract_id": r["contract_id"], "kind": r["kind"], "ref_id": r["ref_id"], "page": r["page"],
                "text": r["text"], "meta": json.loads(r["meta"] or "{}"), "model": r["model"], "dim": r["dim"],
                "vector": np.frombuffer(r["vector"], dtype=np.float32).round(6).tolist(),
            }, ensure_ascii=False) + "\n")
    return out_path
