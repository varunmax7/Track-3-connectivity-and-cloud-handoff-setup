"""
Core types shared across all daemons.
EncryptedBlob is the only permitted input to storage writes (R1).
SensorReading explicitly models UNAVAILABLE to prevent zero-substitution (R3).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class SensorUnavailableReason(str, Enum):
    NOT_WORN = "not_worn"
    HARDWARE_FAULT = "hardware_fault"
    INITIALIZING = "initializing"
    LOW_POWER = "low_power"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SensorReading:
    """R3: value is None when unavailable — never a substitute zero."""
    sensor_id: str
    timestamp: float
    value: Optional[Any]
    reason: Optional[SensorUnavailableReason]
    unit: str = ""

    @classmethod
    def available(cls, sensor_id: str, timestamp: float, value: Any, unit: str = "") -> "SensorReading":
        return cls(sensor_id=sensor_id, timestamp=timestamp, value=value, unit=unit, reason=None)

    @classmethod
    def unavailable(cls, sensor_id: str, timestamp: float, reason: SensorUnavailableReason) -> "SensorReading":
        return cls(sensor_id=sensor_id, timestamp=timestamp, value=None, unit="", reason=reason)

    def to_wire(self) -> dict:
        """R3: propagates null-with-reason, never substitutes default."""
        return {
            "sensor_id": self.sensor_id,
            "timestamp": self.timestamp,
            "value": self.value,
            "reason": self.reason.value if self.reason else None,
            "unit": self.unit,
        }

    @property
    def is_available(self) -> bool:
        return self.value is not None and self.reason is None


@dataclass(frozen=True)
class EncryptedBlob:
    """
    R1: The ONLY type accepted by vault_write.
    Callers cannot construct this from raw bytes — they must go through
    the encryption daemon's encrypt_and_sign().
    """
    ciphertext: bytes
    iv: bytes
    tag: bytes
    signature: bytes
    key_id: str
    session_id: str
    sha256_hash: str
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.ciphertext, bytes):
            raise TypeError("ciphertext must be bytes")
        if not isinstance(self.iv, bytes) or len(self.iv) != 12:
            raise TypeError("iv must be 12 bytes (AES-GCM nonce)")
        if not isinstance(self.tag, bytes) or len(self.tag) != 16:
            raise TypeError("tag must be 16 bytes (AES-GCM auth tag)")
        if not isinstance(self.signature, bytes):
            raise TypeError("signature must be bytes")
        expected_hash = hashlib.sha256(self.ciphertext).hexdigest()
        if self.sha256_hash != expected_hash:
            raise ValueError("sha256_hash does not match ciphertext")

    def to_dict(self) -> dict:
        return {
            "ciphertext": self.ciphertext.hex(),
            "iv": self.iv.hex(),
            "tag": self.tag.hex(),
            "signature": self.signature.hex(),
            "key_id": self.key_id,
            "session_id": self.session_id,
            "sha256_hash": self.sha256_hash,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "EncryptedBlob":
        return cls(
            ciphertext=bytes.fromhex(d["ciphertext"]),
            iv=bytes.fromhex(d["iv"]),
            tag=bytes.fromhex(d["tag"]),
            signature=bytes.fromhex(d["signature"]),
            key_id=d["key_id"],
            session_id=d["session_id"],
            sha256_hash=d["sha256_hash"],
            metadata=d.get("metadata", {}),
        )


class CaptureLevel(int, Enum):
    L0 = 0  # idle
    L1 = 1
    L2 = 2
    L3 = 3
    L4 = 4
    L5 = 5  # max capture


@dataclass(frozen=True)
class CaptureEvent:
    timestamp: float
    level: CaptureLevel
    cause: str


@dataclass(frozen=True)
class AlertEvent:
    timestamp: float
    alert_type: str
    payload: dict = field(default_factory=dict)


class OperatingMode(str, Enum):
    NORMAL = "normal"
    LOW_POWER = "low_power"
    SLEEP = "sleep"
    UPDATE = "update"


@dataclass
class DeviceState:
    battery_pct: Optional[int]
    firmware_version: str
    sync_status: str
    storage_used_bytes: int
    storage_free_bytes: int
    current_level: Optional[CaptureLevel]
    kill_switch_camera: bool
    audio_paused: bool
    operating_mode: OperatingMode
    worn: Optional[bool]  # None = UNAVAILABLE
