"""CLI tests — stdout leak test asserts zero plaintext user data exposure."""
from __future__ import annotations

import io
import json
import pytest

from cli.chronis_cli import ChronosCLI
from common.mock_hal import MockDeviceStateProvider, MockEncryptionDaemon, MockSensorProvider

# Known plaintext user data patterns that must NEVER appear in CLI output
KNOWN_PLAINTEXT_FIXTURES = [
    b"SYNTHETIC_AUDIO:",
    b"SYNTHETIC_CAMERA:",
    b"SYNTHETIC_IMU:",
    b"SYNTHETIC_PPG:",
]
KNOWN_PLAINTEXT_STRINGS = [p.decode() for p in KNOWN_PLAINTEXT_FIXTURES]


@pytest.fixture
def cli():
    buf = io.StringIO()
    c = ChronosCLI(
        enc_daemon=MockEncryptionDaemon(),
        sensor_provider=MockSensorProvider(),
        device_state=MockDeviceStateProvider(),
        output_stream=buf,
    )
    return c, buf


class TestStatusCommand:
    def test_returns_ok(self, cli):
        c, buf = cli
        result = c.cmd_status()
        assert result["status"] == "ok"

    def test_contains_battery_and_firmware(self, cli):
        c, buf = cli
        result = c.cmd_status()
        assert "battery_pct" in result
        assert "firmware_version" in result

    def test_no_plaintext_user_data_in_stdout(self, cli):
        c, buf = cli
        c.cmd_status()
        output = buf.getvalue()
        for plaintext in KNOWN_PLAINTEXT_STRINGS:
            assert plaintext not in output, f"User data leaked: {plaintext!r}"


class TestSensorReadCommand:
    def test_returns_ok(self, cli):
        c, buf = cli
        result = c.cmd_sensor_read()
        assert result["status"] == "ok"

    def test_no_raw_bytes_in_output(self, cli):
        c, buf = cli
        c.cmd_sensor_read()
        output = buf.getvalue()
        for plaintext in KNOWN_PLAINTEXT_STRINGS:
            assert plaintext not in output, f"Raw sensor bytes leaked: {plaintext!r}"

    def test_unavailable_sensor_shows_null_with_reason(self, cli):
        c, buf = cli
        c._sensors.set_worn(None)  # UNAVAILABLE
        result = c.cmd_sensor_read()
        worn = result["readings"]["worn"]
        assert worn["value"] is None
        assert worn["reason"] is not None

    def test_shows_ciphertext_preview_not_plaintext(self, cli):
        c, buf = cli
        result = c.cmd_sensor_read()
        # IMU should have ciphertext_preview, not raw bytes
        imu = result["readings"]["imu"]
        assert "ciphertext_preview" in imu
        assert "..." in imu["ciphertext_preview"]


class TestCryptoTestCommand:
    def test_returns_pass(self, cli):
        c, buf = cli
        result = c.cmd_crypto_test()
        assert result["encrypt"] == "pass"
        assert result["verify"] == "pass"
        assert result["decrypt_match"] == "pass"

    def test_no_plaintext_user_data_in_stdout(self, cli):
        c, buf = cli
        c.cmd_crypto_test()
        output = buf.getvalue()
        for plaintext in KNOWN_PLAINTEXT_STRINGS:
            assert plaintext not in output


class TestStorageListCommand:
    def test_returns_ok_without_vault(self, cli):
        c, buf = cli
        result = c.cmd_storage_list()
        assert result["status"] == "ok"

    def test_no_plaintext_user_data_in_stdout(self, cli):
        c, buf = cli
        c.cmd_storage_list()
        output = buf.getvalue()
        for plaintext in KNOWN_PLAINTEXT_STRINGS:
            assert plaintext not in output

    def test_with_vault_lists_only_names_and_sizes(self, tmp_path):
        from storage.vault import Vault
        vault = Vault(tmp_path)
        enc = MockEncryptionDaemon()
        blob = enc.encrypt_and_sign(b"data", {"session_id": "s1", "date": "2024-01-01"})
        vault.vault_write(blob, "audio", day="2024-01-01")

        buf = io.StringIO()
        c = ChronosCLI(enc_daemon=enc, vault=vault, output_stream=buf)
        result = c.cmd_storage_list()
        output = buf.getvalue()

        assert result["count"] == 1
        assert "files" in result
        file_entry = result["files"][0]
        assert "path" in file_entry
        assert "size_bytes" in file_entry
        # No content/payload in output
        for plaintext in KNOWN_PLAINTEXT_STRINGS:
            assert plaintext not in output


class TestAllCommandsLeakTest:
    """Master leak test — scans stdout of every command."""

    def test_no_command_leaks_known_plaintext(self, cli):
        c, buf = cli
        c.cmd_status()
        c.cmd_sensor_read()
        c.cmd_crypto_test()
        c.cmd_storage_list()

        full_output = buf.getvalue()
        for plaintext in KNOWN_PLAINTEXT_STRINGS:
            assert plaintext not in full_output, (
                f"Plaintext user data '{plaintext}' leaked to stdout"
            )
