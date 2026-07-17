"""
OTA update receiver.
Signature verification (RSA-2048), SHA-256 integrity check, partition management.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey

from ota.partitions import PartitionManager

logger = logging.getLogger(__name__)

MAX_FAILED_BOOTS = 3


class OTAError(Exception):
    pass


class SignatureVerificationFailed(OTAError):
    pass


class IntegrityCheckFailed(OTAError):
    pass


@dataclass
class OTAManifest:
    version: str
    sha256: str
    size: int


class OTAReceiver:

    def __init__(
        self,
        partitions: PartitionManager,
        public_key: RSAPublicKey,
        alert_fn: Optional[Callable[[str, dict], None]] = None,
    ):
        self._partitions = partitions
        self._public_key = public_key
        self._alert_fn = alert_fn or (lambda t, p: None)

    def _emit_alert(self, alert_type: str, payload: dict):
        logger.warning("OTA alert: %s %s", alert_type, payload)
        self._alert_fn(alert_type, payload)

    def receive_update(
        self,
        image_bytes: bytes,
        manifest: OTAManifest,
        signature: bytes,
    ) -> None:
        """
        Full OTA pipeline: verify signature → check hash → write to inactive slot → mark active.
        Raises and emits phone-alert on signature failure.
        """
        # 1. Signature verification (RSA-2048 over the image)
        try:
            self._public_key.verify(
                signature,
                image_bytes,
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
        except Exception as exc:
            self._emit_alert(
                "ota_signature_failed",
                {"version": manifest.version, "reason": str(exc)},
            )
            raise SignatureVerificationFailed(
                f"OTA signature verification failed for version {manifest.version}"
            ) from exc

        # 2. Integrity check
        actual_sha256 = hashlib.sha256(image_bytes).hexdigest()
        if actual_sha256 != manifest.sha256:
            self._emit_alert(
                "ota_integrity_failed",
                {"version": manifest.version, "expected": manifest.sha256, "actual": actual_sha256},
            )
            raise IntegrityCheckFailed(
                f"SHA-256 mismatch: expected {manifest.sha256}, got {actual_sha256}"
            )

        # 3. Write to inactive partition
        self._partitions.write_to_inactive(image_bytes, manifest.version)
        logger.info("OTA image written to %s", self._partitions.inactive_slot)

        # 4. Mark inactive as active (pending boot confirmation)
        self._partitions.mark_inactive_active()
        logger.info("OTA: slot %s is now active, awaiting boot", self._partitions.active_slot)


class RollbackManager:
    """Tracks boot attempts and rolls back after MAX_FAILED_BOOTS consecutive failures."""

    def __init__(self, partitions: PartitionManager):
        self._partitions = partitions

    def record_boot_attempt(self) -> None:
        count = self._partitions.increment_boot_attempts()
        logger.info("Boot attempt #%d on slot %s", count, self._partitions.active_slot)
        if count >= MAX_FAILED_BOOTS:
            self._rollback()

    def record_successful_boot(self) -> None:
        self._partitions.reset_boot_attempts()
        logger.info("Successful boot on slot %s", self._partitions.active_slot)

    def _rollback(self) -> None:
        failed_slot = self._partitions.active_slot
        failed_version = self._partitions.active_version()
        self._partitions.mark_inactive_active()
        reverted_version = self._partitions.active_version()
        logger.warning(
            "ROLLBACK: %d consecutive boot failures on %s (v%s) → reverted to %s (v%s)",
            MAX_FAILED_BOOTS,
            failed_slot,
            failed_version,
            self._partitions.active_slot,
            reverted_version,
        )
