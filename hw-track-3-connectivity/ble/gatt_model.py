"""
GATT service/characteristic definitions — single source of truth.
Both mock_peripheral and daemon consume these definitions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional


class CharProp(str, Enum):
    READ = "read"
    WRITE = "write"
    WRITE_NO_RESP = "write_no_response"
    NOTIFY = "notify"
    INDICATE = "indicate"
    WRITE_ONLY = "write_only"


@dataclass
class Characteristic:
    uuid: str
    name: str
    props: List[CharProp]
    description: str = ""
    write_encrypted: bool = False


@dataclass
class Service:
    uuid: str
    name: str
    characteristics: List[Characteristic]


# ── Service definitions ─────────────────────────────────────────────────────

DEVICE_INFO_SERVICE = Service(
    uuid="0000180A-0000-1000-8000-00805F9B34FB",
    name="DeviceInfo",
    characteristics=[
        Characteristic("0000FE01-0000-1000-8000-00805F9B34FB", "BatteryPercent", [CharProp.READ, CharProp.NOTIFY]),
        Characteristic("0000FE02-0000-1000-8000-00805F9B34FB", "FirmwareVersion", [CharProp.READ]),
        Characteristic("0000FE03-0000-1000-8000-00805F9B34FB", "SyncStatus", [CharProp.READ, CharProp.NOTIFY]),
        Characteristic("0000FE04-0000-1000-8000-00805F9B34FB", "StorageUsed", [CharProp.READ]),
        Characteristic("0000FE05-0000-1000-8000-00805F9B34FB", "StorageAvailable", [CharProp.READ]),
        Characteristic("0000FE06-0000-1000-8000-00805F9B34FB", "CaptureLevelCurrent", [CharProp.READ, CharProp.NOTIFY]),
        Characteristic("0000FE07-0000-1000-8000-00805F9B34FB", "OperatingMode", [CharProp.READ]),
        Characteristic("0000FE08-0000-1000-8000-00805F9B34FB", "CameraKillSwitch", [CharProp.READ]),
        Characteristic("0000FE09-0000-1000-8000-00805F9B34FB", "AudioPauseState", [CharProp.READ]),
    ],
)

LED_CONTROL_SERVICE = Service(
    uuid="0000FE10-0000-1000-8000-00805F9B34FB",
    name="LEDControl",
    characteristics=[
        Characteristic("0000FE11-0000-1000-8000-00805F9B34FB", "ZoneColor", [CharProp.READ, CharProp.WRITE]),
        Characteristic("0000FE12-0000-1000-8000-00805F9B34FB", "Pattern", [CharProp.READ, CharProp.WRITE]),
        Characteristic("0000FE13-0000-1000-8000-00805F9B34FB", "Brightness", [CharProp.READ, CharProp.WRITE]),
        Characteristic("0000FE14-0000-1000-8000-00805F9B34FB", "OnOffSchedule", [CharProp.READ, CharProp.WRITE]),
    ],
)

DISPLAY_CONTROL_SERVICE = Service(
    uuid="0000FE20-0000-1000-8000-00805F9B34FB",
    name="DisplayControl",
    characteristics=[
        Characteristic("0000FE21-0000-1000-8000-00805F9B34FB", "PushMessage", [CharProp.WRITE]),
        Characteristic("0000FE22-0000-1000-8000-00805F9B34FB", "WatchFaceUpload", [CharProp.WRITE]),
    ],
)

CAMERA_CONTROL_SERVICE = Service(
    uuid="0000FE30-0000-1000-8000-00805F9B34FB",
    name="CameraControl",
    characteristics=[
        Characteristic("0000FE31-0000-1000-8000-00805F9B34FB", "KillSwitchStatus", [CharProp.READ]),
        Characteristic("0000FE32-0000-1000-8000-00805F9B34FB", "FrameRate", [CharProp.READ]),
    ],
)

AUDIO_CONTROL_SERVICE = Service(
    uuid="0000FE40-0000-1000-8000-00805F9B34FB",
    name="AudioControl",
    characteristics=[
        Characteristic("0000FE41-0000-1000-8000-00805F9B34FB", "PauseWithDuration", [CharProp.WRITE]),
        Characteristic("0000FE42-0000-1000-8000-00805F9B34FB", "Resume", [CharProp.WRITE]),
        Characteristic("0000FE43-0000-1000-8000-00805F9B34FB", "CurrentState", [CharProp.READ, CharProp.NOTIFY]),
    ],
)

CONFIG_SERVICE = Service(
    uuid="0000FE50-0000-1000-8000-00805F9B34FB",
    name="Config",
    characteristics=[
        Characteristic(
            "0000FE51-0000-1000-8000-00805F9B34FB",
            "WiFiCredentials",
            [CharProp.WRITE_ONLY],
            write_encrypted=True,
        ),
        Characteristic("0000FE52-0000-1000-8000-00805F9B34FB", "SyncSchedule", [CharProp.READ, CharProp.WRITE]),
        Characteristic("0000FE53-0000-1000-8000-00805F9B34FB", "OperatingMode", [CharProp.READ, CharProp.WRITE]),
        Characteristic("0000FE54-0000-1000-8000-00805F9B34FB", "DisplayPrefs", [CharProp.READ, CharProp.WRITE]),
        Characteristic("0000FE55-0000-1000-8000-00805F9B34FB", "NotificationPrefs", [CharProp.READ, CharProp.WRITE]),
    ],
)

ALERTS_SERVICE = Service(
    uuid="0000FE60-0000-1000-8000-00805F9B34FB",
    name="Alerts",
    characteristics=[
        Characteristic("0000FE61-0000-1000-8000-00805F9B34FB", "SyncComplete", [CharProp.NOTIFY]),
        Characteristic("0000FE62-0000-1000-8000-00805F9B34FB", "LowBattery", [CharProp.NOTIFY]),
        Characteristic("0000FE63-0000-1000-8000-00805F9B34FB", "StorageWarning", [CharProp.NOTIFY]),
        Characteristic("0000FE64-0000-1000-8000-00805F9B34FB", "SensorDisconnect", [CharProp.NOTIFY]),
        Characteristic("0000FE65-0000-1000-8000-00805F9B34FB", "TamperDetected", [CharProp.NOTIFY]),
        Characteristic("0000FE66-0000-1000-8000-00805F9B34FB", "NewInsightReady", [CharProp.NOTIFY]),
        Characteristic("0000FE67-0000-1000-8000-00805F9B34FB", "DoubleTapMoment", [CharProp.NOTIFY]),
        Characteristic("0000FE68-0000-1000-8000-00805F9B34FB", "ModeChangeConfirmed", [CharProp.NOTIFY]),
        Characteristic("0000FE69-0000-1000-8000-00805F9B34FB", "BootComplete", [CharProp.NOTIFY]),
    ],
)

ANNOTATION_SERVICE = Service(
    uuid="0000FE70-0000-1000-8000-00805F9B34FB",
    name="Annotation",
    characteristics=[
        Characteristic("0000FE71-0000-1000-8000-00805F9B34FB", "TextNote", [CharProp.WRITE]),
        Characteristic("0000FE72-0000-1000-8000-00805F9B34FB", "NearestDoubleTapTs", [CharProp.READ]),
    ],
)

ALL_SERVICES: List[Service] = [
    DEVICE_INFO_SERVICE,
    LED_CONTROL_SERVICE,
    DISPLAY_CONTROL_SERVICE,
    CAMERA_CONTROL_SERVICE,
    AUDIO_CONTROL_SERVICE,
    CONFIG_SERVICE,
    ALERTS_SERVICE,
    ANNOTATION_SERVICE,
]

SERVICE_MAP: Dict[str, Service] = {s.uuid: s for s in ALL_SERVICES}
CHAR_MAP: Dict[str, Characteristic] = {
    c.uuid: c for s in ALL_SERVICES for c in s.characteristics
}
