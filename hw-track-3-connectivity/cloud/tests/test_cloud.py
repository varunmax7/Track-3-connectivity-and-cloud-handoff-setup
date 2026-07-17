"""Cloud gateway and canonical DB tests."""
from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from cloud.canonical_db import CanonicalDB
from cloud.gateway import app, configure
from common.mock_hal import MockEncryptionDaemon


@pytest.fixture
def enc():
    return MockEncryptionDaemon()


@pytest.fixture
def db():
    return CanonicalDB(":memory:")


@pytest.fixture
def client(enc, db):
    configure(enc, db)
    return TestClient(app)


def make_payload(enc, raw=b'{"sensor": "test", "value": 42}', session_id="sess-1"):
    blob = enc.encrypt_and_sign(raw, {
        "session_id": session_id,
        "date": "2024-01-01",
        "device_id": "device-001",
        "event_type": "sensor_data",
    })
    return {
        "ciphertext": blob.ciphertext.hex(),
        "iv": blob.iv.hex(),
        "tag": blob.tag.hex(),
        "signature": blob.signature.hex(),
        "key_id": blob.key_id,
        "session_id": blob.session_id,
        "sha256_hash": blob.sha256_hash,
        "metadata": blob.metadata,
    }, blob


# ── Gateway tests ────────────────────────────────────────────────────────────

class TestGateway:
    def test_health_check(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_valid_ingest_returns_200(self, client, enc):
        payload, blob = make_payload(enc)
        resp = client.post("/ingest", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "record_id" in data
        assert "server_sha256" in data

    def test_server_sha256_matches_ciphertext(self, client, enc):
        import hashlib
        payload, blob = make_payload(enc)
        resp = client.post("/ingest", json=payload)
        data = resp.json()
        expected = hashlib.sha256(blob.ciphertext).hexdigest()
        assert data["server_sha256"] == expected

    def test_bad_signature_returns_400(self, client, enc):
        import os
        payload, blob = make_payload(enc)
        payload["signature"] = os.urandom(256).hex()
        resp = client.post("/ingest", json=payload)
        assert resp.status_code == 400
        assert "Signature" in resp.json()["detail"]

    def test_malformed_payload_returns_400(self, client):
        resp = client.post("/ingest", json={"bad": "data"})
        assert resp.status_code == 422  # pydantic validation error

    def test_record_saved_to_canonical_db(self, client, enc, db):
        payload, blob = make_payload(enc, session_id="sess-db-test")
        resp = client.post("/ingest", json=payload)
        assert resp.status_code == 200
        records = db.all_records()
        assert len(records) == 1

    def test_duplicate_session_id_rejected(self, client, enc):
        payload, _ = make_payload(enc, session_id="dup-session")
        resp1 = client.post("/ingest", json=payload)
        assert resp1.status_code == 200
        resp2 = client.post("/ingest", json=payload)
        # Second insertion of same record_id must be rejected (unique constraint)
        assert resp2.status_code == 409


# ── Canonical DB tests ────────────────────────────────────────────────────────

class TestCanonicalDB:
    def test_insert_succeeds(self, db):
        h = db.insert("rec-1", "device-1", "sensor_data", {"x": 1})
        assert h is not None

    def test_update_fails(self, db):
        db.insert("rec-1", "device-1", "sensor_data", {"x": 1})
        conn = db._conn
        # SQLite raises IntegrityError for RAISE(ABORT) in triggers
        with pytest.raises((sqlite3.OperationalError, sqlite3.IntegrityError), match="append-only"):
            conn.execute("UPDATE canonical_records SET event_type = 'hacked' WHERE record_id = 'rec-1'")

    def test_delete_fails(self, db):
        db.insert("rec-1", "device-1", "sensor_data", {"x": 1})
        conn = db._conn
        with pytest.raises((sqlite3.OperationalError, sqlite3.IntegrityError), match="append-only"):
            conn.execute("DELETE FROM canonical_records WHERE record_id = 'rec-1'")

    def test_hash_chain_links_records(self, db):
        db.insert("rec-1", "device-1", "sensor_data", {"x": 1})
        db.insert("rec-2", "device-1", "sensor_data", {"x": 2})
        records = db.all_records()
        assert records[1]["prev_hash"] == records[0]["hash"]

    def test_first_record_has_null_prev_hash(self, db):
        db.insert("rec-1", "device-1", "sensor_data", {"x": 1})
        rec = db.get("rec-1")
        assert rec["prev_hash"] is None
