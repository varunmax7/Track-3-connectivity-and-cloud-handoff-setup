# Component Specification List

Specs only — no vendors, no pricing.

---

## ICM-42688-P — IMU (Inertial Measurement Unit)

| Property | Value |
|---|---|
| Interface | SPI (up to 24 MHz) / I²C (up to 1 MHz) |
| Supply voltage | 1.71 V – 3.6 V (VDD) |
| Typical current draw | 2.78 mA (accel + gyro, full-power) |
| Peak current draw | 3.0 mA (all axes active) |
| Package | LGA-14 (2.5 × 3.0 × 0.91 mm) |
| Mock in this repo | `common/mock_hal.py` → `MockSensorProvider.get_imu_batch()` |

---

## MAX30102 — Heart-Rate / PPG Sensor

| Property | Value |
|---|---|
| Interface | I²C (up to 400 kHz) |
| Supply voltage | 1.7 V – 2.0 V (VDD) / 3.1 V – 5.0 V (LED) |
| Typical current draw | 600 µA (LEDs off), up to 50 mA (LED on) |
| Peak current draw | 50 mA per LED channel |
| Package | OLGA-14 (5.6 × 3.3 × 1.55 mm) |
| Mock in this repo | `common/mock_hal.py` → `MockSensorProvider.get_ppg_batch()` |

---

## ATECC608B — Secure Element

| Property | Value |
|---|---|
| Interface | I²C (up to 1 MHz) / Single-Wire |
| Supply voltage | 1.7 V – 3.6 V |
| Typical current draw | 1 µA (sleep) / 8 mA (active) |
| Peak current draw | 8 mA (during crypto operation) |
| Package | UDFN-8 (2 × 3 mm) / SOIC-8 |
| Mock in this repo | `common/mock_hal.py` → `MockEncryptionDaemon` (marked `# TEMPORARY STAND-IN for ATECC608B`) |

---

## IMX219 — Camera Sensor

| Property | Value |
|---|---|
| Interface | MIPI CSI-2 (2-lane) |
| Supply voltage | 1.8 V (VDDIO) / 2.8 V (VDD) / 1.2 V (VDDPLL) |
| Typical current draw | 270 mA (streaming at 1080p) |
| Peak current draw | 320 mA |
| Package | 65-pin CSP |
| Mock in this repo | `common/mock_hal.py` → `MockSensorProvider.get_camera_frame()` |

---

## DS3231-class — RTC Backup

| Property | Value |
|---|---|
| Interface | I²C (up to 400 kHz) |
| Supply voltage | 2.3 V – 5.5 V (primary) / 2.3 V – 5.5 V (backup) |
| Typical current draw | 200 µA (active) / 0.84 µA (battery backup) |
| Peak current draw | 300 µA (temperature conversion) |
| Package | SOIC-16 / DIP-16 |
| Mock in this repo | System clock via `time.time()` (injected as `clock_fn` in all daemons) |

---

## Radxa Zero 3W — Compute Board

| Property | Value |
|---|---|
| Interface | USB-C (power + OTG), HDMI, GPIO (40-pin), CSI |
| Supply voltage | 5 V via USB-C |
| Typical current draw | 500 mA (idle) |
| Peak current draw | 2000 mA (CPU + peripherals under load) |
| Package | 65 × 37 mm SBC |
| Mock in this repo | Host machine running Python 3.11+ — all modules execute on any Linux x86_64 |
