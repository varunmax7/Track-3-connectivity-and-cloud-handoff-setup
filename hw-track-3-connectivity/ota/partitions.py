"""
OTA partition management.
slot_a / slot_b in /system/firmware/.
Marks active slot only after verification.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

Slot = Literal["slot_a", "slot_b"]


class PartitionManager:

    def __init__(self, firmware_dir: Path):
        self.firmware_dir = Path(firmware_dir)
        self._slot_a = self.firmware_dir / "slot_a"
        self._slot_b = self.firmware_dir / "slot_b"
        self._slot_a.mkdir(parents=True, exist_ok=True)
        self._slot_b.mkdir(parents=True, exist_ok=True)
        self._state_file = self.firmware_dir / "partition_state.json"
        self._state = self._load_state()

    def _load_state(self) -> dict:
        if self._state_file.exists():
            return json.loads(self._state_file.read_text())
        return {
            "active_slot": "slot_a",
            "boot_attempts": 0,
            "slot_a_version": None,
            "slot_b_version": None,
        }

    def _save_state(self):
        self._state_file.write_text(json.dumps(self._state, indent=2))

    @property
    def active_slot(self) -> Slot:
        return self._state["active_slot"]

    @property
    def inactive_slot(self) -> Slot:
        return "slot_b" if self.active_slot == "slot_a" else "slot_a"

    def inactive_slot_dir(self) -> Path:
        return self.firmware_dir / self.inactive_slot

    def active_slot_dir(self) -> Path:
        return self.firmware_dir / self.active_slot

    def write_to_inactive(self, image_bytes: bytes, version: str) -> Path:
        dest = self.inactive_slot_dir() / "firmware.bin"
        dest.write_bytes(image_bytes)
        self._state[f"{self.inactive_slot}_version"] = version
        self._save_state()
        return dest

    def mark_inactive_active(self) -> None:
        """Promote inactive slot to active — call only after verification."""
        new_active = self.inactive_slot
        self._state["active_slot"] = new_active
        self._state["boot_attempts"] = 0
        self._save_state()

    def increment_boot_attempts(self) -> int:
        self._state["boot_attempts"] += 1
        self._save_state()
        return self._state["boot_attempts"]

    def reset_boot_attempts(self) -> None:
        self._state["boot_attempts"] = 0
        self._save_state()

    @property
    def boot_attempts(self) -> int:
        return self._state["boot_attempts"]

    def active_version(self) -> str | None:
        return self._state.get(f"{self.active_slot}_version")

    def inactive_version(self) -> str | None:
        return self._state.get(f"{self.inactive_slot}_version")
