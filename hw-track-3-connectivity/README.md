# Chronis — Track HW-3: Connectivity, Storage & Cloud Handoff

This track implements the device-side infrastructure and cloud backend for the Chronis wearable.
Everything here runs and tests on a plain Linux or macOS development machine — no physical hardware, no Bluetooth radio, no real chips required.
Where a real component is needed, a mock stands in behind a clean interface so the real driver is a one-file swap when hardware arrives.

---

## Repository layout

```
hw-track-3-connectivity/
├── common/
│   ├── types.py          -- EncryptedBlob, SensorReading (with UNAVAILABLE), DeviceState, events
│   ├── mock_hal.py       -- stand-ins for HW-1 (sensors) and HW-2 (encryption) interfaces
│   └── seams.py          -- R4 cross-daemon access interfaces (IEncryptionDaemon, ISensorProvider, ...)
├── storage/
│   ├── vault.py          -- vault_write(), plaintext log writer, vault tree management
│   ├── deletion.py       -- double-confirmation deletion flow
│   ├── append_only.py    -- AppendOnlyStore: raises AppendOnlyViolation on overwrite
│   └── tests/
├── provisioning/
│   ├── harden.sh         -- idempotent network hardening script (firewall + SSH)
│   └── tests/
├── ota/
│   ├── receiver.py       -- signature verification, hash check, partition download
│   ├── partitions.py     -- slot_a / slot_b partition management
│   ├── rollback.py       -- boot-attempt counter, automatic 3-fail rollback
│   └── tests/
├── ble/
│   ├── gatt_model.py     -- single source of truth for all 8 GATT service definitions
│   ├── mock_peripheral.py -- fixed-response fake peripheral for phone-app team to pair against
│   ├── daemon.py         -- real on-device BLE daemon, reads live mocked state via seams.py
│   ├── pairing.py        -- Numeric Comparison pairing flow (6-digit code, no Just Works)
│   └── tests/
├── orchestration/
│   ├── units/            -- declarative service unit definitions (.unit files)
│   ├── supervisor.py     -- startup ordering, readiness checks, restart policy
│   └── tests/
├── cli/
│   ├── chronis_cli.py    -- debug CLI: status, sensor-read, crypto-test, storage-list
│   └── tests/
├── cloud/
│   ├── gateway.py        -- FastAPI ingestion endpoint: verify -> decrypt -> canonical DB -> receipt
│   ├── canonical_db.py   -- append-only SQLite store with hash-chain and DB-level triggers
│   └── tests/
├── e2e/
│   └── test_full_pipeline.py -- full loop: sensor -> encrypt -> vault -> gateway -> DB -> deletion
├── docs/
│   └── COMPONENT_SPEC_LIST.md
├── conftest.py
└── pyproject.toml
```

---

## Non-negotiable rules enforced in code

These four rules are structural, not comments. Violating any of them fails the sprint gate.

| Rule | Requirement | Where enforced |
|---|---|---|
| R1 | No write to disk without going through the encryption daemon first. `vault_write()` accepts only `EncryptedBlob` — raw bytes raise `TypeError` at the call site. | `storage/vault.py`, `common/types.py` |
| R2 | The canonical record is append-only. Any attempt to overwrite an existing record raises `AppendOnlyViolation`. Enforced both in the mock filesystem and at the SQLite trigger level. | `storage/append_only.py`, `cloud/canonical_db.py` |
| R3 | A sensor with no data reports "no data", never a fake zero. `SensorReading` carries an explicit `UNAVAILABLE` state. Consumers propagate `{"value": null, "reason": "..."}` on the wire. | `common/types.py`, all consumers |
| R4 | No daemon reaches directly into another daemon's data. All cross-daemon access goes through `common/seams.py`. No module imports another daemon's internals. | `common/seams.py`, enforced by import structure |

---

## Prerequisites

- Python 3.11 or later (tested on 3.11 through 3.14)
- No system packages required beyond a standard Python install

---

## Setup

Create a virtual environment and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Or, if a `.venv` already exists (as in this repo):

```bash
source .venv/bin/activate
```

---

## Running the tests

Run the full suite from the `hw-track-3-connectivity/` directory:

```bash
.venv/bin/pytest -v
```

Quick summary output:

```bash
.venv/bin/pytest -q
```

Run a single module's tests:

```bash
.venv/bin/pytest storage/tests/ -v
.venv/bin/pytest ble/tests/ -v
.venv/bin/pytest cloud/tests/ -v
.venv/bin/pytest e2e/ -v
```

Run with coverage:

```bash
.venv/bin/pytest --cov=. --cov-report=term-missing
```

Expected result: **91 passed**.

---

## Module documentation

### common/

**`types.py`**

Defines the core types shared across all modules:

- `EncryptedBlob` — the only type `vault_write()` accepts (R1). Fields: `ciphertext`, `iv` (12-byte AES-GCM nonce), `tag` (16-byte auth tag), `signature`, `key_id`, `session_id`, `sha256_hash`, `metadata`. The constructor validates field types and verifies the SHA-256 hash against the ciphertext on construction — a mismatched hash raises `ValueError` immediately.
- `SensorReading` — models R3. Has `.available(sensor_id, ts, value)` and `.unavailable(sensor_id, ts, reason)` constructors. `.to_wire()` always emits `{"value": null, "reason": "..."}` when unavailable, never a zero.
- `SensorUnavailableReason` — enum: `NOT_WORN`, `HARDWARE_FAULT`, `INITIALIZING`, `LOW_POWER`, `UNKNOWN`.
- `DeviceState` — snapshot of device status queried by the BLE daemon: battery, firmware version, sync status, storage usage, capture level, kill-switch states, operating mode, worn state.
- `CaptureEvent`, `AlertEvent`, `CaptureLevel` (L0–L5), `OperatingMode` — supporting types.

**`seams.py`**

R4 enforcement. Defines abstract base classes that are the only permitted channel for cross-daemon access:

- `IEncryptionDaemon` — `encrypt_and_sign()`, `verify()`, `decrypt()`, `is_ready()`
- `ISensorProvider` — `get_worn_state()`, `get_capture_level()`, `next_capture_event()`, sensor frame getters
- `IDeviceStateProvider` — `get_state()`, `set_kill_switch_camera()`, `set_audio_paused()`, `set_operating_mode()`
- `IStorageManager` — `confirm_server_receipt()`, `delete_if_confirmed()`

**`mock_hal.py`**

Temporary stand-ins for HW-1 and HW-2 output. All marked `# TEMPORARY STAND-IN` in code. Implements all four seam interfaces using real AES-GCM + RSA-2048 crypto under a mock chip interface (stand-in for ATECC608B). The key hierarchy mirrors the spec: Device Identity Key generated once, daily-derived Data Session Key (never persisted), per-upload-session Server Transport Key.

---

### storage/

**Vault tree**

All writes into `/vault/**` are routed through a single function: `vault_write(blob: EncryptedBlob, category, date) -> RecordId`. The tree structure is:

```
/vault/YYYY-MM-DD/audio/
/vault/YYYY-MM-DD/camera/
/vault/YYYY-MM-DD/sensors/imu/
/vault/YYYY-MM-DD/sensors/ppg/
/vault/YYYY-MM-DD/metadata.json
/vault/YYYY-MM-DD/manifest.sha
/system/logs/        -- plaintext only, never user data
/system/config/      -- device config, public key, sync state
/system/firmware/    -- current + previous version (rollback)
```

The base directory is configurable (`tmp_path` in tests, any path in production). `/system/logs/` has a separate plaintext writer that refuses payloads tagged as user data.

**`vault.py`** — `vault_write()`, log writer, filesystem tree initialization.

**`append_only.py`** — `AppendOnlyStore`: records record IDs in a manifest. Any second write with the same ID raises `AppendOnlyViolation`. Used for `manifest.sha` and metadata records.

**`deletion.py`** — `DeletionManager`: a file is deletable only when both conditions are met:
1. Device-side upload-complete confirmation recorded for that file.
2. Server-side receipt confirmation with a matching SHA-256 hash on both sides.

Anything else is refused and logged. There is no force-delete path.

---

### provisioning/

**`harden.sh`**

Idempotent bash script that configures network security. Supports `--dry-run` mode for testing without root.

- Firewall (ufw): default deny incoming, allow SSH with rate limiting, allow the gateway upload port.
- SSH: `PasswordAuthentication no`, `PubkeyAuthentication yes`, `PermitRootLogin no`, `ChallengeResponseAuthentication no`.

The gateway port defaults to `8443` and can be overridden with `--gateway-port <PORT>`.

Run dry-run:

```bash
bash provisioning/harden.sh --dry-run
```

---

### ota/

Over-the-air update receiver. Fully testable with plain files.

**`receiver.py`** — downloads firmware image into the inactive partition slot, verifies RSA-2048 signature against a signed manifest, checks SHA-256 integrity, marks the slot active only after verification passes. Emits an `AlertEvent` on signature failure.

**`partitions.py`** — manages `slot_a` and `slot_b` under `/system/firmware/`. Tracks which slot is active.

**`rollback.py`** — `BootAttemptTracker`: increments a counter on each boot. After 3 consecutive failed boots on the new firmware, automatically reverts the active slot to the previous version.

Failure paths with explicit tests:
- Bad signature: update rejected, phone alert emitted, partition not written.
- Three failed boots: automatic revert to the previous working slot.

---

### ble/

**`gatt_model.py`**

Single source of truth for all 8 GATT service definitions. Both the mock peripheral and the real daemon consume the same definitions. Services:

1. Device Info — battery %, firmware version, sync status, storage used/available, capture level, operating mode, kill-switch state, audio pause state
2. LED Control — per-zone color, pattern (static/pulse/chase/flash/custom), brightness 0–100, on/off schedule
3. Display Control — push short message, upload watch-face image
4. Camera Control — kill-switch status (read-only), current frame rate
5. Audio Control — pause (with duration), resume, current state
6. Config — WiFi credentials (write-only, encrypted in transit), sync schedule, operating mode, display/notification prefs
7. Alerts (device-to-phone only) — sync complete, low battery, storage warning, sensor disconnect, tamper detected, new insight ready, double-tap moment marked, mode change confirmed, boot complete
8. Annotation — receive a text note from the phone, attach to the nearest double-tap timestamp

**`mock_peripheral.py`**

Standalone fake peripheral for the phone-app team to pair against. Returns scriptable fixed responses. This is a test double — it is not the device logic.

**`daemon.py`**

The actual on-device BLE daemon:
- Handlers for all 8 services, reading live mocked device state through `seams.py` (R4).
- Auto-reconnect: maintains bond with last-paired phone, reconnects within 10 seconds of a connection drop. Uses injected clock — tests never sleep.
- Beacon mode: when unconnected, advertises only device name and battery percentage at 1 Hz. No user data fields in the advertisement.
- Disconnect monitoring: if disconnected more than 30 minutes while worn-detector says "worn", logs a flagged event for delivery on next reconnect. If worn state is `UNAVAILABLE`, flags differently (R3 — not treated as worn or not-worn).

**`pairing.py`**

Numeric Comparison pairing flow. Generates a 6-digit code, simulates display on device and phone, requires explicit confirmation before bond completes. Failed match aborts pairing safely — no bond stored, no fallback to Just Works.

---

### orchestration/

**`supervisor.py`**

Reads declarative unit files from `orchestration/units/`. Each unit defines: name, command, dependencies, readiness check, restart policy.

Ordering invariant: the encryption daemon starts first. Nothing else launches until it reports ready. If the encryption daemon dies mid-run, the supervisor halts all other services (mirrors HW-2 watchdog HALT semantics).

Unit file format (`.unit`):

```ini
[Unit]
Name = ble-daemon
Command = python -m ble.daemon
Requires = encryption-daemon
ReadinessCheck = tcp:localhost:9001
RestartPolicy = always
```

---

### cli/

**`chronis_cli.py`**

Debug command-line tool. All commands run against the mock stack.

```bash
python -m cli.chronis_cli status
python -m cli.chronis_cli sensor-read
python -m cli.chronis_cli crypto-test
python -m cli.chronis_cli storage-list
```

Privacy guarantee: no command prints plaintext user data to stdout. `sensor-read` shows availability metadata and a ciphertext preview. `storage-list` shows filenames, sizes, and hashes only. The test suite captures stdout for every command and asserts zero leakage against known plaintext fixtures.

---

### cloud/

**`gateway.py`**

FastAPI ingestion gateway.

Endpoint: `POST /ingest`

Flow:
1. Reconstruct `EncryptedBlob` from the request payload.
2. Verify RSA signature via the encryption daemon interface.
3. Decrypt using server-side transport key logic.
4. Build a structured event with session ID, key ID, payload, and receipt timestamp.
5. Insert into the canonical DB.
6. Return the server-side SHA-256 receipt — this is the confirmation the deletion flow (storage/deletion.py) requires to unlock local deletion.

Responds `400` on bad signature or malformed payload. Responds `409` on duplicate session ID.

Health check: `GET /health`

**`canonical_db.py`**

Append-only SQLite store. Table schema:

```
id, device_id, ts, event_type, payload_json, hash, prev_hash
```

- `BEFORE UPDATE` trigger: `RAISE(ABORT)` — update is impossible at the DB level.
- `BEFORE DELETE` trigger: `RAISE(ABORT)` — deletion is impossible at the DB level.
- Hash chain: each record's `hash` field covers its own content; `prev_hash` links to the previous record, providing tamper evidence.

---

### e2e/

**`test_full_pipeline.py`**

The sprint's connective proof. Runs the complete loop:

```
synthetic sensor data
  -> capture-intensity decision (mock HW-1)
  -> encrypt + sign (mock HW-2)
  -> vault_write (storage)
  -> POST /ingest (cloud gateway)
  -> signature verify
  -> decrypt
  -> structured event
  -> canonical DB row
  -> server SHA-256 receipt
  -> deletion flow now permits local delete
```

Assertions across the loop:
- Zero R1 through R4 violations.
- SHA-256 hash matches end-to-end (device ciphertext hash equals server receipt hash).
- DB rows are append-only (UPDATE and DELETE both raise at the DB level).
- Local deletion is locked until both device-side and server-side confirmations are present and hashes match.

---

## Running the cloud gateway manually

To bring up the FastAPI gateway as a local server for manual testing:

```bash
source .venv/bin/activate
uvicorn cloud.gateway:app --reload --port 8443
```

The gateway is not pre-configured with a daemon instance when started this way. Wire it via `cloud.gateway.configure()` in your startup code or use the test client in `cloud/tests/` as a reference.

---

## Continuous integration

`.github/workflows/ci.yml` runs on every push and pull request:

1. Lint with `ruff`
2. Full pytest suite across all modules and e2e, with `--tb=short` and fail-fast disabled so all module failures are reported together

Python version matrix: 3.11 minimum.

---

## Mock hardware — what stands in for what

| Real component | Interface | Mock location |
|---|---|---|
| ATECC608B (secure element) | I2C / Single-Wire | `common/mock_hal.py` → `MockEncryptionDaemon` |
| ICM-42688-P (IMU) | SPI / I2C | `common/mock_hal.py` → `MockSensorProvider.get_imu_batch()` |
| MAX30102 (PPG) | I2C | `common/mock_hal.py` → `MockSensorProvider.get_ppg_batch()` |
| IMX219 (camera) | MIPI CSI-2 | `common/mock_hal.py` → `MockSensorProvider.get_camera_frame()` |
| DS3231 (RTC) | I2C | `time.time()` injected as `clock_fn` in all daemons |
| Bluetooth radio | BLE | Unix domain socket / in-process message bus |
| Radxa Zero 3W | — | Host machine running Python 3.11+ |

All mock stand-ins are marked `# TEMPORARY STAND-IN for <component>` in code. When real hardware arrives, only the driver files in `common/mock_hal.py` get replaced — no logic in any other module changes.

---

## What this track does not cover

- Real Bluetooth RF behavior, range, or pairing UX on a physical device
- Real I2C/SPI bus timing, address conflicts, or signal integrity
- Vendor selection or component pricing
- The policy/permissions layer behind the R4 seams (the seam exists, the layer is a future concern)
- Production deployment of the gateway (local run only in this sprint)
