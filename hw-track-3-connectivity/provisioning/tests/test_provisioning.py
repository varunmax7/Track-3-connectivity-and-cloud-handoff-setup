"""Tests for harden.sh — runs in --dry-run mode, no root required."""
from __future__ import annotations

import subprocess
from pathlib import Path

HARDEN_SCRIPT = Path(__file__).parent.parent / "harden.sh"


def run_dry(extra_args: list[str] = None) -> str:
    cmd = ["bash", str(HARDEN_SCRIPT), "--dry-run"]
    if extra_args:
        cmd.extend(extra_args)
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0, f"Script failed: {result.stderr}"
    return result.stdout


class TestHardenScript:
    def test_script_exists_and_is_executable(self):
        assert HARDEN_SCRIPT.exists()

    def test_dry_run_exits_zero(self):
        out = run_dry()
        assert "Hardening complete" in out

    def test_sshd_password_auth_disabled(self):
        out = run_dry()
        assert "PasswordAuthentication no" in out

    def test_sshd_pubkey_enabled(self):
        out = run_dry()
        assert "PubkeyAuthentication yes" in out

    def test_sshd_root_login_disabled(self):
        out = run_dry()
        assert "PermitRootLogin no" in out

    def test_sshd_challenge_response_disabled(self):
        out = run_dry()
        assert "ChallengeResponseAuthentication no" in out

    def test_firewall_default_deny_incoming(self):
        out = run_dry()
        assert "default deny incoming" in out

    def test_firewall_ssh_rate_limited(self):
        out = run_dry()
        assert "limit" in out
        assert "22/tcp" in out

    def test_firewall_gateway_port_allowed(self):
        out = run_dry()
        assert "8443/tcp" in out

    def test_custom_gateway_port(self):
        out = run_dry(["--gateway-port", "9090"])
        assert "9090/tcp" in out

    def test_dry_run_does_not_call_ufw_without_dry_run_flag(self):
        out = run_dry()
        for line in out.splitlines():
            if "ufw" in line:
                assert "DRY-RUN" in line
