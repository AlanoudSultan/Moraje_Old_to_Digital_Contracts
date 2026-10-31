"""Embeddings with a fake bag-of-words embedder: what is embedded, search ranking, JSONL export, pipeline hook."""
import json

import numpy as np

from backend.app import config, db, embeddings, graph
from backend.app.tools.arabic_text import normalize


class FakeEmbedder:
    """Deterministic, model-free: hashed bag of normalized words, unit length."""
    model_id = "fake-e5"
    dim = 64

    def encode(self, texts, kind):
        out = np.zeros((len(texts), self.dim), np.float32)
        for i, t in enumerate(texts):
            for w in normalize(t).split():
                out[i, hash(w) % self.dim] += 1.0
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        return out / np.where(norms == 0, 1, norms)


STATE = {
    "digital": {
        "clauses": [
            {"clause_id": "c1", "number_raw": "البند الأول", "heading": "المسؤولية", "pages": [1], "confidence": 0.8,
             "text": "لا تتجاوز المسؤولية الإجمالية قيمة العقد"},
            {"clause_id": "c2", "number_raw": "البند الثاني", "heading": "الدفع", "pages": [2], "confidence": 0.9,
             "text": "يسدد المبلغ خلال ثلاثين يوماً من الفاتورة"},
            {"clause_id": "c3", "number_raw": None, "heading": None, "pages": [2], "confidence": 0.2, "text": "[؟]"},
        ],
        "key_fields": [{"section": "البيانات الأساسية", "label": "رقم الصك", "value": "٣٣٠١٥٢٠"}],
    },
    "clause_types": [{"clause_id": "c1", "type": "liability"}, {"clause_id": "c2", "type": "payment"}],
}


def test_items_cover_clauses_and_key_fields_but_not_unreadable():
    items = embeddings.items_for(STATE)
    assert [(i["kind"], i["ref_id"]) for i in items] == [("clause", "c1"), ("clause", "c2"), ("key_field", "k1")]
    assert items[0]["text"].startswith("البند الأول المسؤولية:")
    assert items[0]["meta"]["clause_type"] == "liability" and items[0]["page"] == 1
    assert items[2]["text"] == "رقم الصك: ٣٣٠١٥٢٠"


def test_index_search_and_export(new_contract, monkeypatch, tmp_path):
    monkeypatch.setattr(embeddings, "get_embedder", lambda: FakeEmbedder())
    cid, _, _ = new_contract("clean_service_agreement.pdf")
    assert embeddings.index_contract(cid, STATE) == 3
    assert embeddings.index_contract(cid, STATE) == 3  # re-indexing replaces, never duplicates
    assert db.count_embeddings(cid) == 3

    hits = embeddings.search("المسؤولية الإجمالية", k=2)
    assert hits[0]["contract_id"] == cid and hits[0]["ref_id"] == "c1"
    assert hits[0]["score"] >= hits[1]["score"]

    out = embeddings.export_jsonl(cid, tmp_path / "e.jsonl")
    rows = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 3 and rows[0]["model"] == "fake-e5" and rows[0]["dim"] == 64
    assert len(rows[0]["vector"]) == 64 and abs(np.linalg.norm(rows[0]["vector"]) - 1) < 1e-3
    assert rows[0]["meta"]["clause_type"] == "liability"


def test_vectors_from_another_model_are_ignored(new_contract, monkeypatch):
    monkeypatch.setattr(embeddings, "get_embedder", lambda: FakeEmbedder())
    cid, _, _ = new_contract("clean_service_agreement.pdf")
    embeddings.index_contract(cid, STATE)

    class Other(FakeEmbedder):
        model_id = "other-model"

    monkeypatch.setattr(embeddings, "get_embedder", lambda: Other())
    assert [h for h in embeddings.search("المسؤولية") if h["contract_id"] == cid] == []


def test_pipeline_embeds_after_decision_and_survives_failure(new_contract, monkeypatch):
    monkeypatch.setattr(config, "EMBEDDINGS_ENABLED", True)
    monkeypatch.setattr(embeddings, "get_embedder", lambda: FakeEmbedder())
    cid, path, name = new_contract("clean_service_agreement.pdf")
    state = graph.run_pipeline(cid, path, name)
    assert state["decision"] == "approve"
    assert db.count_embeddings(cid) >= len(state["digital"]["clauses"]) - 1
    assert any(p["agent"] == "embed" and p["status"] == "done" for _, p in db.events_after(cid, 0))

    def broken():
        raise RuntimeError("no model")

    monkeypatch.setattr(embeddings, "get_embedder", broken)
    cid2, path2, name2 = new_contract("clean_service_agreement.pdf")
    state2 = graph.run_pipeline(cid2, path2, name2)
    assert state2["decision"] == "approve"  # embedding failure never changes the review
    assert any(p["agent"] == "embed" and p["status"] == "error" for _, p in db.events_after(cid2, 0))
