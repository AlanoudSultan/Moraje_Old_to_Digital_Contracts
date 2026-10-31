"""Spend controls: template redlines and summary make no API call, the budget cap falls back to rules,
and unreadable OCR skips the (paid) legal review."""
import pytest

from backend.app import config, db, graph, llm
from backend.app.agents import decision as decision_agent
from backend.app.agents import redline as redline_agent
from backend.app.state import IntakeLLM
from backend.app.templates import redline_templates
from backend.app.tools import ocr_structure
from backend.app.tools.arabic_ocr import OcrText


def _no_api(*a, **k):
    raise AssertionError("Claude must not be called")


def test_every_rule_has_a_template():
    assert {r["id"] for r in config.load_rules()["rules"]} == set(redline_templates())


def test_template_redlines_and_summary_cost_nothing(new_contract, monkeypatch):
    cid, path, name = new_contract("risky_supply_contract.pdf")
    state = graph.run_pipeline(cid, path, name)  # mock agents up to here
    monkeypatch.setattr(config, "mock_mode", lambda: False)
    monkeypatch.setattr(redline_agent, "call_structured", _no_api)
    monkeypatch.setattr(decision_agent, "call_structured", _no_api)
    out = redline_agent.run(state)
    flagged = {r["rule_id"] for r in state["policy_results"] if r["status"] in ("fail", "missing")}
    assert {r["rule_id"] for r in out["redlines"]} == flagged
    liab = next(r for r in out["redlines"] if r["rule_id"] == "LIAB-01")
    assert liab["suggested_ar"] == redline_templates()["LIAB-01"]["suggested_ar"]
    assert "غير المحدودة" in liab["original_ar"]  # exact clause text kept for the strike-through
    assert out["redlines"][0]["severity"] == "critical"  # most severe first
    summary = decision_agent.run({**state, **out})["reviewer_summary_ar"]
    assert summary.startswith("سبب الإحالة") and "الإجراء المقترح" in summary


def test_budget_cap_falls_back_to_rules(monkeypatch):
    monkeypatch.setattr(config, "mock_mode", lambda: False)
    monkeypatch.setattr(config, "CLAUDE_BUDGET_USD", 0.0)
    monkeypatch.setattr(llm, "client", _no_api)
    fallback = {"is_scanned": False, "quality_score": 0.9, "doc_type": "lease", "language": "ar",
                "page_count_seen": 1, "readable": True, "issues_ar": []}
    out = llm.call_structured("intake", "intake", [llm.text_block("x")], IntakeLLM, mock=lambda: fallback)
    assert out.doc_type == "lease"
    with pytest.raises(llm.BudgetExceeded):
        llm.call_structured("structuring", "structuring", [llm.text_block("x")], IntakeLLM)


def test_spend_is_tracked():
    before = db.total_spend()
    db.add_usage("t-spend", "policy", "claude-sonnet-5-5", 1_000_000, 0, 0, 0)
    assert db.total_spend() == pytest.approx(before + 2.0)


class GarbleEngine:
    def lines(self, crops):
        return [OcrText("كلام مقروء بصعوبة شديدة في هذا السطر من الصفحة", 0.3) for _ in crops]


def test_unreadable_ocr_skips_legal_review(new_contract, monkeypatch):
    monkeypatch.setattr(config, "OCR_ENGINE", "historic")
    monkeypatch.setattr(ocr_structure, "get_engine", lambda name: GarbleEngine())
    cid, path, name = new_contract("old_scanned_lease.jpg")
    state = graph.run_pipeline(cid, path, name)
    assert "clause_types" not in state and "policy_results" not in state
    assert state["outputs"]["docx_path"]  # the digital copy is still produced
    assert state["decision"] == "escalate"
    assert any("جودة القراءة منخفضة جداً" in r for r in state["decision_reasons"])
