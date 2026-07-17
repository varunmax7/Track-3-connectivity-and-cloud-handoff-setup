"""BLE daemon and pairing tests — all timing uses fake clock."""
from __future__ import annotations

import pytest

from ble.daemon import BLEDaemon, DISCONNECT_WHILE_WORN_TIMEOUT_S, RECONNECT_TIMEOUT_S
from ble.gatt_model import ALL_SERVICES, DEVICE_INFO_SERVICE
from ble.mock_peripheral import MockPeripheral, MockPeripheralClient
from ble.pairing import PairingError, PairingSession, PairingState
from common.mock_hal import MockDeviceStateProvider, MockSensorProvider


@pytest.fixture
def sensors():
    return MockSensorProvider()


@pytest.fixture
def device_state():
    return MockDeviceStateProvider()


@pytest.fixture
def fake_clock():
    t = [0.0]
    return t, lambda: t[0]


@pytest.fixture
def daemon(device_state, sensors, fake_clock):
    _, clock = fake_clock
    return BLEDaemon(device_state, sensors, clock_fn=clock)


# ── GATT model tests ────────────────────────────────────────────────────────

class TestGATTModel:
    def test_exactly_eight_services(self):
        assert len(ALL_SERVICES) == 8

    def test_all_service_names_unique(self):
        names = [s.name for s in ALL_SERVICES]
        assert len(names) == len(set(names))

    def test_device_info_service_has_expected_characteristics(self):
        char_names = {c.name for c in DEVICE_INFO_SERVICE.characteristics}
        assert "BatteryPercent" in char_names
        assert "FirmwareVersion" in char_names
        assert "CameraKillSwitch" in char_names
        assert "AudioPauseState" in char_names


# ── Pairing tests ───────────────────────────────────────────────────────────

class TestPairing:
    def test_generates_six_digit_code(self):
        p = PairingSession()
        code = p.start("AA:BB:CC:DD:EE:FF")
        assert len(code) == 6
        assert code.isdigit()

    def test_correct_code_bonds(self):
        p = PairingSession()
        code = p.start("AA:BB:CC:DD:EE:FF")
        p.confirm(code)
        assert p.state == PairingState.BONDED
        assert p.is_bonded

    def test_wrong_code_aborts_no_bond(self):
        p = PairingSession()
        p.start("AA:BB:CC:DD:EE:FF")
        with pytest.raises(PairingError, match="do not match"):
            p.confirm("000000")
        assert p.state == PairingState.ABORTED
        assert not p.is_bonded

    def test_aborted_stores_no_bond(self):
        p = PairingSession()
        p.start("AA:BB:CC:DD:EE:FF")
        with pytest.raises(PairingError):
            p.confirm("wrong!")
        assert p.bonded_address is None

    def test_no_fallback_to_just_works(self):
        """Pairing failure must abort, not fall back silently."""
        p = PairingSession()
        p.start("AA:BB:CC:DD:EE:FF")
        with pytest.raises(PairingError, match="No fallback to Just Works"):
            p.confirm("999999")
        assert p.state == PairingState.ABORTED

    def test_daemon_pairing_aborts_on_mismatch(self, daemon):
        code = daemon.start_pairing("AA:BB:CC:DD:EE:FF")
        wrong_code = str((int(code) + 1) % 1_000_000).zfill(6)
        with pytest.raises(PairingError):
            daemon.confirm_pairing(wrong_code)
        assert daemon.pairing_state() == PairingState.ABORTED


# ── Beacon tests ────────────────────────────────────────────────────────────

class TestBeacon:
    def test_beacon_contains_no_user_data(self, daemon):
        adv = daemon.get_beacon_advertisement()
        allowed_keys = {"device_name", "battery_pct"}
        assert set(adv.keys()) <= allowed_keys

    def test_beacon_contains_device_name(self, daemon):
        adv = daemon.get_beacon_advertisement()
        assert "device_name" in adv
        assert adv["device_name"] == "Chronis-1"

    def test_beacon_contains_battery_pct(self, daemon):
        adv = daemon.get_beacon_advertisement()
        assert "battery_pct" in adv

    def test_beacon_has_zero_user_data_fields(self, daemon):
        user_data_fields = {
            "sensor_data", "audio", "camera", "imu", "ppg",
            "location", "activity", "user_id", "events",
        }
        adv = daemon.get_beacon_advertisement()
        overlap = set(adv.keys()) & user_data_fields
        assert overlap == set(), f"Beacon contains user data fields: {overlap}"


# ── Auto-reconnect tests (fake clock) ───────────────────────────────────────

class TestAutoReconnect:
    def test_should_reconnect_within_10s(self, daemon, fake_clock):
        t, _ = fake_clock
        daemon.on_connect("AA:BB:CC:DD:EE:FF")
        daemon._bond.store("AA:BB:CC:DD:EE:FF")
        daemon.on_disconnect("AA:BB:CC:DD:EE:FF")
        t[0] = 5.0
        assert daemon.should_attempt_reconnect()

    def test_should_not_reconnect_after_10s(self, daemon, fake_clock):
        t, _ = fake_clock
        daemon.on_connect("AA:BB:CC:DD:EE:FF")
        daemon._bond.store("AA:BB:CC:DD:EE:FF")
        daemon.on_disconnect("AA:BB:CC:DD:EE:FF")
        t[0] = RECONNECT_TIMEOUT_S + 1
        assert not daemon.should_attempt_reconnect()

    def test_no_reconnect_without_bond(self, daemon, fake_clock):
        t, _ = fake_clock
        daemon.on_disconnect("AA:BB:CC:DD:EE:FF")
        t[0] = 5.0
        assert not daemon.should_attempt_reconnect()


# ── Range/disconnect monitoring (fake clock) ────────────────────────────────

class TestDisconnectMonitoring:
    def test_worn_disconnect_over_30min_flags_event(self, daemon, sensors, fake_clock):
        t, _ = fake_clock
        sensors.set_worn(True)
        daemon.on_connect("AA:BB:CC:DD:EE:FF")
        daemon.on_disconnect("AA:BB:CC:DD:EE:FF")
        t[0] = DISCONNECT_WHILE_WORN_TIMEOUT_S + 1
        event = daemon.check_reconnect_or_flag()
        assert event is not None
        assert event["type"] == "disconnect_while_worn"

    def test_not_worn_no_flag(self, daemon, sensors, fake_clock):
        t, _ = fake_clock
        sensors.set_worn(False)
        daemon.on_connect("AA:BB:CC:DD:EE:FF")
        daemon.on_disconnect("AA:BB:CC:DD:EE:FF")
        t[0] = DISCONNECT_WHILE_WORN_TIMEOUT_S + 1
        event = daemon.check_reconnect_or_flag()
        assert event is None

    def test_worn_unavailable_flags_differently(self, daemon, sensors, fake_clock):
        """R3: UNAVAILABLE worn state must not be treated as worn or not-worn."""
        t, _ = fake_clock
        sensors.set_worn(None)  # UNAVAILABLE
        daemon.on_connect("AA:BB:CC:DD:EE:FF")
        daemon.on_disconnect("AA:BB:CC:DD:EE:FF")
        t[0] = DISCONNECT_WHILE_WORN_TIMEOUT_S + 1
        event = daemon.check_reconnect_or_flag()
        assert event is not None
        assert event["type"] == "disconnect_worn_state_unavailable"
        assert event["type"] != "disconnect_while_worn"

    def test_no_flag_within_30min(self, daemon, sensors, fake_clock):
        t, _ = fake_clock
        sensors.set_worn(True)
        daemon.on_connect("AA:BB:CC:DD:EE:FF")
        daemon.on_disconnect("AA:BB:CC:DD:EE:FF")
        t[0] = DISCONNECT_WHILE_WORN_TIMEOUT_S - 1
        event = daemon.check_reconnect_or_flag()
        assert event is None


# ── Mock peripheral tests ───────────────────────────────────────────────────

class TestMockPeripheral:
    # Unix socket paths on macOS have a 104-byte limit — use /tmp with short names
    def test_read_battery_default(self):
        import time
        sock_path = "/tmp/chronis_t1.sock"
        p = MockPeripheral(socket_path=sock_path)
        p.start()
        time.sleep(0.1)
        try:
            client = MockPeripheralClient(socket_path=sock_path)
            resp = client.read("BatteryPercent")
            assert resp["status"] == "ok"
            assert resp["value"] == 75
        finally:
            p.stop()

    def test_override_response(self):
        import time
        sock_path = "/tmp/chronis_t2.sock"
        p = MockPeripheral(socket_path=sock_path)
        p.set_response("BatteryPercent", 42)
        p.start()
        time.sleep(0.1)
        try:
            client = MockPeripheralClient(socket_path=sock_path)
            resp = client.read("BatteryPercent")
            assert resp["value"] == 42
        finally:
            p.stop()

    def test_list_services_returns_8(self):
        import time
        sock_path = "/tmp/chronis_t3.sock"
        p = MockPeripheral(socket_path=sock_path)
        p.start()
        time.sleep(0.1)
        try:
            client = MockPeripheralClient(socket_path=sock_path)
            resp = client.list_services()
            assert len(resp["services"]) == 8
        finally:
            p.stop()
