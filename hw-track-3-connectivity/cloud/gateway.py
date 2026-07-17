"""
FastAPI cloud ingestion gateway.
POST /ingest — verify signature → decrypt → structured event → canonical DB → server SHA-256 receipt.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from typing import Any, Callable, Dict, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from cloud.canonical_db import CanonicalDB
from common.seams import IEncryptionDaemon
from common.types import EncryptedBlob

logger = logging.getLogger(__name__)

app = FastAPI(title="Chronis Cloud Gateway")


class IngestRequest(BaseModel):
    ciphertext: str
    iv: str
    tag: str
    signature: str
    key_id: str
    session_id: str
    sha256_hash: str
    metadata: Dict[str, Any] = {}


class IngestResponse(BaseModel):
    status: str
    record_id: str
    server_sha256: str
    db_row_hash: str


# Module-level injection points (set before serving)
_enc_daemon: Optional[IEncryptionDaemon] = None
_canonical_db: Optional[CanonicalDB] = None
_handoff_fn: Optional[Callable[[str, dict], None]] = None


def configure(
    enc_daemon: IEncryptionDaemon,
    db: CanonicalDB,
    handoff_fn: Optional[Callable[[str, dict], None]] = None,
):
    global _enc_daemon, _canonical_db, _handoff_fn
    _enc_daemon = enc_daemon
    _canonical_db = db
    _handoff_fn = handoff_fn


@app.post("/ingest", response_model=IngestResponse)
async def ingest(payload: IngestRequest):
    if _enc_daemon is None or _canonical_db is None:
        raise HTTPException(status_code=503, detail="Gateway not configured")

    # Reconstruct EncryptedBlob
    try:
        blob = EncryptedBlob(
            ciphertext=bytes.fromhex(payload.ciphertext),
            iv=bytes.fromhex(payload.iv),
            tag=bytes.fromhex(payload.tag),
            signature=bytes.fromhex(payload.signature),
            key_id=payload.key_id,
            session_id=payload.session_id,
            sha256_hash=payload.sha256_hash,
            metadata=payload.metadata,
        )
    except (ValueError, TypeError) as exc:
        logger.warning("Malformed ingest payload: %s", exc)
        raise HTTPException(status_code=400, detail=f"Malformed payload: {exc}")

    # Verify signature
    if not _enc_daemon.verify(blob):
        logger.warning("Signature verification failed for session %s", payload.session_id)
        raise HTTPException(status_code=400, detail="Signature verification failed")

    # Decrypt
    try:
        raw = _enc_daemon.decrypt(blob)
    except Exception as exc:
        logger.error("Decryption failed: %s", exc)
        raise HTTPException(status_code=400, detail="Decryption failed")

    # Build structured event
    device_id = payload.metadata.get("device_id", "unknown")
    event_type = payload.metadata.get("event_type", "sensor_data")
    record_id = f"{device_id}/{payload.session_id}/{payload.sha256_hash[:8]}"

    try:
        structured_payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        structured_payload = {"raw_b64": raw.hex(), "size": len(raw)}

    event = {
        "session_id": payload.session_id,
        "key_id": payload.key_id,
        "payload": structured_payload,
        "received_ts": time.time(),
    }

    # Persist to canonical DB
    import sqlite3
    try:
        db_row_hash = _canonical_db.insert(
            record_id=record_id,
            device_id=device_id,
            event_type=event_type,
            payload=event,
        )
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail=f"Record '{record_id}' already exists")

    # Server-side SHA-256 receipt (matches what deletion flow requires)
    server_sha256 = hashlib.sha256(blob.ciphertext).hexdigest()

    if _handoff_fn:
        _handoff_fn(record_id, event)

    logger.info("Ingested record %s, server_sha256=%s", record_id, server_sha256)

    return IngestResponse(
        status="ok",
        record_id=record_id,
        server_sha256=server_sha256,
        db_row_hash=db_row_hash,
    )


@app.get("/health")
async def health():
    return {"status": "ok"}
