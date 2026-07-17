"""OTA tests — mandatory failure paths included."""
from __future__ import annotations

import hashlib
import os

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from ota.partitions import PartitionManager
from ota.receiver import (
    MAX_FAILED_BOOTS,
    OTAManifest,
    OTAReceiver,
    RollbackManager,
    SignatureVerificationFailed,
)


@pytest.fixture
def keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


@pytest.fixture
def partitions(tmp_path):
    return PartitionManager(tmp_path / "firmware")


@pytest.fixture
def alerts():
    received = []
    return received, lambda t, p: received.append((t, p))


@pytest.fixture
def receiver(partitions, keypair, alerts):
    _, alerts_fn = alerts
    _, pub = keypair
    return OTAReceiver(partitions, pub, alert_fn=alerts_fn)


def make_firmware_and_manifest(version="1.1.0"):
    image = os.urandom(512)
    sha = hashlib.sha256(image).hexdigest()
    manifest = OTAManifest(version=version, sha256=sha, size=len(image))
    return image, manifest


def sign_image(private_key, image: bytes) -> bytes:
    return private_key.sign(image, padding.PKCS1v15(), hashes.SHA256())


# ── Mandatory failure path: bad signature ──────────────────────────────────

class TestBadSignatureRejection:
    """Spec requires explicit named test: bad signature → update rejected + alert emitted."""

    def test_bad_signature_raises(self, receiver, alerts):
        received, _ = alerts
        image, manifest = make_firmware_and_manifest()
        bad_sig = os.urandom(256)

        with pytest.raises(SignatureVerificationFailed):
            receiver.receive_update(image, manifest, bad_sig)

    def test_bad_signature_emits_phone_alert(self, partitions, keypair, tmp_path):
        _, pub = keypair
        emitted = []
        rcv = OTAReceiver(partitions, pub, alert_fn=lambda t, p: emitted.append((t, p)))
        image, manifest = make_firmware_and_manifest()

        with pytest.raises(SignatureVerificationFailed):
            rcv.receive_update(image, manifest, os.urandom(256))

        assert any("signature" in t for t, _ in emitted)

    def test_bad_signature_does_not_write_partition(self, receiver, partitions):
        image, manifest = make_firmware_and_manifest()
        with pytest.raises(SignatureVerificationFailed):
            receiver.receive_update(image, manifest, os.urandom(256))
        assert not (partitions.inactive_slot_dir() / "firmware.bin").exists()


# ── Mandatory failure path: 3 failed boots → rollback ──────────────────────

class TestThreeFailedBootsRollback:
    """Spec requires explicit named test: 3 failed boots → automatic revert."""

    def test_three_failed_boots_triggers_rollback(self, partitions, keypair):
        priv, pub = keypair
        emitted = []
        rcv = OTAReceiver(partitions, pub, alert_fn=lambda t, p: emitted.append((t, p)))
        rb = RollbackManager(partitions)

        image, manifest = make_firmware_and_manifest("2.0.0")
        sig = sign_image(priv, image)

        # Install original version in slot_a
        partitions.write_to_inactive(b"old firmware", "1.0.0")
        partitions.mark_inactive_active()
        partitions.write_to_inactive(b"old firmware", "1.0.0")
        # Now slot_a is active (was inactive before last flip)

        # Install new version
        rcv.receive_update(image, manifest, sig)
        new_slot = partitions.active_slot
        assert partitions.active_version() == "2.0.0"

        # Simulate 3 failed boots
        for _ in range(MAX_FAILED_BOOTS):
            rb.record_boot_attempt()

        # After 3 failures, should have rolled back
        assert partitions.active_slot != new_slot

    def test_successful_boot_resets_counter(self, partitions, keypair):
        priv, pub = keypair
        rcv = OTAReceiver(partitions, pub)
        rb = RollbackManager(partitions)

        image, manifest = make_firmware_and_manifest("2.0.0")
        sig = sign_image(priv, image)
        rcv.receive_update(image, manifest, sig)

        rb.record_boot_attempt()
        rb.record_boot_attempt()
        rb.record_successful_boot()

        assert partitions.boot_attempts == 0

    def test_two_failed_boots_no_rollback_yet(self, partitions, keypair):
        priv, pub = keypair
        rcv = OTAReceiver(partitions, pub)
        rb = RollbackManager(partitions)

        image, manifest = make_firmware_and_manifest("2.0.0")
        sig = sign_image(priv, image)
        rcv.receive_update(image, manifest, sig)
        slot_after_install = partitions.active_slot

        rb.record_boot_attempt()
        rb.record_boot_attempt()

        assert partitions.active_slot == slot_after_install


# ── Happy path ──────────────────────────────────────────────────────────────

class TestOTAHappyPath:
    def test_valid_update_installs_and_marks_active(self, partitions, keypair):
        priv, pub = keypair
        rcv = OTAReceiver(partitions, pub)
        image, manifest = make_firmware_and_manifest("1.5.0")
        sig = sign_image(priv, image)
        rcv.receive_update(image, manifest, sig)
        assert partitions.active_version() == "1.5.0"
        assert (partitions.active_slot_dir() / "firmware.bin").exists()
