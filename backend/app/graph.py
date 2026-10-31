"""LangGraph StateGraph: digitize -> review -> route, with a human-review interrupt.

START -> intake -> digitize -> reconstruct -> clause -> policy -> redline -> decide
intake -> decide when unreadable; any node error -> decide (which must escalate)
decide -> finalize (approve) | human_review -> finalize (escalate) -> END
"""
from __future__ import annotations

import logging
import sqlite3
import traceback
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Callable

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from . import config, db
from .agents import clause, decision, digitization, intake, policy, reconstruction, redline
from .events import emit
from .state import NON_CONTRACT_DOC_TYPES, ContractState, HumanReview
from .tools import pdf_render
from .tools.arabic_text import count_ar, to_arabic_digits
from .tools.report_builder import build_report

log = logging.getLogger("graph")

START_MSG = {
    "intake": "جارٍ فحص المستند وتحديد نوعه وجودته",
    "digitize": "جارٍ قراءة الصفحات واستخراج البنود",
    "reconstruct": "جارٍ إنشاء النسخة الرقمية (Word و PDF)",
    "clause": "جارٍ تصنيف البنود",
    "policy": "جارٍ مطابقة البنود مع سياسات الجهة",
    "redline": "جارٍ حساب المخاطر واقتراح الصياغات البديلة",
    "decide": "جارٍ تحديد مسار العقد",
    "deed": "جارٍ تعبئة نموذج الصك من النص المقروء",
}


def _fmt(x: float) -> str:
    return to_arabic_digits(f"{x:.2f}")


def _done_msg(name: str, update: dict, state: ContractState) -> tuple[str, dict]:
    if name == "intake":
        i = update["intake"]
        kind = "ممسوح ضوئياً" if i["is_scanned"] else "رقمي"
        msg = f"مستند {kind}، {count_ar(i['page_count'], 'page')}، جودة {_fmt(i['quality_score'])}"
        if not i["readable"]:
            msg += "، غير قابل للرقمنة"
        return msg, {"intake": i}
    if name == "digitize":
        d, o = update["digital"], update["ocr_confidence"]
        return (f"تم استخراج {count_ar(len(d['clauses']), 'clause')} و{count_ar(len(d['stamps']), 'stamp')} "
                f"ختم/توقيع بنموذج {d.get('ocr_engine') or '-'}، أدنى ثقة {_fmt(o['min'])}"), {"ocr_confidence": o}
    if name == "reconstruct":
        o = update["outputs"]
        return ("تم إنشاء النسخة الرقمية" + (" (Word و PDF)" if o.get("pdf_path") else " (Word)")), {
            "warnings": o.get("warnings", [])}
    if name == "clause":
        missing = update.get("missing_types", [])
        return (f"تم تصنيف {count_ar(len(update['clause_types']), 'clause')}"
                + (f"، أنواع مفقودة: {to_arabic_digits(len(missing))}" if missing else "")), {"missing_types": missing}
    if name == "policy":
        rs = update["policy_results"]
        fails = sum(r["status"] == "fail" for r in rs)
        miss = sum(r["status"] == "missing" for r in rs)
        passed = sum(r["status"] == "pass" for r in rs)
        return (f"مطابق {to_arabic_digits(passed)}، مخالف {to_arabic_digits(fails)}، مفقود {to_arabic_digits(miss)}"), {}
    if name == "redline":
        return (f"درجة المخاطر {to_arabic_digits(update['risk_score'])} من ١٠٠، "
                f"{count_ar(len(update['redlines']), 'redline')}"), {"risk_score": update["risk_score"]}
    if name == "deed":
        fields = update["deed_fields"]
        th = config.thresholds()["block_highlight_below"]
        low = sum(1 for f in fields if f["confidence"] < th)
        msg = f"تمت تعبئة {count_ar(len(fields), 'field')} من نموذج الصك"
        if low:
            msg += f"، منها {count_ar(low, 'low_field')} تحتاج تحققاً"
        return msg, {"count": len(fields), "low": low}
    if name == "decide":
        if update["decision"] == "approve":
            return "معتمد تلقائياً", {"decision": "approve"}
        return "محال للمراجع البشري", {"decision": "escalate", "reasons": update["decision_reasons"]}
    return "تم", {}


def agent_node(name: str, fn: Callable[[ContractState], dict]) -> Callable[[ContractState], dict]:
    """Emit started/done/error events. On exception, log to `errors`; routing then goes to `decide`."""

    def node(state: ContractState) -> dict:
        cid = state["contract_id"]
        emit(cid, name, "started", START_MSG.get(name, ""))
        try:
            update = fn(state)
        except Exception as exc:
            log.error("contract=%s node=%s failed: %s", cid, name, traceback.format_exc(limit=3))
            message = f"{name}: {type(exc).__name__}: {str(exc)[:300]}"
            # Errors written for the user (unreadable file, missing key) are shown as they are.
            shown = str(exc) if getattr(exc, "user_facing", False) else "تعذر إكمال هذه الخطوة، وسيحال العقد للمراجع"
            emit(cid, name, "error", shown, {"error": message})
            return {"errors": [*state.get("errors", []), message]}
        msg, data = _done_msg(name, update, {**state, **update})
        emit(cid, name, "done", msg, data)
        return update

    return node


def _intake(state: ContractState) -> dict:
    update: dict = {}
    if not state.get("page_images"):
        pages_dir = str(Path(state["file_path"]).parent / "pages")
        images, texts, _ = pdf_render.render(state["file_path"], pages_dir)
        update = {"page_images": images, "text_layer": texts}
    return {**update, **intake.run({**state, **update})}


def _decide(state: ContractState) -> dict:
    update = decision.run(state)
    cid = state["contract_id"]
    merged = {**state, **update}
    db.update_contract(
        cid, decision=update["decision"], risk_score=merged.get("risk_score"),
        ocr_min=(merged.get("ocr_confidence") or {}).get("min"),
        doc_type=(merged.get("intake") or {}).get("doc_type"),
        title=(merged.get("digital") or {}).get("title"),
        status="approved" if update["decision"] == "approve" else "awaiting_review",
    )
    _embed(cid, merged)
    return update


def _embed(cid: str, state: dict) -> None:
    """Index clauses and key fields for later search and export. Optional: never blocks the review."""
    if not config.EMBEDDINGS_ENABLED or not (state.get("digital") or {}).get("clauses"):
        return
    from . import embeddings

    emit(cid, "embed", "started", "جارٍ تضمين البنود والبيانات للبحث لاحقاً")
    try:
        n = embeddings.index_contract(cid, state)
    except Exception as exc:
        log.error("embedding failed for %s: %s", cid, exc)
        emit(cid, "embed", "error", "تعذر تضمين البيانات، ولم يؤثر ذلك على المراجعة", {"error": str(exc)[:300]})
        return
    emit(cid, "embed", "done", f"تم تضمين {count_ar(n, 'segment')} بنموذج {config.EMBED_MODEL.split('/')[-1]}",
         {"count": n, "model": config.EMBED_MODEL})


def human_review_node(state: ContractState) -> dict:
    # Runs again from the top on resume, so no side effects before interrupt().
    answer = interrupt({"contract_id": state["contract_id"], "reviewer_summary_ar": state.get("reviewer_summary_ar"),
                        "decision_reasons": state.get("decision_reasons", [])})
    review = HumanReview(
        decision=answer["decision"], notes=answer.get("notes", ""), reviewer=answer.get("reviewer") or "المراجع",
        timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    labels = {"approve": "اعتمد المراجع العقد", "reject": "رفض المراجع العقد",
              "request_changes": "طلب المراجع تعديلات"}
    emit(state["contract_id"], "human_review", "done", labels[review.decision], {"human_review": review.model_dump()})
    return {"human_review": review.model_dump()}


def finalize_node(state: ContractState) -> dict:
    cid = state["contract_id"]
    hr = state.get("human_review")
    if hr:
        status = {"approve": "approved_by_reviewer", "reject": "rejected",
                  "request_changes": "changes_requested"}[hr["decision"]]
    else:
        status = "approved" if state.get("decision") == "approve" else "awaiting_review"
    outputs = dict(state.get("outputs") or {})
    out_dir = Path(state["file_path"]).parent / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        outputs["report_path"] = str(build_report(dict(state), out_dir))
    except Exception as exc:
        log.error("report failed for %s: %s", cid, exc)
    db.update_contract(cid, status=status)
    emit(cid, "pipeline", "done", "اكتملت معالجة الطلب", {"status": status})
    return {"final_status": status, "outputs": outputs}


def _after(next_node: str) -> Callable[[ContractState], str]:
    def route(state: ContractState) -> str:
        return "decide" if state.get("errors") else next_node
    return route


def _deed(state: ContractState) -> dict:
    from . import deed

    return {"deed_fields": deed.extract(dict(state))}


def _after_reconstruct(state: ContractState) -> str:
    """Deeds and court records go to the deed template instead of contract rules. Text the OCR could
    barely read is not reviewed either: findings on hallucinated text would mislead and cost money."""
    if state.get("errors"):
        return "decide"
    if (state.get("intake") or {}).get("doc_type") in NON_CONTRACT_DOC_TYPES:
        return "deed"
    if (state.get("ocr_confidence") or {}).get("mean", 1.0) < config.REVIEW_MIN_OCR_MEAN:
        return "decide"
    return "clause"


def _after_intake(state: ContractState) -> str:
    if state.get("errors") or not (state.get("intake") or {}).get("readable"):
        return "decide"
    return "digitize"


def _escalation_report(state: ContractState) -> dict:
    """Write the report as soon as a contract is escalated so the reviewer can download it."""
    out_dir = Path(state["file_path"]).parent / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    outputs = dict(state.get("outputs") or {})
    try:
        outputs["report_path"] = str(build_report(dict(state), out_dir))
    except Exception as exc:
        log.error("report failed: %s", exc)
    emit(state["contract_id"], "human_review", "waiting", "بانتظار قرار المراجع البشري",
         {"reasons": state.get("decision_reasons", [])})
    return {"outputs": outputs}


def build_graph(checkpointer=None):
    g = StateGraph(ContractState)
    g.add_node("intake", agent_node("intake", _intake))
    g.add_node("digitize", agent_node("digitize", digitization.run))
    g.add_node("reconstruct", agent_node("reconstruct", reconstruction.run))
    g.add_node("deed", agent_node("deed", _deed))
    g.add_node("clause", agent_node("clause", clause.run))
    g.add_node("policy", agent_node("policy", policy.run))
    g.add_node("redline", agent_node("redline", redline.run))
    g.add_node("decide", agent_node("decide", _decide))
    g.add_node("notify_reviewer", _escalation_report)
    g.add_node("human_review", human_review_node)
    g.add_node("finalize", finalize_node)

    g.add_edge(START, "intake")
    g.add_conditional_edges("intake", _after_intake, ["digitize", "decide"])
    g.add_conditional_edges("digitize", _after("reconstruct"), ["reconstruct", "decide"])
    g.add_conditional_edges("reconstruct", _after_reconstruct, ["clause", "deed", "decide"])
    g.add_edge("deed", "decide")
    g.add_conditional_edges("clause", _after("policy"), ["policy", "decide"])
    g.add_conditional_edges("policy", _after("redline"), ["redline", "decide"])
    g.add_conditional_edges("redline", _after("decide"), ["decide"])
    g.add_conditional_edges("decide", _after_decide_with_notify, ["finalize", "notify_reviewer"])
    g.add_edge("notify_reviewer", "human_review")
    g.add_edge("human_review", "finalize")
    g.add_edge("finalize", END)
    return g.compile(checkpointer=checkpointer)


def _after_decide_with_notify(state: ContractState) -> str:
    return "finalize" if state.get("decision") == "approve" else "notify_reviewer"


@lru_cache(maxsize=1)
def get_graph():
    config.ensure_dirs()
    conn = sqlite3.connect(config.CHECKPOINT_DB_PATH, check_same_thread=False)
    return build_graph(SqliteSaver(conn))


def thread_config(contract_id: str) -> dict:
    return {"configurable": {"thread_id": contract_id}}


def run_pipeline(contract_id: str, file_path: str, file_name: str) -> dict:
    emit(contract_id, "pipeline", "started", "بدأت معالجة الطلب")
    initial: ContractState = {"contract_id": contract_id, "file_path": file_path, "file_name": file_name,
                              "errors": []}
    try:
        return get_graph().invoke(initial, thread_config(contract_id))
    except Exception as exc:  # graph-level failure: never silent, never approved
        log.exception("pipeline crashed for %s", contract_id)
        db.update_contract(contract_id, status="error", decision="escalate")
        emit(contract_id, "pipeline", "error", "توقف المسار بسبب خطأ غير متوقع", {"error": str(exc)[:300]})
        raise


def resume_review(contract_id: str, review: dict) -> dict:
    return get_graph().invoke(Command(resume=review), thread_config(contract_id))


def get_state(contract_id: str) -> dict:
    snap = get_graph().get_state(thread_config(contract_id))
    return dict(snap.values) if snap and snap.values else {}
