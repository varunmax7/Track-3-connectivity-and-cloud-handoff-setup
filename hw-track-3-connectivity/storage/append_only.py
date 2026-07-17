"""
R2: Append-only record enforcement.
Any attempt to modify/replace an existing record raises AppendOnlyViolation.
"""
from __future__ import annotations

from typing import Dict, Optional


class AppendOnlyViolation(Exception):
    """Raised when code attempts to overwrite an existing record (R2)."""


class AppendOnlyStore:
    """In-memory append-only record store — enforces R2 at the application level."""

    def __init__(self):
        self._records: Dict[str, dict] = {}

    def insert(self, record_id: str, data: dict) -> None:
        if record_id in self._records:
            raise AppendOnlyViolation(
                f"Record '{record_id}' already exists — append-only store forbids overwrite (R2)"
            )
        self._records[record_id] = dict(data)

    def get(self, record_id: str) -> Optional[dict]:
        return self._records.get(record_id)

    def exists(self, record_id: str) -> bool:
        return record_id in self._records

    def all_ids(self) -> list[str]:
        return list(self._records.keys())

    def __len__(self) -> int:
        return len(self._records)
