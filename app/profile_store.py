"""Encrypted personal facts in SQLite. Values are encrypted with Fernet; the key
comes from the OS credential store, so the .db file alone is useless."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from cryptography.fernet import Fernet, InvalidToken


class ProfileLocked(Exception):
    """Stored data cannot be decrypted with the current key."""


class ProfileStore:
    def __init__(self, db_path, key: bytes):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._f = Fernet(key)
        with self._conn() as c:
            c.execute("CREATE TABLE IF NOT EXISTS facts (key TEXT PRIMARY KEY, value BLOB NOT NULL, updated TEXT NOT NULL)")

    def _conn(self):
        return sqlite3.connect(self.path)

    def set(self, key: str, value: str) -> None:
        token = self._f.encrypt(value.encode())
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as c:
            c.execute(
                "INSERT INTO facts(key,value,updated) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated=excluded.updated",
                (key, token, now),
            )

    def _decrypt(self, token: bytes) -> str:
        try:
            return self._f.decrypt(token).decode()
        except InvalidToken as e:
            raise ProfileLocked("Cannot decrypt profile: wrong or missing key") from e

    def get(self, key: str) -> Optional[str]:
        with self._conn() as c:
            row = c.execute("SELECT value FROM facts WHERE key=?", (key,)).fetchone()
        return self._decrypt(row[0]) if row else None

    def delete(self, key: str) -> bool:
        with self._conn() as c:
            return c.execute("DELETE FROM facts WHERE key=?", (key,)).rowcount > 0

    def keys(self) -> List[str]:
        with self._conn() as c:
            return [r[0] for r in c.execute("SELECT key FROM facts ORDER BY key")]

    def all(self) -> Dict[str, str]:
        with self._conn() as c:
            rows = c.execute("SELECT key,value FROM facts ORDER BY key").fetchall()
        return {k: self._decrypt(v) for k, v in rows}