"""SQLite storage for the contract registry, agent events, and token usage."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone

from . import config

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS contracts (
    id TEXT PRIMARY KEY,
    file_name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    status TEXT NOT NULL,
    decision TEXT,
    risk_score INTEGER,
    ocr_min REAL,
    doc_type TEXT,
    title TEXT,
    mock INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_contract ON events(contract_id, seq);
CREATE TABLE IF NOT EXISTS embeddings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    ref_id TEXT NOT NULL,
    page INTEGER,
    text TEXT NOT NULL,
    meta TEXT,
    model TEXT NOT NULL,
    dim INTEGER NOT NULL,
    vector BLOB NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS embeddings_contract ON embeddings(contract_id);
CREATE TABLE IF NOT EXISTS deed_edits (
    contract_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT,
    status TEXT NOT NULL,
    ts TEXT NOT NULL,
    PRIMARY KEY (contract_id, key)
);
CREATE TABLE IF NOT EXISTS usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id TEXT NOT NULL,
    agent TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cache_read_tokens INTEGER NOT NULL,
    cache_write_tokens INTEGER NOT NULL,
    cost_usd REAL NOT NULL,
    ts TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        config.ensure_dirs()
        _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(SCHEMA)
    return _conn


def reset_for_tests() -> None:
    global _conn
    _conn = None


def create_contract(contract_id: str, file_name: str, mock: bool) -> None:
    ts = now_iso()
    with _lock:
        conn().execute(
            "INSERT INTO contracts (id, file_name, created_at, updated_at, status, mock) VALUES (?,?,?,?,?,?)",
            (contract_id, file_name, ts, ts, "processing", int(mock)),
        )
        conn().commit()


def update_contract(contract_id: str, **fields) -> None:
    if not fields:
        return
    fields["updated_at"] = now_iso()
    cols = ", ".join(f"{k} = ?" for k in fields)
    with _lock:
        conn().execute(f"UPDATE contracts SET {cols} WHERE id = ?", (*fields.values(), contract_id))
        conn().commit()


def get_contract(contract_id: str) -> dict | None:
    row = conn().execute("SELECT * FROM contracts WHERE id = ?", (contract_id,)).fetchone()
    return dict(row) if row else None


def list_contracts(status: str | None = None) -> list[dict]:
    if status:
        rows = conn().execute(
            "SELECT * FROM contracts WHERE status = ? ORDER BY created_at DESC", (status,)
        ).fetchall()
    else:
        rows = conn().execute("SELECT * FROM contracts ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


def add_event(contract_id: str, payload: dict) -> int:
    with _lock:
        cur = conn().execute(
            "INSERT INTO events (contract_id, ts, payload) VALUES (?,?,?)",
            (contract_id, now_iso(), json.dumps(payload, ensure_ascii=False)),
        )
        conn().commit()
        return cur.lastrowid


def events_after(contract_id: str, after_seq: int) -> list[tuple[int, dict]]:
    rows = conn().execute(
        "SELECT seq, payload FROM events WHERE contract_id = ? AND seq > ? ORDER BY seq",
        (contract_id, after_seq),
    ).fetchall()
    return [(r["seq"], json.loads(r["payload"])) for r in rows]


def add_usage(contract_id: str, agent: str, model: str, input_tokens: int, output_tokens: int,
              cache_read: int, cache_write: int) -> None:
    price_in, price_out = config.price(model)
    cost = (
        input_tokens * price_in
        + cache_write * price_in * 1.25
        + cache_read * price_in * 0.1
        + output_tokens * price_out
    ) / 1_000_000
    with _lock:
        conn().execute(
            "INSERT INTO usage (contract_id, agent, model, input_tokens, output_tokens, cache_read_tokens,"
            " cache_write_tokens, cost_usd, ts) VALUES (?,?,?,?,?,?,?,?,?)",
            (contract_id, agent, model, input_tokens, output_tokens, cache_read, cache_write, cost, now_iso()),
        )
        conn().commit()


def deed_edits(contract_id: str) -> dict[str, dict]:
    rows = conn().execute("SELECT key, value, status FROM deed_edits WHERE contract_id = ?", (contract_id,)).fetchall()
    return {r["key"]: {"value": r["value"], "status": r["status"]} for r in rows}


def set_deed_edit(contract_id: str, key: str, value: str | None, status: str) -> None:
    """status: edited (reviewer's value), skipped (left out of the copy), confirmed, or auto (undo)."""
    with _lock:
        if status == "auto":
            conn().execute("DELETE FROM deed_edits WHERE contract_id = ? AND key = ?", (contract_id, key))
        else:
            conn().execute(
                "INSERT INTO deed_edits (contract_id, key, value, status, ts) VALUES (?,?,?,?,?)"
                " ON CONFLICT(contract_id, key) DO UPDATE SET value = excluded.value, status = excluded.status,"
                " ts = excluded.ts", (contract_id, key, value, status, now_iso()))
        conn().commit()


def delete_embeddings(contract_id: str) -> None:
    with _lock:
        conn().execute("DELETE FROM embeddings WHERE contract_id = ?", (contract_id,))
        conn().commit()


def add_embeddings(contract_id: str, model: str, items: list[dict]) -> None:
    ts = now_iso()
    with _lock:
        conn().executemany(
            "INSERT INTO embeddings (contract_id, kind, ref_id, page, text, meta, model, dim, vector, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            [(contract_id, it["kind"], it["ref_id"], it.get("page"), it["text"],
              json.dumps(it.get("meta") or {}, ensure_ascii=False), model, it["dim"], it["vector"], ts) for it in items],
        )
        conn().commit()


def all_embeddings(contract_id: str | None = None) -> list[dict]:
    sql = ("SELECT e.*, c.file_name, c.title FROM embeddings e JOIN contracts c ON c.id = e.contract_id")
    rows = (conn().execute(sql + " WHERE e.contract_id = ? ORDER BY e.id", (contract_id,)) if contract_id
            else conn().execute(sql + " ORDER BY e.id")).fetchall()
    return [dict(r) for r in rows]


def count_embeddings(contract_id: str) -> int:
    return conn().execute("SELECT COUNT(*) AS n FROM embeddings WHERE contract_id = ?", (contract_id,)).fetchone()["n"]


def total_spend(prefix: str | None = None, exclude: str | None = None) -> float:
    """Spend across all contracts, optionally only models starting with `prefix` or without `exclude`."""
    sql, args = "SELECT COALESCE(SUM(cost_usd), 0) AS c FROM usage WHERE 1=1", []
    if prefix:
        sql, args = sql + " AND model LIKE ?", [*args, prefix + "%"]
    if exclude:
        sql, args = sql + " AND model NOT LIKE ?", [*args, exclude + "%"]
    return float(conn().execute(sql, args).fetchone()["c"])


def usage_summary(contract_id: str) -> dict:
    rows = conn().execute(
        "SELECT agent, model, SUM(input_tokens) i, SUM(output_tokens) o, SUM(cache_read_tokens) cr,"
        " SUM(cache_write_tokens) cw, SUM(cost_usd) c, COUNT(*) n FROM usage WHERE contract_id = ?"
        " GROUP BY agent, model",
        (contract_id,),
    ).fetchall()
    per_agent = [
        {"agent": r["agent"], "model": r["model"], "calls": r["n"], "input_tokens": r["i"],
         "output_tokens": r["o"], "cache_read_tokens": r["cr"], "cache_write_tokens": r["cw"],
         "cost_usd": round(r["c"], 5)}
        for r in rows
    ]
    total_tokens = sum(a["input_tokens"] + a["output_tokens"] + a["cache_read_tokens"]
                       + a["cache_write_tokens"] for a in per_agent)
    return {
        "per_agent": per_agent,
        "total_tokens": total_tokens,
        "total_cost_usd": round(sum(a["cost_usd"] for a in per_agent), 4),
    }
