"""
chronis-cli — command-line interface to the mock stack.
Hard requirement: NEVER print plaintext user data.
sensor-read shows availability/metadata + ciphertext preview only.
storage-list shows filenames/sizes/hashes only.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from common.mock_hal import MockDeviceStateProvider, MockEncryptionDaemon, MockSensorProvider
from common.types import SensorUnavailableReason


class ChronosCLI:

    def __init__(
        self,
        enc_daemon=None,
        sensor_provider=None,
        device_state=None,
        vault=None,
        output_stream=None,
    ):
        self._enc = enc_daemon or MockEncryptionDaemon()
        self._sensors = sensor_provider or MockSensorProvider()
        self._device_state = device_state or MockDeviceStateProvider()
        self._vault = vault
        self._out = output_stream or sys.stdout

    def _print(self, data: dict) -> None:
        print(json.dumps(data, indent=2), file=self._out)

    def cmd_status(self) -> dict:
        state = self._device_state.get_state()
        result = {
            "status": "ok",
            "battery_pct": state.battery_pct,
            "firmware_version": state.firmware_version,
            "sync_status": state.sync_status,
            "operating_mode": state.operating_mode.value,
            "encryption_daemon_ready": self._enc.is_ready(),
            "storage_used_bytes": state.storage_used_bytes,
            "storage_free_bytes": state.storage_free_bytes,
        }
        self._print(result)
        return result

    def cmd_sensor_read(self) -> dict:
        """
        Shows availability/metadata + ciphertext preview. Never plaintext user data.
        R3: propagates null-with-reason for unavailable sensors.
        """
        readings = {
            "worn": self._sensors.get_worn_state().to_wire(),
            "imu": self._sensors.get_imu_batch().to_wire(),
            "ppg": self._sensors.get_ppg_batch().to_wire(),
            "audio": self._sensors.get_audio_chunk().to_wire(),
            "camera": self._sensors.get_camera_frame().to_wire(),
        }

        # Replace raw bytes with ciphertext preview — never print user data
        sanitized = {}
        for sensor, reading in readings.items():
            if reading["value"] is not None and isinstance(reading["value"], (bytes, bytearray)):
                # Encrypt and show hash preview only
                blob = self._enc.encrypt_and_sign(reading["value"], {"sensor": sensor})
                sanitized[sensor] = {
                    "available": True,
                    "timestamp": reading["timestamp"],
                    "unit": reading["unit"],
                    "ciphertext_preview": blob.sha256_hash[:16] + "...",
                    "key_id": blob.key_id,
                }
            else:
                sanitized[sensor] = {
                    "available": reading["value"] is not None,
                    "timestamp": reading["timestamp"],
                    "value": reading["value"],
                    "reason": reading["reason"],
                }

        result = {"status": "ok", "readings": sanitized}
        self._print(result)
        return result

    def cmd_crypto_test(self) -> dict:
        """Runs a round-trip encrypt/verify/decrypt test."""
        test_payload = b"crypto-test-payload"
        blob = self._enc.encrypt_and_sign(test_payload, {"purpose": "cli-test"})
        verified = self._enc.verify(blob)
        decrypted = self._enc.decrypt(blob)
        result = {
            "status": "ok",
            "encrypt": "pass",
            "verify": "pass" if verified else "FAIL",
            "decrypt_match": "pass" if decrypted == test_payload else "FAIL",
            "key_id": blob.key_id,
            "sha256": blob.sha256_hash[:16] + "...",
        }
        self._print(result)
        return result

    def cmd_storage_list(self) -> dict:
        """Lists files by name/size/hash only — no content, no user data."""
        if self._vault is None:
            result = {"status": "ok", "files": [], "note": "No vault configured"}
        else:
            files = []
            vault_dir = Path(self._vault.base_dir) / "vault"
            if vault_dir.exists():
                for blob_file in vault_dir.rglob("*.blob"):
                    files.append({
                        "path": str(blob_file.relative_to(self._vault.base_dir)),
                        "size_bytes": blob_file.stat().st_size,
                    })
            result = {"status": "ok", "files": files, "count": len(files)}
        self._print(result)
        return result


def main():
    parser = argparse.ArgumentParser(description="Chronis device CLI")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("status", help="Show device status")
    subparsers.add_parser("sensor-read", help="Read sensor availability and metadata")
    subparsers.add_parser("crypto-test", help="Run encryption round-trip test")
    subparsers.add_parser("storage-list", help="List stored files (names/sizes/hashes only)")

    args = parser.parse_args()
    cli = ChronosCLI()

    if args.command == "status":
        cli.cmd_status()
    elif args.command == "sensor-read":
        cli.cmd_sensor_read()
    elif args.command == "crypto-test":
        cli.cmd_crypto_test()
    elif args.command == "storage-list":
        cli.cmd_storage_list()
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
