"""SQLite store. Payloads live as JSON text so the schema stays flexible."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.environ.get("DB_PATH", os.path.join(ROOT, "data", "app.db"))
JSON_COLS = {"raw", "profile", "signals", "transcript", "verdict_a", "verdict_b", "moments", "proposal", "data"}
_lock = threading.RLock()
_conn = None


def db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.executescript("""
        CREATE TABLE IF NOT EXISTS people (id TEXT PRIMARY KEY, name TEXT, li_url TEXT, ig_url TEXT, status TEXT,
            error TEXT, raw TEXT, profile TEXT, signals TEXT, source TEXT, created REAL);
        CREATE TABLE IF NOT EXISTS arcs (id INTEGER PRIMARY KEY AUTOINCREMENT, a TEXT, b TEXT, match_ab REAL,
            match_ba REAL, status TEXT, outcome TEXT, created REAL, UNIQUE(a, b));
        CREATE TABLE IF NOT EXISTS dates (id INTEGER PRIMARY KEY AUTOINCREMENT, arc_id INTEGER, seq INTEGER, day INTEGER,
            venue TEXT, why TEXT, status TEXT, transcript TEXT, verdict_a TEXT, verdict_b TEXT, moments TEXT,
            proposal TEXT, UNIQUE(arc_id, seq));
        CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, job TEXT, kind TEXT, data TEXT, ts REAL);
        """)
    return _conn


def _row(r) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    for k in JSON_COLS & d.keys():
        d[k] = json.loads(d[k]) if d[k] else None
    return d


def _enc(k, v):
    return json.dumps(v, ensure_ascii=False) if k in JSON_COLS and v is not None else v


def _update(table: str, key: str, kid, fields: dict):
    if fields:
        cols = ", ".join(f"{k}=?" for k in fields)
        db().execute(f"UPDATE {table} SET {cols} WHERE {key}=?", [*(_enc(k, v) for k, v in fields.items()), kid])


# ---- people
def upsert_person(pid: str, **fields):
    with _lock:
        db().execute("INSERT OR IGNORE INTO people(id, status, created) VALUES (?, 'queued', ?)", (pid, time.time()))
        _update("people", "id", pid, fields)
        db().commit()


def person(pid: str, raw: bool = False) -> dict | None:
    cols = "*" if raw else "id,name,li_url,ig_url,status,error,profile,signals,source,created"
    with _lock:
        return _row(db().execute(f"SELECT {cols} FROM people WHERE id=?", (pid,)).fetchone())


def people(status: str | None = "ready", raw: bool = False) -> list[dict]:
    cols = "*" if raw else "id,name,li_url,ig_url,status,error,profile,signals,source,created"
    q, args = f"SELECT {cols} FROM people", ()
    if status:
        q, args = q + " WHERE status=?", (status,)
    with _lock:
        return [_row(r) for r in db().execute(q + " ORDER BY created", args).fetchall()]


# ---- arcs (a relationship between two people) and their dates
def create_arc(a: str, b: str, match_ab: float, match_ba: float) -> int:
    with _lock:
        c = db()
        row = c.execute("SELECT id FROM arcs WHERE (a=? AND b=?) OR (a=? AND b=?)", (a, b, b, a)).fetchone()
        if row:
            return row["id"]
        cur = c.execute("INSERT INTO arcs(a, b, match_ab, match_ba, status, created) VALUES (?,?,?,?, 'live', ?)",
                        (a, b, match_ab, match_ba, time.time()))
        c.commit()
        return cur.lastrowid


def update_arc(aid: int, **fields):
    with _lock:
        _update("arcs", "id", aid, fields)
        db().commit()


def arc(aid: int) -> dict | None:
    with _lock:
        return _row(db().execute("SELECT * FROM arcs WHERE id=?", (aid,)).fetchone())


def arcs(pid: str | None = None) -> list[dict]:
    with _lock:
        if pid:
            rows = db().execute("SELECT * FROM arcs WHERE a=? OR b=? ORDER BY id", (pid, pid)).fetchall()
        else:
            rows = db().execute("SELECT * FROM arcs ORDER BY id").fetchall()
    return [_row(r) for r in rows]


def upsert_date(arc_id: int, seq: int, **fields) -> int:
    with _lock:
        c = db()
        c.execute("INSERT OR IGNORE INTO dates(arc_id, seq, status, transcript) VALUES (?, ?, 'live', '[]')", (arc_id, seq))
        did = c.execute("SELECT id FROM dates WHERE arc_id=? AND seq=?", (arc_id, seq)).fetchone()["id"]
        _update("dates", "id", did, fields)
        c.commit()
        return did


def dates(arc_id: int) -> list[dict]:
    with _lock:
        return [_row(r) for r in db().execute("SELECT * FROM dates WHERE arc_id=? ORDER BY seq", (arc_id,)).fetchall()]


def all_dates() -> list[dict]:
    with _lock:
        return [_row(r) for r in db().execute("SELECT * FROM dates ORDER BY arc_id, seq").fetchall()]


# ---- live job events (the SSE bus)
def emit(job: str, kind: str, data):
    with _lock:
        db().execute("INSERT INTO events(job, kind, data, ts) VALUES (?,?,?,?)", (job, kind, json.dumps(data, ensure_ascii=False), time.time()))
        db().commit()


def events(job: str, after: int = 0) -> list[dict]:
    with _lock:
        rows = db().execute("SELECT id, kind, data FROM events WHERE job=? AND id>? ORDER BY id", (job, after)).fetchall()
    return [{"id": r["id"], "kind": r["kind"], "data": json.loads(r["data"])} for r in rows]
