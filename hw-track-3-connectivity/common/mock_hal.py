"""
TEMPORARY STAND-IN for HW-1 and HW-2 hardware interfaces.
Replace with real drivers when hardware arrives — swap is a one-file change.

Mock chip interface replaces ATECC608B secure element.
"""
from __future__ import annotations

import hashlib
import os
import struct
import time
from typing import Iterator, Optional

from cryptography.hazmat.primitives import hashes, hmac, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from common.seams import IDeviceStateProvider, IEncryptionDaemon, ISensorProvider
from common.types import (
    CaptureEvent,
    CaptureLevel,
    DeviceState,
    EncryptedBlob,
    OperatingMode,
    SensorReading,
    SensorUnavailableReason,
)


# ---------------------------------------------------------------------------
# HW-2 mock: encryption daemon (ATECC608B stand-in)
# ---------------------------------------------------------------------------

class MockEncryptionDaemon(IEncryptionDaemon):
    """
    TEMPORARY STAND-IN for ATECC608B secure element + HW-2 encryption daemon.
    Real AES-GCM encryption and RSA-2048 signing; mock key storage only.

    Key hierarchy:
      DIK  — Device Identity Key, RSA-2048, generated once per instance
      DSK  — Data Session Key, AES-256, derived from DIK + date (never persisted)
      STK  — Server Transport Key, AES-256, per-upload-session, ephemeral
    """

    def __init__(self):
        # DIK — generated once, stands in for ATECC608B-provisioned key
        self._dik_private = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
        )
        self._dik_public = self._dik_private.public_key()
        self._ready = True

    def _derive_dsk(self, date_str: str) -> bytes:
        """Derive daily Data Session Key from DIK + date string (SHA-256)."""
        dik_bytes = self._dik_private.private_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        return hashlib.sha256(dik_bytes + date_str.encode()).digest()

    def _generate_stk(self) -> bytes:
        """Generate per-session Server Transport Key."""
        return os.urandom(32)

    def encrypt_and_sign(self, raw: bytes, meta: dict) -> EncryptedBlob:
        date_str = meta.get("date", time.strftime("%Y-%m-%d"))
        session_id = meta.get("session_id", os.urandom(8).hex())
        key_id = f"dsk-{date_str}"

        dsk = self._derive_dsk(date_str)
        iv = os.urandom(12)
        aesgcm = AESGCM(dsk)
        ct_with_tag = aesgcm.encrypt(iv, raw, None)
        ciphertext = ct_with_tag[:-16]
        tag = ct_with_tag[-16:]

        sha256_hash = hashlib.sha256(ciphertext).hexdigest()
        signature = self._dik_private.sign(
            sha256_hash.encode(),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )

        return EncryptedBlob(
            ciphertext=ciphertext,
            iv=iv,
            tag=tag,
            signature=signature,
            key_id=key_id,
            session_id=session_id,
            sha256_hash=sha256_hash,
            metadata=meta,
        )

    def verify(self, blob: EncryptedBlob) -> bool:
        try:
            expected_hash = hashlib.sha256(blob.ciphertext).hexdigest()
            if expected_hash != blob.sha256_hash:
                return False
            self._dik_public.verify(
                blob.signature,
                blob.sha256_hash.encode(),
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
            return True
        except Exception:
            return False

    def decrypt(self, blob: EncryptedBlob) -> bytes:
        date_str = blob.key_id.replace("dsk-", "")
        dsk = self._derive_dsk(date_str)
        aesgcm = AESGCM(dsk)
        ct_with_tag = blob.ciphertext + blob.tag
        return aesgcm.decrypt(blob.iv, ct_with_tag, None)

    def get_public_key_pem(self) -> bytes:
        return self._dik_public.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )

    def is_ready(self) -> bool:
        return self._ready

    def simulate_not_ready(self):
        self._ready = False

    def simulate_ready(self):
        self._ready = True


# ---------------------------------------------------------------------------
# HW-1 mock: sensor / capture-intensity provider
# ---------------------------------------------------------------------------

class MockSensorProvider(ISensorProvider):
    """
    TEMPORARY STAND-IN for HW-1 sensor capture daemon.
    Emits synthetic bytes clearly labeled synthetic.
    Supports toggling worn-state and UNAVAILABLE for R3 testing.
    """

    SYNTHETIC_AUDIO_MARKER = b"SYNTHETIC_AUDIO:"
    SYNTHETIC_CAMERA_MARKER = b"SYNTHETIC_FRAME:"
    SYNTHETIC_IMU_MARKER = b"SYNTHETIC_IMU:"
    SYNTHETIC_PPG_MARKER = b"SYNTHETIC_PPG:"

    def __init__(self):
        self._worn: Optional[bool] = True  # None = UNAVAILABLE
        self._capture_level = CaptureLevel.L2
        self._audio_unavailable = False
        self._camera_unavailable = False
        self._imu_unavailable = False
        self._ppg_unavailable = False
        self._event_queue: list[CaptureEvent] = []
        self._clock_fn = time.time

    def set_clock(self, fn):
        self._clock_fn = fn

    def set_worn(self, worn: Optional[bool]):
        """None = UNAVAILABLE (R3)."""
        self._worn = worn

    def set_capture_level(self, level: CaptureLevel):
        self._capture_level = level

    def push_event(self, event: CaptureEvent):
        self._event_queue.append(event)

    def get_worn_state(self) -> SensorReading:
        ts = self._clock_fn()
        if self._worn is None:
            return SensorReading.unavailable("worn_detector", ts, SensorUnavailableReason.UNKNOWN)
        return SensorReading.available("worn_detector", ts, self._worn, unit="bool")

    def get_capture_level(self) -> Optional[CaptureLevel]:
        return self._capture_level

    def next_capture_event(self) -> Optional[CaptureEvent]:
        if self._event_queue:
            return self._event_queue.pop(0)
        return CaptureEvent(
            timestamp=self._clock_fn(),
            level=self._capture_level,
            cause="periodic",
        )

    def get_audio_chunk(self) -> SensorReading:
        ts = self._clock_fn()
        if self._audio_unavailable:
            return SensorReading.unavailable("audio", ts, SensorUnavailableReason.HARDWARE_FAULT)
        payload = self.SYNTHETIC_AUDIO_MARKER + os.urandom(256)
        return SensorReading.available("audio", ts, payload, unit="bytes")

    def get_camera_frame(self) -> SensorReading:
        ts = self._clock_fn()
        if self._camera_unavailable:
            return SensorReading.unavailable("camera", ts, SensorUnavailableReason.HARDWARE_FAULT)
        payload = self.SYNTHETIC_CAMERA_MARKER + os.urandom(1024)
        return SensorReading.available("camera", ts, payload, unit="bytes")

    def get_imu_batch(self) -> SensorReading:
        ts = self._clock_fn()
        if self._imu_unavailable:
            return SensorReading.unavailable("imu", ts, SensorUnavailableReason.HARDWARE_FAULT)
        payload = self.SYNTHETIC_IMU_MARKER + struct.pack("!6f", *[0.0] * 6)
        return SensorReading.available("imu", ts, payload, unit="bytes")

    def get_ppg_batch(self) -> SensorReading:
        ts = self._clock_fn()
        if self._ppg_unavailable:
            return SensorReading.unavailable("ppg", ts, SensorUnavailableReason.HARDWARE_FAULT)
        payload = self.SYNTHETIC_PPG_MARKER + struct.pack("!32H", *([500] * 32))
        return SensorReading.available("ppg", ts, payload, unit="bytes")


# ---------------------------------------------------------------------------
# Device state provider mock
# ---------------------------------------------------------------------------

class MockDeviceStateProvider(IDeviceStateProvider):
    """Mock device state — queried by BLE daemon through seams.py (R4)."""

    def __init__(self):
        self._state = DeviceState(
            battery_pct=85,
            firmware_version="1.0.0-mock",
            sync_status="idle",
            storage_used_bytes=1024 * 1024 * 100,
            storage_free_bytes=1024 * 1024 * 900,
            current_level=CaptureLevel.L2,
            kill_switch_camera=False,
            audio_paused=False,
            operating_mode=OperatingMode.NORMAL,
            worn=True,
        )

    def get_state(self) -> DeviceState:
        return self._state

    def set_kill_switch_camera(self, enabled: bool) -> None:
        self._state.kill_switch_camera = enabled

    def set_audio_paused(self, paused: bool, duration_s: Optional[int] = None) -> None:
        self._state.audio_paused = paused

    def set_operating_mode(self, mode: OperatingMode) -> None:
        self._state.operating_mode = mode

    def set_battery_pct(self, pct: int) -> None:
        self._state.battery_pct = pct

    def set_worn(self, worn: Optional[bool]) -> None:
        """None = UNAVAILABLE."""
        self._state.worn = worn
