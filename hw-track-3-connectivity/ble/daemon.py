"""
Real BLE daemon — on-device logic.
Reads live device state through seams.py (R4). Never uses static canned values.
Uses injected fake clock for timing tests (no real sleeps).
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from ble.gatt_model import ALL_SERVICES, CHAR_MAP, Service
from ble.pairing import PairingSession, PairingState
from common.seams import IDeviceStateProvider, ISensorProvider
from common.types import SensorUnavailableReason

logger = logging.getLogger(__name__)

RECONNECT_TIMEOUT_S = 10
DISCONNECT_WHILE_WORN_TIMEOUT_S = 30 * 60  # 30 minutes
BEACON_INTERVAL_S = 1.0


@dataclass
class Advertisement:
    device_name: str
    battery_pct: Optional[int]

    def serialize(self) -> dict:
        return {
            "device_name": self.device_name,
            "battery_pct": self.battery_pct,
        }


@dataclass
class BondStore:
    peer_address: Optional[str] = None
    is_bonded: bool = False

    def store(self, address: str) -> None:
        self.peer_address = address
        self.is_bonded = True

    def clear(self) -> None:
        self.peer_address = None
        self.is_bonded = False


class BLEDaemon:
    """
    Device-side BLE logic. All device state reads go through seams (R4).
    Clock is injected for deterministic tests — no real time.sleep() calls.
    """

    def __init__(
        self,
        device_state: IDeviceStateProvider,
        sensor_provider: ISensorProvider,
        clock_fn: Callable[[], float] = time.time,
        device_name: str = "Chronis-1",
    ):
        self._device_state = device_state
        self._sensors = sensor_provider
        self._clock_fn = clock_fn
        self._device_name = device_name
        self._pairing = PairingSession()
        self._bond = BondStore()
        self._connected = False
        self._connected_peer: Optional[str] = None
        self._disconnect_time: Optional[float] = None
        self._pending_alerts: List[dict] = []
        self._annotations: List[dict] = []

    # ── Connection management ───────────────────────────────────────────────

    def on_connect(self, peer_address: str) -> None:
        self._connected = True
        self._connected_peer = peer_address
        self._disconnect_time = None
        logger.info("BLE connected: %s", peer_address)
        self._deliver_pending_alerts()

    def on_disconnect(self, peer_address: str) -> None:
        self._connected = False
        self._connected_peer = None
        self._disconnect_time = self._clock_fn()
        logger.info("BLE disconnected: %s", peer_address)

    def check_reconnect_or_flag(self) -> Optional[dict]:
        """
        Call periodically. Returns a flagged event if disconnected > 30 min while worn.
        Uses injected clock — no sleep.
        """
        if self._connected or self._disconnect_time is None:
            return None

        elapsed = self._clock_fn() - self._disconnect_time
        if elapsed < DISCONNECT_WHILE_WORN_TIMEOUT_S:
            return None

        worn_reading = self._sensors.get_worn_state()
        if worn_reading.value is None:
            # R3: UNAVAILABLE is not the same as worn or not-worn
            event = {
                "type": "disconnect_worn_state_unavailable",
                "elapsed_s": elapsed,
                "reason": worn_reading.reason.value if worn_reading.reason else "unknown",
            }
            self._pending_alerts.append(event)
            return event
        elif worn_reading.value is True:
            event = {
                "type": "disconnect_while_worn",
                "elapsed_s": elapsed,
            }
            self._pending_alerts.append(event)
            return event

        return None

    def should_attempt_reconnect(self) -> bool:
        """Returns True if we should try to reconnect within the 10s window."""
        if self._connected or not self._bond.is_bonded or self._disconnect_time is None:
            return False
        elapsed = self._clock_fn() - self._disconnect_time
        return elapsed <= RECONNECT_TIMEOUT_S

    def _deliver_pending_alerts(self) -> None:
        if self._pending_alerts:
            logger.info("Delivering %d pending alerts", len(self._pending_alerts))
            self._pending_alerts.clear()

    # ── Pairing ─────────────────────────────────────────────────────────────

    def start_pairing(self, peer_address: str) -> str:
        """Returns 6-digit code to display on both sides."""
        return self._pairing.start(peer_address)

    def confirm_pairing(self, user_code: str) -> None:
        """User confirms code. Mismatch → abort, no bond stored."""
        self._pairing.confirm(user_code)
        if self._pairing.is_bonded:
            self._bond.store(self._pairing.bonded_address)
            logger.info("Bond stored for %s", self._bond.peer_address)

    def pairing_state(self) -> PairingState:
        return self._pairing.state

    # ── Beacon ──────────────────────────────────────────────────────────────

    def get_beacon_advertisement(self) -> dict:
        """
        When unconnected, advertise ONLY device name + battery %.
        Zero user data fields. Tests assert this.
        """
        state = self._device_state.get_state()
        adv = Advertisement(
            device_name=self._device_name,
            battery_pct=state.battery_pct,
        )
        return adv.serialize()

    # ── GATT read handlers — all read from seams (R4) ───────────────────────

    def read_characteristic(self, char_name: str) -> dict:
        state = self._device_state.get_state()

        handlers = {
            "BatteryPercent": lambda: {"value": state.battery_pct, "reason": None},
            "FirmwareVersion": lambda: {"value": state.firmware_version},
            "SyncStatus": lambda: {"value": state.sync_status},
            "StorageUsed": lambda: {"value": state.storage_used_bytes},
            "StorageAvailable": lambda: {"value": state.storage_free_bytes},
            "CaptureLevelCurrent": lambda: {"value": state.current_level.value if state.current_level else None},
            "OperatingMode": lambda: {"value": state.operating_mode.value},
            "CameraKillSwitch": lambda: {"value": state.kill_switch_camera},
            "AudioPauseState": lambda: {"value": state.audio_paused},
            "KillSwitchStatus": lambda: {"value": state.kill_switch_camera},
        }

        handler = handlers.get(char_name)
        if handler:
            return handler()
        return {"error": f"Unknown characteristic: {char_name}"}

    def write_characteristic(self, char_name: str, value: any) -> dict:
        if char_name == "PauseWithDuration":
            self._device_state.set_audio_paused(True, duration_s=value)
            return {"status": "ok"}
        elif char_name == "Resume":
            self._device_state.set_audio_paused(False)
            return {"status": "ok"}
        elif char_name == "TextNote":
            self._annotations.append({
                "note": value,
                "ts": self._clock_fn(),
            })
            return {"status": "ok"}
        return {"status": "error", "message": f"Write not handled for {char_name}"}
