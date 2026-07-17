"""
Double-confirmation deletion.
A file is deletable ONLY when BOTH are true:
  1. Device-side upload-complete confirmation recorded for that file
  2. Independent server-side receipt confirmation with matching SHA-256

No force-delete path exists. No exceptions flag.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from storage.vault import Vault

logger = logging.getLogger(__name__)


@dataclass
class DeletionRecord:
    record_id: str
    device_confirmed: bool = False
    server_confirmed: bool = False
    device_sha256: Optional[str] = None
    server_sha256: Optional[str] = None


class DeletionRefused(Exception):
    """Raised when deletion conditions are not met."""


class DeletionManager:

    def __init__(self, vault: Vault):
        self._vault = vault
        self._records: Dict[str, DeletionRecord] = {}

    def _get_or_create(self, record_id: str) -> DeletionRecord:
        if record_id not in self._records:
            self._records[record_id] = DeletionRecord(record_id=record_id)
        return self._records[record_id]

    def confirm_device_upload(self, record_id: str, sha256: str) -> None:
        rec = self._get_or_create(record_id)
        rec.device_confirmed = True
        rec.device_sha256 = sha256
        logger.info("Device upload confirmed for %s", record_id)

    def confirm_server_receipt(self, record_id: str, server_sha256: str) -> None:
        rec = self._get_or_create(record_id)
        rec.server_confirmed = True
        rec.server_sha256 = server_sha256
        logger.info("Server receipt confirmed for %s", record_id)

    def delete(self, record_id: str) -> None:
        """
        Delete a file only when both conditions are met and hashes match.
        Raises DeletionRefused in every other case — no force-delete path.
        """
        rec = self._records.get(record_id)

        if rec is None or not rec.device_confirmed:
            msg = f"Deletion refused for '{record_id}': missing device upload confirmation"
            logger.warning(msg)
            raise DeletionRefused(msg)

        if not rec.server_confirmed:
            msg = f"Deletion refused for '{record_id}': missing server receipt confirmation"
            logger.warning(msg)
            raise DeletionRefused(msg)

        if rec.device_sha256 != rec.server_sha256:
            msg = (
                f"Deletion refused for '{record_id}': SHA-256 hash mismatch "
                f"(device={rec.device_sha256}, server={rec.server_sha256})"
            )
            logger.warning(msg)
            raise DeletionRefused(msg)

        meta = self._vault.get_record_meta(record_id)
        if meta is None:
            raise DeletionRefused(f"Record '{record_id}' not found in vault")

        blob_path = Path(meta["path"])
        if blob_path.exists():
            blob_path.unlink()
            logger.info("Deleted local file for record %s", record_id)

        del self._records[record_id]

    def can_delete(self, record_id: str) -> bool:
        rec = self._records.get(record_id)
        if not rec:
            return False
        return (
            rec.device_confirmed
            and rec.server_confirmed
            and rec.device_sha256 == rec.server_sha256
        )
