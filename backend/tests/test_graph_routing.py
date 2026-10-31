"""Routing: approve path, policy escalate, OCR escalate, error escalate, unreadable escalate, resume."""
import pytest

from backend.app import db, graph, mock_llm
from backend.app.agents import policy as policy_agent


def run(new_contract, sample):
    cid, path, name = new_contract(sample)
    return cid, graph.run_pipeline(cid, path, name)


def test_clean_contract_auto_approves(new_contract):
    cid, state = run(new_contract, "clean_service_agreement.pdf")
    assert state["decision"] == "approve"
    assert state["decision_reasons"] == []
    assert state["risk_score"] <= 25
    assert db.get_contract(cid)["status"] == "approved"
    assert state["final_status"] == "approved"


def test_risky_contract_escalates_on_policy(new_contract):
    cid, state = run(new_contract, "risky_supply_contract.pdf")
    assert state["decision"] == "escalate"
    failed = {r["rule_id"] for r in state["policy_results"] if r["status"] in ("fail", "missing")}
    assert {"LIAB-01", "LAW-01", "DATA-01", "PAY-01"} <= failed
    assert state["risk_score"] > 25
    assert db.get_contract(cid)["status"] == "awaiting_review"


def test_scanned_lease_escalates_on_ocr(new_contract):
    _, state = run(new_contract, "old_scanned_lease.jpg")
    assert state["decision"] == "escalate"
    assert state["intake"]["is_scanned"] is True
    assert state["ocr_confidence"]["min"] < 0.85
    assert any("ثقة الرقمنة" in r for r in state["decision_reasons"])
    assert len(state["digital"]["stamps"]) == 2


def test_agent_error_escalates_never_approves(new_contract, monkeypatch):
    def boom(state):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(policy_agent, "run", boom)
    graph.get_graph.cache_clear()
    try:
        cid, state = run(new_contract, "clean_service_agreement.pdf")
    finally:
        graph.get_graph.cache_clear()
    assert state["errors"] and "simulated failure" in state["errors"][0]
    assert state["decision"] == "escalate"
    assert "redlines" not in state  # routed straight to decide
    events = [p for _, p in db.events_after(cid, 0)]
    assert any(e["agent"] == "policy" and e["status"] == "error" for e in events)


def test_unreadable_document_escalates(new_contract, monkeypatch):
    original = mock_llm.intake

    def unreadable(*a, **k):
        out = original(*a, **k)
        out.update(readable=False, quality_score=0.1)
        return out

    monkeypatch.setattr(mock_llm, "intake", unreadable)
    _, state = run(new_contract, "old_scanned_lease.jpg")
    assert state["decision"] == "escalate"
    assert "digital" not in state
    assert any("غير مقروء" in r for r in state["decision_reasons"])


@pytest.mark.parametrize("choice,status", [("approve", "approved_by_reviewer"), ("reject", "rejected"),
                                           ("request_changes", "changes_requested")])
def test_human_review_resumes_graph(new_contract, choice, status):
    cid, state = run(new_contract, "risky_supply_contract.pdf")
    assert state["decision"] == "escalate"
    final = graph.resume_review(cid, {"decision": choice, "notes": "ملاحظة", "reviewer": "سارة"})
    assert final["human_review"]["decision"] == choice
    assert final["final_status"] == status
    assert db.get_contract(cid)["status"] == status


def test_property_deed_is_digitized_but_not_policy_checked(new_contract, monkeypatch):
    import zipfile

    orig_intake, orig_page = mock_llm.intake, mock_llm.digitize_page

    def deed_intake(*a, **k):
        return {**orig_intake(*a, **k), "doc_type": "property_deed"}

    def deed_page(*a, **k):
        page = orig_page(*a, **k)
        page["key_fields"] = [
            {"section": "البيانات الأساسية", "label": "رقم الصك", "value": "٣٣٠١٥٢٠", "block_id": None,
             "confidence": 0.9},
            {"section": "الحدود والأطوال", "label": "شمالاً", "value": "شارع عرض ٢٠ م", "block_id": None,
             "confidence": 0.7},
        ]
        return page

    monkeypatch.setattr(mock_llm, "intake", deed_intake)
    monkeypatch.setattr(mock_llm, "digitize_page", deed_page)
    _, state = run(new_contract, "old_scanned_lease.jpg")
    assert state["decision"] == "escalate"
    assert "policy_results" not in state and "clause_types" not in state
    assert any("صك ملكية" in r for r in state["decision_reasons"])
    assert len(state["digital"]["key_fields"]) == 2
    xml = zipfile.ZipFile(state["outputs"]["docx_path"]).read("word/document.xml").decode("utf-8")
    assert "الحدود والأطوال" in xml and 'w:fill="1B8354"' in xml
