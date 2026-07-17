"""
Supervisor — enforces startup ordering and readiness checks.
Ordering invariant: encryption daemon starts FIRST; nothing else launches until ready.
Encryption daemon death mid-run → halt everything (mirrors HW-2 watchdog HALT semantics).
Runs on any Linux machine without root.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class ServiceState(str, Enum):
    PENDING = "pending"
    STARTING = "starting"
    RUNNING = "running"
    FAILED = "failed"
    HALTED = "halted"


class RestartPolicy(str, Enum):
    RESTART_ON_FAILURE = "restart_on_failure"
    HALT_ALL_ON_FAILURE = "halt_all_on_failure"


@dataclass
class UnitDefinition:
    name: str
    description: str
    dependencies: List[str]
    readiness_check: str
    restart_policy: RestartPolicy
    startup_priority: int


@dataclass
class ServiceStatus:
    name: str
    state: ServiceState = ServiceState.PENDING
    start_time: Optional[float] = None
    failure_count: int = 0


class SupervisorError(Exception):
    pass


class Supervisor:
    """
    Manages service lifecycle with strict ordering.
    Services provide a start_fn and readiness_fn at registration.
    """

    def __init__(self, clock_fn: Callable[[], float] = time.time):
        self._units: Dict[str, UnitDefinition] = {}
        self._statuses: Dict[str, ServiceStatus] = {}
        self._start_fns: Dict[str, Callable[[], None]] = {}
        self._readiness_fns: Dict[str, Callable[[], bool]] = {}
        self._stop_fns: Dict[str, Callable[[], None]] = {}
        self._clock_fn = clock_fn
        self._halted = False

    def register(
        self,
        unit: UnitDefinition,
        start_fn: Callable[[], None],
        readiness_fn: Callable[[], bool],
        stop_fn: Callable[[], None] = lambda: None,
    ) -> None:
        self._units[unit.name] = unit
        self._statuses[unit.name] = ServiceStatus(name=unit.name)
        self._start_fns[unit.name] = start_fn
        self._readiness_fns[unit.name] = readiness_fn
        self._stop_fns[unit.name] = stop_fn

    def start_all(self) -> None:
        """Start services in priority order, enforcing dependency readiness."""
        if self._halted:
            raise SupervisorError("Supervisor is halted — restart required")

        ordered = sorted(self._units.values(), key=lambda u: u.startup_priority)

        for unit in ordered:
            self._start_service(unit.name)
            if self._halted:
                return

    def _start_service(self, name: str) -> None:
        unit = self._units[name]
        status = self._statuses[name]

        # Check all dependencies are running first
        for dep in unit.dependencies:
            dep_status = self._statuses.get(dep)
            if dep_status is None or dep_status.state != ServiceState.RUNNING:
                logger.error(
                    "Cannot start '%s' — dependency '%s' is not running (state=%s)",
                    name,
                    dep,
                    dep_status.state if dep_status else "not registered",
                )
                status.state = ServiceState.FAILED
                self._handle_failure(name)
                return

        logger.info("Starting service: %s", name)
        status.state = ServiceState.STARTING
        status.start_time = self._clock_fn()

        try:
            self._start_fns[name]()
        except Exception as exc:
            logger.error("Service '%s' failed to start: %s", name, exc)
            status.state = ServiceState.FAILED
            self._handle_failure(name)
            return

        if self._readiness_fns[name]():
            status.state = ServiceState.RUNNING
            logger.info("Service '%s' is ready", name)
        else:
            logger.error("Service '%s' failed readiness check", name)
            status.state = ServiceState.FAILED
            self._handle_failure(name)

    def _handle_failure(self, name: str) -> None:
        unit = self._units[name]
        status = self._statuses[name]
        status.failure_count += 1

        if unit.restart_policy == RestartPolicy.HALT_ALL_ON_FAILURE:
            logger.critical("Critical service '%s' failed — halting all services", name)
            self._halt_all()
        else:
            logger.warning("Service '%s' failed — will restart on next cycle", name)

    def _halt_all(self) -> None:
        self._halted = True
        for name, status in self._statuses.items():
            if status.state == ServiceState.RUNNING:
                logger.info("Halting service: %s", name)
                try:
                    self._stop_fns[name]()
                except Exception:
                    pass
                status.state = ServiceState.HALTED

    def notify_service_died(self, name: str) -> None:
        """Call when a running service dies unexpectedly."""
        status = self._statuses.get(name)
        if status:
            status.state = ServiceState.FAILED
            self._handle_failure(name)

    def get_status(self, name: str) -> Optional[ServiceStatus]:
        return self._statuses.get(name)

    def is_running(self, name: str) -> bool:
        status = self._statuses.get(name)
        return status is not None and status.state == ServiceState.RUNNING

    def is_halted(self) -> bool:
        return self._halted

    def all_statuses(self) -> Dict[str, ServiceState]:
        return {name: s.state for name, s in self._statuses.items()}
