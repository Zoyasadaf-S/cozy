"""SQLite storage. WAL + synchronous=NORMAL keeps disk writes (and battery use) low."""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS history (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, text TEXT, tool TEXT, ok INTEGER, message TEXT);
CREATE TABLE IF NOT EXISTS reminders (
  id INTEGER PRIMARY KEY AUTOINCREMENT, due REAL NOT NULL, kind TEXT NOT NULL, text TEXT,
  command TEXT, repeat TEXT, done INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS reminders_due ON reminders(done, due);
CREATE TABLE IF NOT EXISTS plays (query TEXT PRIMARY KEY, count INTEGER NOT NULL, last REAL NOT NULL);
"""

_LONG_DIGITS = re.compile(r"\d{6,}")


def redact(text: str) -> str:
    """Keep secrets out of the action log."""
    text = _LONG_DIGITS.sub("[number]", text or "")
    return re.sub(r"(?i)\b(password|passcode|pin|otp|token|secret|api key)\b\s*(?:is|=|:)?\s*\S+", r"\1 [hidden]", text)


class Store:
    def __init__(self, path):
        path = Path(path)
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=NORMAL")
            self._db.executescript(SCHEMA)

    # ---- key/value (facts, macros, snippets, projects, settings, profiles) ----
    def kv_get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            row = self._db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def kv_set(self, key: str, value: Any) -> None:
        with self._lock:
            self._db.execute("INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                             (key, json.dumps(value)))
            self._db.commit()

    def kv_delete(self, key: str) -> bool:
        with self._lock:
            cur = self._db.execute("DELETE FROM kv WHERE key=?", (key,))
            self._db.commit()
            return cur.rowcount > 0

    def kv_all(self, prefix: str) -> Dict[str, Any]:
        with self._lock:
            rows = self._db.execute("SELECT key,value FROM kv WHERE key LIKE ? ESCAPE '\\' ORDER BY key",
                                    (prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%",)).fetchall()
        return {r["key"][len(prefix):]: json.loads(r["value"]) for r in rows}

    # ---- action log ----
    def log_action(self, text: str, tool: str, ok: bool, message: str, ts: Optional[float] = None) -> None:
        with self._lock:
            self._db.execute("INSERT INTO history(ts,text,tool,ok,message) VALUES(?,?,?,?,?)",
                             (ts or time.time(), redact(text), tool, int(ok), redact(message)[:500]))
            self._db.execute("DELETE FROM history WHERE id <= (SELECT MAX(id) FROM history) - 2000")
            self._db.commit()

    def history(self, limit: int = 50, since: float = 0.0) -> List[dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM history WHERE ts>=? ORDER BY id DESC LIMIT ?", (since, limit)).fetchall()
        return [dict(r) for r in rows]

    # ---- reminders / timers / alarms / scheduled commands ----
    def add_reminder(self, due: float, kind: str, text: str, command: Optional[str], repeat: Optional[dict]) -> int:
        with self._lock:
            cur = self._db.execute("INSERT INTO reminders(due,kind,text,command,repeat) VALUES(?,?,?,?,?)",
                                   (due, kind, text, command, json.dumps(repeat) if repeat else None))
            self._db.commit()
            return int(cur.lastrowid)

    def next_due(self) -> Optional[float]:
        with self._lock:
            row = self._db.execute("SELECT MIN(due) AS d FROM reminders WHERE done=0").fetchone()
        return row["d"] if row and row["d"] is not None else None

    def due_reminders(self, now: float) -> List[dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM reminders WHERE done=0 AND due<=? ORDER BY due", (now,)).fetchall()
        return [self._rem(r) for r in rows]

    def list_reminders(self, kind: Optional[str] = None) -> List[dict]:
        q, args = "SELECT * FROM reminders WHERE done=0", []
        if kind:
            q += " AND kind=?"
            args.append(kind)
        with self._lock:
            rows = self._db.execute(q + " ORDER BY due", args).fetchall()
        return [self._rem(r) for r in rows]

    def reschedule(self, rid: int, due: float) -> None:
        with self._lock:
            self._db.execute("UPDATE reminders SET due=? WHERE id=?", (due, rid))
            self._db.commit()

    def finish_reminder(self, rid: int) -> None:
        with self._lock:
            self._db.execute("DELETE FROM reminders WHERE id=?", (rid,))
            self._db.commit()

    def cancel_reminders(self, rid: Optional[int] = None, kind: Optional[str] = None) -> int:
        q, args = "DELETE FROM reminders WHERE done=0", []
        if rid is not None:
            q += " AND id=?"
            args.append(rid)
        if kind:
            q += " AND kind=?"
            args.append(kind)
        with self._lock:
            cur = self._db.execute(q, args)
            self._db.commit()
            return cur.rowcount

    @staticmethod
    def _rem(row) -> dict:
        d = dict(row)
        d["repeat"] = json.loads(d["repeat"]) if d.get("repeat") else None
        return d

    # ---- play history (for "play my usual music") ----
    def record_play(self, query: str) -> None:
        q = query.strip().lower()
        if not q:
            return
        with self._lock:
            self._db.execute("INSERT INTO plays(query,count,last) VALUES(?,1,?) "
                             "ON CONFLICT(query) DO UPDATE SET count=count+1,last=excluded.last", (q, time.time()))
            self._db.commit()

    def top_plays(self, n: int = 5) -> List[dict]:
        with self._lock:
            rows = self._db.execute("SELECT query,count FROM plays ORDER BY count DESC,last DESC LIMIT ?", (n,)).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        with self._lock:
            self._db.close()
