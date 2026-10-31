"""Agent 4: Clause typing. Text only."""
from __future__ import annotations

import json

from .. import mock_llm
from ..llm import call_structured, text_block
from ..state import ClauseOutput, ContractState


def clause_payload(digital: dict) -> list[dict]:
    return [{"clause_id": c["clause_id"], "number_raw": c.get("number_raw"), "heading": c.get("heading"),
             "text": c["text"]} for c in digital["clauses"]]


def run(state: ContractState) -> dict:
    clauses = clause_payload(state["digital"])
    content = [text_block("<clauses>\n" + json.dumps(clauses, ensure_ascii=False, indent=1) + "\n</clauses>\n"
                          "صنّف كل بند، واذكر الأنواع الأساسية المفقودة.")]
    out = call_structured("clause", "clause", content, ClauseOutput, contract_id=state["contract_id"],
                          mock=lambda: mock_llm.classify(clauses))
    known = {c["clause_id"] for c in clauses}
    typed = [t.model_dump() for t in out.clause_types if t.clause_id in known]
    # Any clause the model skipped is typed "other" so later agents still see it.
    seen = {t["clause_id"] for t in typed}
    typed += [{"clause_id": cid, "type": "other", "secondary_types": [], "confidence": 0.0}
              for cid in known - seen]
    return {"clause_types": typed, "missing_types": list(out.missing_types)}
