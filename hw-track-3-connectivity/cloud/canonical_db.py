"""
Canonical record DB — append-only SQLite store.
BEFORE UPDATE and BEFORE DELETE triggers → RAISE(ABORT) — R2 at DB level.
Hash-chain: each record links to prev_hash for tamper evidence.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Optional

DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS canonical_records (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id   TEXT    NOT NULL UNIQUE,
    device_id   TEXT    NOT NULL,
    ts          REAL    NOT NULL,
    event_type  TEXT    NOT NULL,
    payload_json TEXT   NOT NULL,
    hash        TEXT    NOT NULL UNIQUE,
    prev_hash   TEXT
);

-- R2: forbid UPDATE
CREATE TRIGGER IF NOT EXISTS no_update_canonical
    BEFORE UPDATE ON canonical_records
BEGIN
    SELECT RAISE(ABORT, 'canonical_records is append-only: UPDATE forbidden (R2)');
END;

-- R2: forbid DELETE
CREATE TRIGGER IF NOT EXISTS no_delete_canonical
    BEFORE DELETE ON canonical_records
BEGIN
    SELECT RAISE(ABORT, 'canonical_records is append-only: DELETE forbidden (R2)');
END;
"""


class CanonicalDB:

    def __init__(self, db_path: str = ":memory:"):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(DB_SCHEMA)
        self._conn.commit()

    def _last_hash(self) -> Optional[str]:
        row = self._conn.execute(
            "SELECT hash FROM canonical_records ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return row["hash"] if row else None

    def insert(
        self,
        record_id: str,
        device_id: str,
        event_type: str,
        payload: dict,
        ts: Optional[float] = None,
    ) -> str:
        ts = ts or time.time()
        prev_hash = self._last_hash()
        payload_json = json.dumps(payload, sort_keys=True)
        chain_input = f"{record_id}:{device_id}:{ts}:{event_type}:{payload_json}:{prev_hash or ''}"
        row_hash = hashlib.sha256(chain_input.encode()).hexdigest()

        self._conn.execute(
            """
            INSERT INTO canonical_records
                (record_id, device_id, ts, event_type, payload_json, hash, prev_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (record_id, device_id, ts, event_type, payload_json, row_hash, prev_hash),
        )
        self._conn.commit()
        return row_hash

    def get(self, record_id: str) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT * FROM canonical_records WHERE record_id = ?", (record_id,)
        ).fetchone()
        if row:
            return dict(row)
        return None

    def all_records(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM canonical_records ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]

    def close(self):
        self._conn.close()
