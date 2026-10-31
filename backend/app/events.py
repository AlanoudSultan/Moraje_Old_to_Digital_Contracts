"""Agent event bus. Events are persisted in SQLite so the SSE stream can replay them on reconnect."""
from __future__ import annotations

import logging
from typing import Any, Literal

from . import db

log = logging.getLogger("events")

AGENT_LABELS_AR = {
    "intake": "وكيل الاستلام",
    "digitize": "وكيل الرقمنة",
    "reconstruct": "وكيل إعادة البناء",
    "clause": "وكيل تصنيف البنود",
    "policy": "وكيل السياسات",
    "redline": "وكيل التعديلات المقترحة",
    "decide": "وكيل القرار",
    "human_review": "المراجعة البشرية",
    "finalize": "الإغلاق",
    "embed": "التضمين",
    "deed": "نموذج الصك",
    "pipeline": "المسار",
}


def emit(contract_id: str, agent: str, status: Literal["started", "done", "error", "waiting", "info"],
         message_ar: str, data: dict[str, Any] | None = None) -> None:
    payload = {"agent": agent, "status": status, "message_ar": message_ar, "data": data or {}}
    db.add_event(contract_id, payload)
    # Log metadata only, never contract text.
    log.info("contract=%s agent=%s status=%s", contract_id, agent, status)
