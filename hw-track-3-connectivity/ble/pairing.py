"""
BLE Numeric Comparison pairing flow.
Generates a 6-digit code; requires explicit match confirmation.
Never falls back to Just Works.
"""
from __future__ import annotations

import os
from enum import Enum
from typing import Optional


class PairingState(str, Enum):
    IDLE = "idle"
    PENDING_CONFIRMATION = "pending_confirmation"
    BONDED = "bonded"
    ABORTED = "aborted"


class PairingError(Exception):
    pass


class PairingSession:
    """Numeric Comparison pairing session."""

    def __init__(self):
        self._state = PairingState.IDLE
        self._code: Optional[str] = None
        self._bond_address: Optional[str] = None

    @property
    def state(self) -> PairingState:
        return self._state

    @property
    def code(self) -> Optional[str]:
        return self._code

    def start(self, peer_address: str) -> str:
        """Begin pairing — generates a 6-digit code to display on both sides."""
        if self._state not in (PairingState.IDLE, PairingState.ABORTED):
            raise PairingError(f"Cannot start pairing from state {self._state}")
        self._code = str(int.from_bytes(os.urandom(3), "big") % 1_000_000).zfill(6)
        self._state = PairingState.PENDING_CONFIRMATION
        self._bond_address = peer_address
        return self._code

    def confirm(self, user_code: str) -> None:
        """
        User confirms the code displayed on both devices.
        Mismatch → abort. No fallback to Just Works.
        """
        if self._state != PairingState.PENDING_CONFIRMATION:
            raise PairingError(f"Not awaiting confirmation (state={self._state})")
        if user_code != self._code:
            self._state = PairingState.ABORTED
            self._code = None
            raise PairingError(
                "Numeric Comparison failed — codes do not match. "
                "Pairing aborted. No bond stored. No fallback to Just Works."
            )
        self._state = PairingState.BONDED

    def abort(self) -> None:
        self._state = PairingState.ABORTED
        self._code = None

    def reset(self) -> None:
        self._state = PairingState.IDLE
        self._code = None
        self._bond_address = None

    @property
    def is_bonded(self) -> bool:
        return self._state == PairingState.BONDED

    @property
    def bonded_address(self) -> Optional[str]:
        return self._bond_address if self.is_bonded else None
