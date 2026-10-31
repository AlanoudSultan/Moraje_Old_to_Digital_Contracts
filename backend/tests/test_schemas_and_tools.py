"""Agent output schemas (mocked Claude), the docx RTL contract, Arabic helpers, and the LLM helper."""
import json
import zipfile
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from backend.app import config, llm, mock_llm
from backend.app.agents.decision import route
from backend.app.agents.digitization import merge, ocr_confidence
from backend.app.agents.redline import compute_risk
from backend.app.state import (ClauseOutput, DecisionSummary, IntakeLLM, PageDigitization, PolicyOutput,
                               RedlineOutput)
from backend.app.tools.arabic_text import normalize, parse_clause_number, parse_date
from backend.app.tools.docx_builder import build_docx


def truth_pages(name):
    data = json.loads((config.SAMPLES_DIR / name.replace(".pdf", ".truth.json").replace(".jpg", ".truth.json"))
                      .read_text(encoding="utf-8"))
    return [PageDigitization.model_validate(p) for p in data["pages"]]


def test_mock_outputs_validate_against_schemas():
    IntakeLLM.model_validate(mock_llm.intake("pdf", True, 2, 200, 0.9))
    digital = merge(truth_pages("risky_supply_contract.pdf")).model_dump()
    clauses = [{"clause_id": c["clause_id"], "heading": c["heading"], "text": c["text"]} for c in digital["clauses"]]
    typed = ClauseOutput.model_validate(mock_llm.classify(clauses))
    pol = PolicyOutput.model_validate(mock_llm.policy(clauses, [t.model_dump() for t in typed.clause_types], digital))
    flagged = [r.model_dump() for r in pol.policy_results if r.status in ("fail", "missing")]
    RedlineOutput.model_validate(mock_llm.redline(flagged, {c["clause_id"]: c for c in digital["clauses"]}))
    DecisionSummary.model_validate(mock_llm.summary(["سبب"], [], {"min": 0.9}, [], {}))


def test_schema_rejects_bad_output():
    with pytest.raises(ValidationError):
        IntakeLLM.model_validate({"is_scanned": True, "quality_score": 0.5, "doc_type": "pizza", "language": "ar",
                                  "page_count_seen": 1, "readable": True, "issues_ar": []})


def test_merge_builds_clauses_and_confidence():
    digital = merge(truth_pages("clean_service_agreement.pdf"))
    assert digital.title == "عقد تقديم خدمات تقنية"
    numbers = [c.number_raw for c in digital.clauses if c.number_raw]
    assert numbers[0] == "البند الأول" and len(numbers) == 12
    assert digital.clauses[0].heading == "التمهيد"  # preamble grouped into one clause
    assert all(c.clause_id == f"c{i}" for i, c in enumerate(digital.clauses, start=1))
    assert any(d.iso == "2026-01-01" for d in digital.dates)
    ocr = ocr_confidence(digital)
    assert 0.9 < ocr.min <= ocr.mean <= 1


def test_risk_score_formula():
    results = [
        {"rule_id": "LIAB-01", "status": "fail", "severity": "critical", "confidence": 0.9},
        {"rule_id": "PAY-01", "status": "fail", "severity": "high", "confidence": 0.5},  # halved
        {"rule_id": "FM-01", "status": "missing", "severity": "low", "confidence": 0.9},  # not required
        {"rule_id": "CONF-01", "status": "pass", "severity": "medium", "confidence": 0.9},
    ]
    score, breakdown = compute_risk(results)
    assert score == 30 + 8  # 7.5 rounds to 8
    assert [b["rule_id"] for b in breakdown] == ["LIAB-01", "PAY-01"]


def test_route_requires_every_condition():
    good = {"intake": {"readable": True}, "ocr_confidence": {"min": 0.95, "mean": 0.97}, "risk_score": 10,
            "policy_results": [], "errors": []}
    assert route(good) == ("approve", [])
    assert route({**good, "risk_score": 26})[0] == "escalate"
    assert route({**good, "ocr_confidence": {"min": 0.84, "mean": 0.97}})[0] == "escalate"
    assert route({**good, "errors": ["x"]})[0] == "escalate"
    crit = [{"rule_id": "LAW-01", "severity": "critical", "status": "fail"}]
    assert route({**good, "policy_results": crit})[0] == "escalate"


def test_docx_is_rtl(tmp_path):
    from PIL import Image

    page = tmp_path / "page_1.png"
    Image.new("RGB", (800, 1100), "white").save(page)
    digital = merge(truth_pages("old_scanned_lease.jpg")).model_dump()
    out, crops = build_docx(digital, [str(page)], "x.jpg", tmp_path, highlight_below=0.85)
    xml = zipfile.ZipFile(out).read("word/document.xml").decode("utf-8")
    assert xml.count("<w:bidi/>") >= 10
    assert "<w:rtl/>" in xml
    assert "<w:bidiVisual/>" in xml
    assert 'w:val="yellow"' in xml  # low-confidence highlight
    assert "[؟]" in xml  # unreadable marker kept as-is
    assert "ختم (صورة من الأصل)" in xml and "توقيع (صورة من الأصل)" in xml
    assert len(crops) == 2
    header = zipfile.ZipFile(out).read("word/header1.xml").decode("utf-8")
    assert "نسخة رقمية" in header


def test_arabic_helpers():
    assert parse_clause_number("البند الأول: موضوع العقد") == ("البند الأول", 1)
    assert parse_clause_number("المادة (٣) الدفع")[1] == 3
    assert parse_clause_number("أولاً: نص")[1] == 1
    assert parse_clause_number("١- يلتزم")[1] == 1
    assert normalize("الإيجـــار ١٢٣") == "الايجار 123"
    assert parse_date("١٢/٠٧/١٤٤٧هـ") == ("hijri", "2026-01-01")
    assert parse_date("٠١/٠١/٢٠٢٦م") == ("gregorian", "2026-01-01")
    assert mock_llm.extract_days("خلال مائة وعشرين يوماً") == [120]


def test_load_skill_strips_frontmatter():
    body = llm.load_skill("intake")
    assert not body.startswith("---") and body.startswith("# Intake Agent")


def test_call_structured_retries_then_falls_back(monkeypatch):
    """Validation error -> retry; refusal -> fallback model; then a parsed result."""
    monkeypatch.setattr(config, "mock_mode", lambda: False)
    calls = []
    responses = iter([
        ValidationError.from_exception_data("IntakeLLM", []),
        SimpleNamespace(stop_reason="refusal", parsed_output=None, usage=None),
        SimpleNamespace(stop_reason="end_turn", usage=None, parsed_output=IntakeLLM.model_validate(
            mock_llm.intake("pdf", True, 1, 200, 0.9))),
    ])

    def parse(**kwargs):
        calls.append(kwargs["model"])
        r = next(responses)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(llm, "client", lambda: SimpleNamespace(messages=SimpleNamespace(parse=parse)))
    out = llm.call_structured("intake", "intake", [llm.text_block("x")], IntakeLLM)
    assert out.readable is True
    assert calls == [config.MODELS["intake"], config.MODELS["intake"], config.FALLBACK_MODEL]


def test_counted_nouns_follow_arabic_agreement():
    from backend.app.tools.arabic_text import count_ar

    assert count_ar(1, "segment") == "مقطع واحد"
    assert count_ar(2, "line") == "سطران"
    assert count_ar(5, "segment") == "٥ مقاطع"
    assert count_ar(10, "page") == "١٠ صفحات"
    assert count_ar(11, "line") == "١١ سطراً"
    assert count_ar(33, "line") == "٣٣ سطراً"
    assert count_ar(100, "segment") == "١٠٠ مقطع"
    assert count_ar(105, "clause") == "١٠٥ بنود"
    assert count_ar(0, "stamp") == "٠ أختام"
