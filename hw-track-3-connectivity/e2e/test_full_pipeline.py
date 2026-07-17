"""
End-to-end pipeline test — the sprint's connective proof.

synthetic sensor data → capture-intensity decision (mock HW-1)
  → encrypt+sign (mock HW-2) → vault_write → "upload" to gateway
  → verify → decrypt → structured event → canonical DB row
  → server receipt → deletion flow now permits local delete

Asserts:
  - zero R1–R4 violations
  - hash matches end-to-end
  - DB rows are append-only
  - deletion only unlocked after dual confirmation
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cloud.canonical_db import CanonicalDB
from cloud.gateway import app, configure
from common.mock_hal import MockDeviceStateProvider, MockEncryptionDaemon, MockSensorProvider
from common.types import CaptureLevel
from storage.deletion import DeletionManager, DeletionRefused
from storage.vault import Vault


@pytest.fixture
def enc():
    return MockEncryptionDaemon()


@pytest.fixture
def sensors():
    s = MockSensorProvider()
    s.set_capture_level(CaptureLevel.L3)
    s.set_worn(True)
    return s


@pytest.fixture
def vault(tmp_path):
    return Vault(tmp_path)


@pytest.fixture
def db():
    return CanonicalDB(":memory:")


@pytest.fixture
def client(enc, db):
    configure(enc, db)
    return TestClient(app)


class TestFullPipeline:
    def test_multi_event_session(self, enc, sensors, vault, db, client):
        """
        Runs the full loop for multiple sensor events in one session.
        Verifies all rules end-to-end.
        """
        dm = DeletionManager(vault)
        record_ids = []
        sensor_types = ["imu", "ppg", "audio"]

        for i, sensor_type in enumerate(sensor_types):
            # Step 1: Get synthetic sensor data (mock HW-1)
            if sensor_type in ("imu", "ppg"):
                reading = getattr(sensors, f"get_{sensor_type}_batch")()
            else:
                reading = getattr(sensors, f"get_{sensor_type}_chunk")()
            assert reading.is_available, f"Sensor {sensor_type} should be available"
            raw_bytes = reading.value

            # Step 2: Encrypt+sign (mock HW-2) — R1: only EncryptedBlob can be written
            blob = enc.encrypt_and_sign(raw_bytes, {
                "session_id": f"e2e-sess-{i}",
                "date": "2024-01-01",
                "device_id": "e2e-device",
                "event_type": f"{sensor_type}_batch",
            })

            # Step 3: vault_write — R1 enforced: only EncryptedBlob accepted
            record_id = vault.vault_write(blob, sensor_type if sensor_type in ("imu", "ppg") else sensor_type, day="2024-01-01")
            record_ids.append((record_id, blob))

            # Step 4: Upload to gateway (verify + decrypt + canonical DB)
            payload = {
                "ciphertext": blob.ciphertext.hex(),
                "iv": blob.iv.hex(),
                "tag": blob.tag.hex(),
                "signature": blob.signature.hex(),
                "key_id": blob.key_id,
                "session_id": blob.session_id,
                "sha256_hash": blob.sha256_hash,
                "metadata": blob.metadata,
            }
            resp = client.post("/ingest", json=payload)
            assert resp.status_code == 200, f"Gateway rejected: {resp.json()}"
            gateway_data = resp.json()

            # Step 5: Server receipt — hash must match device-side hash
            server_sha256 = gateway_data["server_sha256"]
            device_sha256 = blob.sha256_hash
            assert server_sha256 == device_sha256, "Hash mismatch end-to-end!"

            # Step 6: Deletion flow — must require BOTH confirmations
            dm.confirm_device_upload(str(record_id), device_sha256)

            # Not yet deletable (no server confirmation yet)
            assert not dm.can_delete(str(record_id))
            with pytest.raises(DeletionRefused):
                dm.delete(str(record_id))

            dm.confirm_server_receipt(str(record_id), server_sha256)

            # Now deletable
            assert dm.can_delete(str(record_id))
            dm.delete(str(record_id))

        # Step 7: DB rows are append-only — confirm all records present and immutable
        db_records = db.all_records()
        assert len(db_records) == len(sensor_types)

        # Verify hash chain integrity
        for idx, rec in enumerate(db_records):
            if idx == 0:
                assert rec["prev_hash"] is None
            else:
                assert rec["prev_hash"] == db_records[idx - 1]["hash"]

        # R2: UPDATE attempt must fail
        import sqlite3
        with pytest.raises((sqlite3.OperationalError, sqlite3.IntegrityError), match="append-only"):
            db._conn.execute(
                "UPDATE canonical_records SET event_type = 'tampered' WHERE id = 1"
            )

        # R2: DELETE attempt must fail
        with pytest.raises((sqlite3.OperationalError, sqlite3.IntegrityError), match="append-only"):
            db._conn.execute("DELETE FROM canonical_records WHERE id = 1")

    def test_r1_raw_bytes_rejected_at_vault(self, vault):
        """R1: Raw bytes cannot reach vault_write."""
        with pytest.raises(TypeError, match="EncryptedBlob"):
            vault.vault_write(b"raw sensor data", "audio")

    def test_r3_unavailable_propagates_null_with_reason(self, sensors):
        """R3: UNAVAILABLE propagates as null-with-reason, never zero."""
        sensors.set_worn(None)
        reading = sensors.get_worn_state()
        wire = reading.to_wire()
        assert wire["value"] is None
        assert wire["reason"] is not None
        # Verify it's not zero
        assert wire["value"] != 0
        assert wire["value"] != False

    def test_r4_no_direct_daemon_imports(self):
        """R4: Cross-daemon access goes through seams only."""
        import ast
        import sys
        from pathlib import Path

        root = Path(__file__).parent.parent
        violations = []
        daemon_modules = ["storage", "ble", "cloud", "orchestration", "cli"]

        for daemon in daemon_modules:
            daemon_dir = root / daemon
            if not daemon_dir.exists():
                continue
            for py_file in daemon_dir.rglob("*.py"):
                if "tests" in py_file.parts:
                    continue
                source = py_file.read_text()
                for other_daemon in daemon_modules:
                    if other_daemon == daemon:
                        continue
                    # Direct import of another daemon's internals (not through seams/common)
                    bad_patterns = [
                        f"from {other_daemon}.",
                        f"import {other_daemon}.",
                    ]
                    for pattern in bad_patterns:
                        if pattern in source:
                            violations.append(
                                f"{py_file.relative_to(root)}: imports {other_daemon} directly"
                            )

        assert violations == [], f"R4 violations:\n" + "\n".join(violations)

    def test_deletion_requires_hash_match(self, enc, vault, db, client):
        """Deletion must be refused on hash mismatch even with both confirmations."""
        dm = DeletionManager(vault)
        raw = b"sensor payload for hash mismatch test"
        blob = enc.encrypt_and_sign(raw, {
            "session_id": "hash-test",
            "date": "2024-01-01",
            "device_id": "device-hash",
        })
        record_id = vault.vault_write(blob, "audio", day="2024-01-01")

        dm.confirm_device_upload(str(record_id), blob.sha256_hash)
        dm.confirm_server_receipt(str(record_id), "wronghash" * 8)

        with pytest.raises(DeletionRefused, match="mismatch"):
            dm.delete(str(record_id))
