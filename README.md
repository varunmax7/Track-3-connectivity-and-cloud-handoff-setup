# Chronis — Track HW-3: Connectivity, Storage & Cloud Handoff

**Implementation spec for a coding agent. Read this entire file before writing any code.**

This is one of three tracks in a simulation-first firmware sprint. **There is no physical hardware.** Every module below must be built and fully tested against mocks on a plain Linux dev machine. If anything appears to require a real chip, real Bluetooth radio, or real board — it doesn't. Mock it behind an interface so the real driver is a later drop-in swap.

---

## 0. Global constraints (non-negotiable — enforced in code structure, not comments)

These four rules apply to every line written in this track. Violating any of them fails the sprint gate.

| Rule | Requirement | How to enforce structurally |
|---|---|---|
| **R1** | No daemon writes to disk without going through the encryption daemon first | The ONLY storage-write function accepts an `EncryptedBlob` type (already-encrypted + signed payload). It must be **impossible** to pass raw bytes — reject at type level and raise at runtime. No second write path may exist. |
| **R2** | The canonical record is append-only. Never overwritten, ever — including in mocks/tests | Any attempt to modify/replace an existing record raises an error. Enforce in the mock filesystem AND at the database level (triggers/constraints). Write a test that deliberately tries to overwrite and asserts failure. |
| **R3** | A sensor with no data reports "no data" — never a fake zero | All mock inputs support an explicit `UNAVAILABLE` state distinct from `0`. Consumers must propagate `NULL-with-reason` (e.g. `{"value": null, "reason": "not_worn"}`), never substitute defaults. |
| **R4** | No daemon reaches directly into another daemon's data | All cross-daemon access goes through an explicit interface module (a visible seam for a future policy/permissions layer). No importing another daemon's internals. |

---

## 1. Tech stack (fixed — don't debate, just build)

- **Language:** Python 3.11+
- **Testing:** `pytest` (every module ships with its own test suite; CI runs all of them)
- **Crypto:** `cryptography` library — RSA-2048 signatures, SHA-256 hashing, AES-GCM for payload encryption. Real crypto math, mock *chip* interface.
- **Cloud gateway:** FastAPI + Uvicorn, running locally
- **Canonical record DB:** SQLite with append-only enforcement via triggers (`BEFORE UPDATE` / `BEFORE DELETE` → `RAISE(ABORT)`)
- **"BLE" transport:** Simulated over local Unix domain sockets / in-process message bus. **Do NOT use bluez, bleak, or any real Bluetooth stack.** The GATT service model (services → characteristics → read/write/notify) is implemented as a protocol abstraction so a real BLE transport can be swapped in later.
- **Orchestration:** systemd-style unit definitions expressed as declarative config + a Python supervisor that enforces startup ordering and readiness checks (must run on any Linux machine without root)
- **CI:** GitHub Actions workflow running the full test matrix on every push

## 2. Repository layout

Build everything inside `hw-track-3-connectivity/`:

```
hw-track-3-connectivity/
├── README.md                      <- this file
├── common/
│   ├── types.py                   <- EncryptedBlob, SensorReading (with UNAVAILABLE), events
│   ├── mock_hal.py                <- minimal stand-ins for HW-1/HW-2 interfaces (see §3)
│   └── seams.py                   <- R4 cross-daemon access interfaces
├── storage/                       <- Day 1
│   ├── vault.py, deletion.py, append_only.py
│   └── tests/
├── provisioning/                  <- Day 1
│   ├── harden.sh
│   └── tests/
├── ota/                           <- Day 2
│   ├── receiver.py, partitions.py, rollback.py
│   └── tests/
├── ble/                           <- Day 2
│   ├── gatt_model.py              <- shared service/characteristic definitions (single source of truth)
│   ├── mock_peripheral.py         <- fixed-response fake for OTHER teams to pair against
│   ├── daemon.py                  <- the REAL device-side logic (reads mocked live state)
│   ├── pairing.py                 <- Numeric Comparison flow
│   └── tests/
├── orchestration/                 <- Day 3
│   ├── units/*.unit               <- service definitions + ordering
│   ├── supervisor.py
│   └── tests/
├── cli/                           <- Day 3
│   ├── chronis_cli.py
│   └── tests/
├── cloud/                         <- Day 4
│   ├── gateway.py                 <- FastAPI ingestion + verify + decrypt
│   ├── canonical_db.py            <- append-only SQLite store
│   └── tests/
├── e2e/
│   └── test_full_pipeline.py      <- fake sensor → encrypt → upload → verify → decrypt → DB
├── docs/
│   └── COMPONENT_SPEC_LIST.md
└── .github/workflows/ci.yml
```

## 3. Dependencies on other tracks — mock them, don't wait

Tracks HW-1 and HW-2 are being built in parallel by other pairs. Do not block on them. Build minimal local stand-ins in `common/mock_hal.py`:

- **HW-1 capture-intensity output:** a generator emitting `{timestamp, level: L0–L5, cause}` events plus fake sensor payloads (audio chunk, camera frame, IMU batch, PPG batch — all synthetic bytes, clearly labeled synthetic).
- **HW-1 worn/not-worn signal:** a togglable boolean feed with an `UNAVAILABLE` state (R3).
- **HW-2 encryption daemon:** an `encrypt_and_sign(raw: bytes, meta: dict) -> EncryptedBlob` and `verify(blob) -> bool` pair using real AES-GCM + RSA under a **mock chip interface** clearly marked `# TEMPORARY STAND-IN for ATECC608B`. Key hierarchy stub: Device Identity Key (generated once), daily-derived Data Session Key (DIK + date, re-derived on demand, never persisted), per-upload-session Server Transport Key.
- **Device state provider:** battery %, firmware version, sync status, storage used/free, current level, kill-switch/pause states — one interface the BLE daemon queries (through `seams.py`, per R4).

Keep every stand-in's interface signature-compatible with what the other tracks will produce, and isolate them in `common/` so swap-out is a one-file change.

---

## 4. Module specs

### 4.1 Storage manager (`storage/`)

**Vault tree** — create and manage exactly:

```
/vault/YYYY-MM-DD/audio/
/vault/YYYY-MM-DD/camera/
/vault/YYYY-MM-DD/sensors/imu/
/vault/YYYY-MM-DD/sensors/ppg/
/vault/YYYY-MM-DD/metadata.json
/vault/YYYY-MM-DD/manifest.sha
/system/logs/        # plaintext allowed — NEVER user data
/system/config/      # device config, public key, sync state
/system/firmware/    # current + previous version (rollback)
```

Root the tree under a configurable base dir (`tmp_path` in tests). All writes into `/vault/**` go through **one** function: `vault_write(blob: EncryptedBlob, category, date) -> RecordId`. It raises `TypeError` on anything that isn't an `EncryptedBlob` (R1). `/system/logs/` has a separate plaintext writer that refuses payloads tagged as user data.

**Double-confirmation deletion** — a file is deletable **only** when BOTH are true:
1. Device-side upload-complete confirmation recorded for that file
2. Independent server-side receipt confirmation with a **matching SHA-256 hash on both sides**

Anything else → deletion refused + logged. No force-delete path, no exceptions flag. Tests must cover: only-device-confirmed, only-server-confirmed, hash mismatch, both-confirmed (the sole success path).

**Append-only enforcement (R2)** — `manifest.sha` and metadata records are append-only: attempting to rewrite an existing record ID raises `AppendOnlyViolation`. Test the overwrite attempt explicitly.

### 4.2 Network provisioning (`provisioning/harden.sh`)

Idempotent bash script, ready to run the moment a real board exists:
- Firewall (ufw or raw nftables): default deny incoming, allow SSH (rate-limited) + the gateway upload port only
- SSH: `PasswordAuthentication no`, `PubkeyAuthentication yes`, `PermitRootLogin no`, `ChallengeResponseAuthentication no`
- Test via a `--dry-run` mode + a pytest that asserts the generated sshd/firewall config lines (don't require root in CI)

### 4.3 OTA update receiver (`ota/`)

Plain-file based, no hardware:
- **Signature verification:** RSA-2048 over the firmware image; generate a test keypair in the test fixtures
- **Integrity:** SHA-256 hash check against a signed manifest
- **Partitioning:** download into an inactive partition directory (`/system/firmware/slot_b/`), mark active only after verification; previous version retained in the other slot
- **Rollback:** a boot-attempt counter; after **3 consecutive failed boots** on new firmware → automatically revert active slot to previous version
- **Mandatory failure-path tests:** (a) bad signature → update rejected + phone-alert event emitted; (b) 3 failed boots → automatic revert to previous working version. Both must be explicit named tests.

### 4.4 GATT model + Mock BLE peripheral + Real BLE daemon (`ble/`)

Define all 8 services ONCE in `gatt_model.py`; both the mock peripheral and the real daemon consume the same definitions:

1. **Device Info** — battery %, fw version, sync status, storage used/available, current capture-intensity level, operating mode, camera kill-switch state, audio pause state
2. **LED Control** — per-zone color, pattern (static/pulse/chase/flash/custom), brightness 0–100, on/off schedule
3. **Display Control** — push short message, upload watch-face image
4. **Camera Control** — kill-switch status (read-only), current frame rate
5. **Audio Control** — pause (with duration), resume, current state
6. **Config** — WiFi credentials (write-only, encrypted in transit), sync schedule, operating mode, display/notification prefs
7. **Alerts** (device→phone only) — sync complete, low battery, storage warning, sensor disconnect, tamper detected, new insight ready, double-tap moment marked, mode change confirmed, boot complete
8. **Annotation** — receive a text note from phone, attach to nearest double-tap timestamp

**Mock peripheral (`mock_peripheral.py`)** — a standalone process other teams can "pair" against over the socket transport. Returns plausible fixed/scriptable responses. This is a test double for the phone-app team; it is NOT the device logic.

**Real BLE daemon (`daemon.py`)** — the actual on-device logic:
- Handlers for all 8 services reading **live mocked device state** through `seams.py` (R4) — never static canned values
- **Numeric Comparison pairing:** generate a 6-digit code, simulate display on device + phone, require explicit match confirmation before bond completes. Test the failed-match case → pairing aborts safely, no bond stored. Never fall back to "Just Works".
- **Auto-reconnect:** persist bond with last-paired phone; on connection drop, reconnect within 10 s (use a fake clock — tests must not sleep)
- **Beacon mode:** when unconnected, advertise ONLY device name + battery %, 1 Hz. Test asserts the serialized advertisement contains zero user data fields.
- **Range/disconnect monitoring:** if disconnected > 30 min while worn-detector says "worn" → log flagged event, deliver to phone on next reconnect. Test with fake clock; also test worn = `UNAVAILABLE` (R3: don't treat as worn or not-worn — flag differently).

### 4.5 Orchestration (`orchestration/`)

- Declarative unit files: name, command, dependencies, readiness check
- **Ordering invariant:** encryption daemon starts FIRST; nothing else launches until it reports ready. Supervisor enforces this; test injects a slow/failed encryption daemon and asserts nothing else started.
- Restart policy per unit; encryption-daemon death mid-run → supervisor halts everything (mirrors HW-2's watchdog HALT semantics)

### 4.6 chronis-cli (`cli/`)

Commands (minimum): `status`, `sensor-read`, `crypto-test`, `storage-list` — all against the mock stack.
**Hard requirement:** never print plaintext user data. `sensor-read` shows availability/metadata + ciphertext preview or redaction, `storage-list` shows filenames/sizes/hashes only. Write a test that scans captured stdout of every command against known plaintext fixtures and asserts zero leakage.

### 4.7 Cloud gateway + canonical DB (`cloud/`)

**Gateway (FastAPI):**
- `POST /ingest` accepts an encrypted payload (storage-manager output format)
- Verifies signature against the (mock) encryption daemon's signing scheme → reject 400 on failure, and log
- Decrypts using server-side transport-key logic
- Emits a structured decrypted event to the next stage (handoff function + persisted to canonical DB)
- Returns the server-side SHA-256 receipt confirmation the deletion flow (§4.1) requires — wire these together

**Canonical record DB (SQLite):**
- Append-only table (id, device_id, ts, event_type, payload_json, hash, prev_hash)
- `BEFORE UPDATE` and `BEFORE DELETE` triggers → `RAISE(ABORT)` — R2 enforced at DB level
- Optional but preferred: hash-chain each record to the previous one for tamper evidence
- Tests: UPDATE attempt fails, DELETE attempt fails, INSERT succeeds

**End-to-end pipeline test (`e2e/`)** — the sprint's connective proof:
```
synthetic sensor data → capture-intensity decision (mock HW-1)
  → encrypt+sign (mock HW-2) → vault_write → "upload" to gateway
  → verify → decrypt → structured event → canonical DB row
  → server receipt → deletion flow now permits local delete
```
One test runs this loop for a multi-event simulated session and asserts: zero R1–R4 violations, hash matches end-to-end, DB rows are append-only, deletion only unlocked after dual confirmation.

### 4.8 CI (`.github/workflows/ci.yml`)

- Trigger: every push + PR
- Jobs: lint (ruff), then pytest across all module test suites + e2e
- Fail-fast off (report all module failures), Python 3.11 matrix entry minimum

### 4.9 Component spec list (`docs/COMPONENT_SPEC_LIST.md`)

Part numbers + key datasheet specs only — **no vendors, no pricing**:
- ICM-42688-P — IMU
- MAX30102 — heart-rate/PPG
- ATECC608B — secure element
- IMX219 — camera sensor
- DS3231-class — RTC backup
- Radxa Zero 3W — compute board

For each: interface (I²C/SPI/CSI), supply voltage, typical/peak current draw, package/footprint, and which mock in this repo stands in for it.

---

## 5. Build order

1. `common/` (types, seams, mock HAL stand-ins) — everything depends on `EncryptedBlob` and R3-aware `SensorReading`
2. `storage/` + `provisioning/`
3. `ota/`
4. `ble/gatt_model.py` → `mock_peripheral.py` → `daemon.py`
5. `orchestration/` + `cli/`
6. `cloud/` + `e2e/`
7. `ci.yml` + `COMPONENT_SPEC_LIST.md`

Run the full pytest suite after each numbered step; do not proceed with failures.

## 6. Definition of done

- [ ] Every module has its own passing pytest suite; `pytest` at repo root is green
- [ ] R1: a test proves raw bytes cannot reach `vault_write` (type rejection + runtime raise)
- [ ] R2: overwrite attempts fail at BOTH mock-filesystem and DB level, proven by tests
- [ ] R3: `UNAVAILABLE` propagates as null-with-reason everywhere a sensor value is consumed; no default-zero substitution anywhere
- [ ] R4: no module imports another daemon's internals; all cross-daemon reads go through `seams.py`
- [ ] Deletion is impossible without device-confirm AND server hash-matched confirm
- [ ] OTA: bad-signature rejection and 3-failed-boot rollback both have explicit passing tests
- [ ] Pairing failed-match case aborts safely; beacon advertisement provably contains no user data
- [ ] Encryption daemon provably starts first; nothing starts if it never becomes ready
- [ ] `chronis-cli` stdout leak-test passes on all commands
- [ ] E2E pipeline test passes: device → gateway → verify → decrypt → append-only DB → receipt → deletion unlock
- [ ] CI workflow present and runs the full suite on push
- [ ] `COMPONENT_SPEC_LIST.md` complete (specs only, no pricing)
- [ ] All timing tests (10 s reconnect, 30 min disconnect, 1 Hz beacon) use injected fake clocks — no real sleeps in the suite

## 7. Explicitly out of scope

- Real Bluetooth radio behavior, RF range, real pairing UX
- Real I²C/SPI bus behavior, address conflicts, chip timing
- Vendor selection, pricing, purchasing
- The policy/permissions layer behind the R4 seams (leave the seam, don't build the layer)
- Production deployment of the gateway (local run only)

Label every mock clearly in code as a temporary stand-in. The exit condition for this track: when real hardware arrives, only drivers get swapped — no logic gets written.
