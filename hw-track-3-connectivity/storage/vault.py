"""
Storage vault: the ONLY interface for writing user data to disk.
vault_write() accepts ONLY EncryptedBlob — raw bytes are rejected at the type level (R1).
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import date
from pathlib import Path
from typing import Optional

from common.types import EncryptedBlob
from storage.append_only import AppendOnlyStore, AppendOnlyViolation


VAULT_CATEGORIES = {"audio", "camera", "imu", "ppg", "metadata"}
_USER_DATA_CATEGORIES = {"audio", "camera", "imu", "ppg"}


class RecordId(str):
    """Typed record identifier — prevents confusion with raw strings."""


class VaultWriteError(Exception):
    pass


class Vault:
    """
    Manages the vault tree rooted at base_dir.

    /vault/YYYY-MM-DD/{audio,camera,sensors/imu,sensors/ppg}/
    /vault/YYYY-MM-DD/metadata.json
    /vault/YYYY-MM-DD/manifest.sha
    /system/logs/
    /system/config/
    /system/firmware/
    """

    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self._manifest_store = AppendOnlyStore()
        self._meta_store = AppendOnlyStore()
        self._init_tree()

    def _init_tree(self):
        for subdir in [
            "system/logs",
            "system/config",
            "system/firmware/slot_a",
            "system/firmware/slot_b",
        ]:
            (self.base_dir / subdir).mkdir(parents=True, exist_ok=True)

    def _vault_day_path(self, category: str, day: str) -> Path:
        if category in ("imu", "ppg"):
            p = self.base_dir / "vault" / day / "sensors" / category
        else:
            p = self.base_dir / "vault" / day / category
        p.mkdir(parents=True, exist_ok=True)
        return p

    def vault_write(self, blob: EncryptedBlob, category: str, day: Optional[str] = None) -> RecordId:
        """
        R1: The ONLY storage-write function for user data.
        Raises TypeError if blob is not an EncryptedBlob — impossible to pass raw bytes.
        """
        if not isinstance(blob, EncryptedBlob):
            raise TypeError(
                f"vault_write requires EncryptedBlob, got {type(blob).__name__}. "
                "Raw bytes must go through the encryption daemon first (R1)."
            )
        if category not in VAULT_CATEGORIES:
            raise VaultWriteError(f"Unknown category '{category}'. Must be one of {VAULT_CATEGORIES}")

        day = day or date.today().isoformat()
        record_id = RecordId(f"{day}/{category}/{blob.sha256_hash[:16]}-{blob.session_id}")

        dest_dir = self._vault_day_path(category, day)
        blob_path = dest_dir / f"{blob.sha256_hash[:16]}-{blob.session_id}.blob"

        blob_dict = blob.to_dict()
        blob_bytes = json.dumps(blob_dict).encode()

        blob_path.write_bytes(blob_bytes)

        self._meta_store.insert(record_id, {
            "record_id": record_id,
            "category": category,
            "day": day,
            "sha256_hash": blob.sha256_hash,
            "session_id": blob.session_id,
            "key_id": blob.key_id,
            "path": str(blob_path),
        })

        self._update_manifest(day, record_id, blob.sha256_hash)
        return record_id

    def _update_manifest(self, day: str, record_id: str, sha256_hash: str):
        manifest_key = f"{day}/{record_id}"
        self._manifest_store.insert(manifest_key, {
            "record_id": record_id,
            "sha256_hash": sha256_hash,
        })

        manifest_path = self.base_dir / "vault" / day / "manifest.sha"
        with manifest_path.open("a") as f:
            f.write(f"{sha256_hash}  {record_id}\n")

    def get_record_meta(self, record_id: str) -> Optional[dict]:
        return self._meta_store.get(record_id)

    def log_write(self, message: str, context: str = "system") -> None:
        """Plaintext log writer — refuses payloads tagged as user data."""
        if context in _USER_DATA_CATEGORIES:
            raise VaultWriteError(
                f"Cannot write user data context '{context}' to plaintext logs (R1)"
            )
        log_path = self.base_dir / "system" / "logs" / "system.log"
        with log_path.open("a") as f:
            f.write(f"{message}\n")

    def config_write(self, key: str, value: str) -> None:
        config_path = self.base_dir / "system" / "config" / f"{key}.cfg"
        config_path.write_text(value)

    def config_read(self, key: str) -> Optional[str]:
        config_path = self.base_dir / "system" / "config" / f"{key}.cfg"
        if config_path.exists():
            return config_path.read_text()
        return None
