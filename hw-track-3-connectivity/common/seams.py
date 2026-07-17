"""
R4: All cross-daemon access goes through this module.
No daemon may import another daemon's internals — only these interfaces.
This is the visible seam for a future policy/permissions layer.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from common.types import (
    CaptureEvent,
    CaptureLevel,
    DeviceState,
    EncryptedBlob,
    OperatingMode,
    SensorReading,
    SensorUnavailableReason,
)


class IEncryptionDaemon(ABC):
    """Interface to HW-2 encryption daemon."""

    @abstractmethod
    def encrypt_and_sign(self, raw: bytes, meta: dict) -> EncryptedBlob:
        ...

    @abstractmethod
    def verify(self, blob: EncryptedBlob) -> bool:
        ...

    @abstractmethod
    def decrypt(self, blob: EncryptedBlob) -> bytes:
        ...

    @abstractmethod
    def is_ready(self) -> bool:
        ...


class ISensorProvider(ABC):
    """Interface to HW-1 sensor capture daemon."""

    @abstractmethod
    def get_worn_state(self) -> SensorReading:
        """R3: returns UNAVAILABLE reading, never bool coercion."""
        ...

    @abstractmethod
    def get_capture_level(self) -> Optional[CaptureLevel]:
        ...

    @abstractmethod
    def next_capture_event(self) -> Optional[CaptureEvent]:
        ...

    @abstractmethod
    def get_audio_chunk(self) -> SensorReading:
        ...

    @abstractmethod
    def get_camera_frame(self) -> SensorReading:
        ...

    @abstractmethod
    def get_imu_batch(self) -> SensorReading:
        ...

    @abstractmethod
    def get_ppg_batch(self) -> SensorReading:
        ...


class IDeviceStateProvider(ABC):
    """Interface for reading device state — used by BLE daemon via seams."""

    @abstractmethod
    def get_state(self) -> DeviceState:
        ...

    @abstractmethod
    def set_kill_switch_camera(self, enabled: bool) -> None:
        ...

    @abstractmethod
    def set_audio_paused(self, paused: bool, duration_s: Optional[int] = None) -> None:
        ...

    @abstractmethod
    def set_operating_mode(self, mode: OperatingMode) -> None:
        ...


class IStorageManager(ABC):
    """Interface to the storage daemon — used by cloud gateway seam."""

    @abstractmethod
    def confirm_server_receipt(self, record_id: str, server_sha256: str) -> None:
        ...

    @abstractmethod
    def delete_if_confirmed(self, record_id: str) -> bool:
        ...
