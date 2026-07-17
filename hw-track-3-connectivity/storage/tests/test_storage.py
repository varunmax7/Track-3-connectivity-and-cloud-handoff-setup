"""Tests for storage module — covers all four rules."""
from __future__ import annotations

import pytest
from pathlib import Path

from common.mock_hal import MockEncryptionDaemon
from common.types import EncryptedBlob
from storage.append_only import AppendOnlyStore, AppendOnlyViolation
from storage.deletion import DeletionManager, DeletionRefused
from storage.vault import Vault


@pytest.fixture
def enc():
    return MockEncryptionDaemon()


@pytest.fixture
def vault(tmp_path):
    return Vault(tmp_path)


@pytest.fixture
def blob(enc):
    return enc.encrypt_and_sign(b"test sensor data", {"session_id": "s1", "date": "2024-01-01"})


# R1 tests —————————————————————————————————————————————————

class TestR1VaultRawBytesRejected:
    def test_raw_bytes_raises_type_error(self, vault):
        with pytest.raises(TypeError, match="EncryptedBlob"):
            vault.vault_write(b"raw bytes", "audio")

    def test_plain_string_raises_type_error(self, vault):
        with pytest.raises(TypeError, match="EncryptedBlob"):
            vault.vault_write("not encrypted", "audio")

    def test_dict_raises_type_error(self, vault):
        with pytest.raises(TypeError, match="EncryptedBlob"):
            vault.vault_write({"data": "fake"}, "audio")

    def test_encrypted_blob_succeeds(self, vault, blob):
        record_id = vault.vault_write(blob, "audio", day="2024-01-01")
        assert record_id is not None

    def test_log_write_refuses_user_data_context(self, vault):
        with pytest.raises(Exception, match="user data"):
            vault.log_write("secret data", context="audio")


# R2 tests —————————————————————————————————————————————————

class TestR2AppendOnly:
    def test_overwrite_raises_append_only_violation(self):
        store = AppendOnlyStore()
        store.insert("rec-1", {"data": "original"})
        with pytest.raises(AppendOnlyViolation):
            store.insert("rec-1", {"data": "overwrite attempt"})

    def test_new_record_succeeds(self):
        store = AppendOnlyStore()
        store.insert("rec-1", {"data": "first"})
        store.insert("rec-2", {"data": "second"})
        assert len(store) == 2

    def test_vault_manifest_is_append_only(self, vault, enc):
        blob1 = enc.encrypt_and_sign(b"data1", {"session_id": "s1", "date": "2024-01-01"})
        blob2 = enc.encrypt_and_sign(b"data2", {"session_id": "s2", "date": "2024-01-01"})
        vault.vault_write(blob1, "audio", day="2024-01-01")
        vault.vault_write(blob2, "camera", day="2024-01-01")
        # Both should succeed (different record IDs)

    def test_duplicate_blob_raises(self, vault, blob):
        vault.vault_write(blob, "audio", day="2024-01-01")
        with pytest.raises(AppendOnlyViolation):
            vault.vault_write(blob, "audio", day="2024-01-01")


# Deletion tests ————————————————————————————————————————————

class TestDeletion:
    @pytest.fixture
    def dm(self, vault, blob):
        record_id = vault.vault_write(blob, "audio", day="2024-01-01")
        return DeletionManager(vault), record_id, blob.sha256_hash

    def test_only_device_confirmed_refuses(self, dm):
        mgr, record_id, sha = dm
        mgr.confirm_device_upload(record_id, sha)
        with pytest.raises(DeletionRefused, match="server receipt"):
            mgr.delete(record_id)

    def test_only_server_confirmed_refuses(self, dm):
        mgr, record_id, sha = dm
        mgr.confirm_server_receipt(record_id, sha)
        with pytest.raises(DeletionRefused, match="device upload"):
            mgr.delete(record_id)

    def test_hash_mismatch_refuses(self, dm):
        mgr, record_id, sha = dm
        mgr.confirm_device_upload(record_id, sha)
        mgr.confirm_server_receipt(record_id, "deadbeef" * 8)
        with pytest.raises(DeletionRefused, match="mismatch"):
            mgr.delete(record_id)

    def test_both_confirmed_matching_hash_succeeds(self, dm, tmp_path):
        mgr, record_id, sha = dm
        mgr.confirm_device_upload(record_id, sha)
        mgr.confirm_server_receipt(record_id, sha)
        mgr.delete(record_id)
        assert not mgr.can_delete(record_id)

    def test_no_confirmation_at_all_refuses(self, dm):
        mgr, record_id, _ = dm
        with pytest.raises(DeletionRefused):
            mgr.delete(record_id)
