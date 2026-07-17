"""Supervisor tests — encryption daemon must start first; halt-on-failure verified."""
from __future__ import annotations

import pytest

from orchestration.supervisor import (
    RestartPolicy,
    ServiceState,
    Supervisor,
    UnitDefinition,
)


def make_unit(name, deps=None, priority=0, policy=RestartPolicy.RESTART_ON_FAILURE):
    return UnitDefinition(
        name=name,
        description=f"{name} description",
        dependencies=deps or [],
        readiness_check=f"{name}_ready",
        restart_policy=policy,
        startup_priority=priority,
    )


class TestEncryptionDaemonFirstInvariant:
    def test_encryption_daemon_starts_before_dependents(self):
        sv = Supervisor()
        start_order = []

        enc_unit = make_unit("encryption-daemon", priority=0, policy=RestartPolicy.HALT_ALL_ON_FAILURE)
        ble_unit = make_unit("ble-daemon", deps=["encryption-daemon"], priority=1)

        sv.register(enc_unit, lambda: start_order.append("enc"), lambda: True)
        sv.register(ble_unit, lambda: start_order.append("ble"), lambda: True)
        sv.start_all()

        assert start_order.index("enc") < start_order.index("ble")

    def test_nothing_starts_if_encryption_daemon_never_ready(self):
        """Inject a slow/failed encryption daemon — assert nothing else started."""
        sv = Supervisor()
        started = []

        enc_unit = make_unit("encryption-daemon", priority=0, policy=RestartPolicy.HALT_ALL_ON_FAILURE)
        ble_unit = make_unit("ble-daemon", deps=["encryption-daemon"], priority=1)
        storage_unit = make_unit("storage-daemon", deps=["encryption-daemon"], priority=1)

        sv.register(enc_unit, lambda: None, lambda: False)  # never ready
        sv.register(ble_unit, lambda: started.append("ble"), lambda: True)
        sv.register(storage_unit, lambda: started.append("storage"), lambda: True)

        sv.start_all()

        assert "ble" not in started
        assert "storage" not in started

    def test_encryption_daemon_failure_halts_all(self):
        sv = Supervisor()
        enc_unit = make_unit("encryption-daemon", priority=0, policy=RestartPolicy.HALT_ALL_ON_FAILURE)
        ble_unit = make_unit("ble-daemon", deps=["encryption-daemon"], priority=1)

        sv.register(enc_unit, lambda: None, lambda: True)
        sv.register(ble_unit, lambda: None, lambda: True)
        sv.start_all()

        assert sv.is_running("encryption-daemon")
        assert sv.is_running("ble-daemon")

        sv.notify_service_died("encryption-daemon")

        assert sv.is_halted()
        assert not sv.is_running("ble-daemon")


class TestDependencyOrdering:
    def test_service_with_unmet_dependency_fails(self):
        sv = Supervisor()
        ble_unit = make_unit("ble-daemon", deps=["encryption-daemon"], priority=0)
        sv.register(ble_unit, lambda: None, lambda: True)
        sv.start_all()

        assert sv.get_status("ble-daemon").state == ServiceState.FAILED

    def test_happy_path_all_services_running(self):
        sv = Supervisor()
        enc = make_unit("encryption-daemon", priority=0, policy=RestartPolicy.HALT_ALL_ON_FAILURE)
        ble = make_unit("ble-daemon", deps=["encryption-daemon"], priority=1)
        storage = make_unit("storage-daemon", deps=["encryption-daemon"], priority=1)

        sv.register(enc, lambda: None, lambda: True)
        sv.register(ble, lambda: None, lambda: True)
        sv.register(storage, lambda: None, lambda: True)
        sv.start_all()

        assert sv.is_running("encryption-daemon")
        assert sv.is_running("ble-daemon")
        assert sv.is_running("storage-daemon")


class TestRestartPolicy:
    def test_non_critical_failure_does_not_halt(self):
        sv = Supervisor()
        enc = make_unit("encryption-daemon", priority=0, policy=RestartPolicy.HALT_ALL_ON_FAILURE)
        storage = make_unit("storage-daemon", deps=["encryption-daemon"], priority=1)

        sv.register(enc, lambda: None, lambda: True)
        sv.register(storage, lambda: None, lambda: True)
        sv.start_all()

        sv.notify_service_died("storage-daemon")

        assert not sv.is_halted()
        assert sv.is_running("encryption-daemon")
