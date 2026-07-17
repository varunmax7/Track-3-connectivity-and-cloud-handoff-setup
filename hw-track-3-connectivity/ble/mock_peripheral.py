"""
Mock BLE peripheral — a standalone test double for the phone-app team.
Returns plausible fixed/scriptable responses over the socket transport.
This is NOT the device logic — see daemon.py for that.
"""
from __future__ import annotations

import json
import socket
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from ble.gatt_model import ALL_SERVICES, CHAR_MAP


SOCKET_PATH = "/tmp/chronis_mock_ble.sock"


class MockPeripheral:
    """
    Scriptable fake device. Other teams can send GATT read/write/notify ops
    over a Unix socket and get fixed responses.
    """

    def __init__(self, socket_path: str = SOCKET_PATH):
        self._socket_path = socket_path
        self._overrides: Dict[str, Any] = {}
        self._running = False
        self._server: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._pending_notifications: list[dict] = []

    def set_response(self, char_name: str, value: Any) -> None:
        """Override a characteristic's response value."""
        self._overrides[char_name] = value

    def _default_value(self, char_name: str) -> Any:
        defaults = {
            "BatteryPercent": 75,
            "FirmwareVersion": "1.0.0-mock",
            "SyncStatus": "idle",
            "StorageUsed": 104857600,
            "StorageAvailable": 943718400,
            "CaptureLevelCurrent": 2,
            "OperatingMode": "normal",
            "CameraKillSwitch": False,
            "AudioPauseState": False,
        }
        return defaults.get(char_name, None)

    def _handle_request(self, request: dict) -> dict:
        op = request.get("op")
        char_name = request.get("char")

        if op == "read":
            val = self._overrides.get(char_name, self._default_value(char_name))
            return {"status": "ok", "char": char_name, "value": val}
        elif op == "write":
            self._overrides[char_name] = request.get("value")
            return {"status": "ok", "char": char_name}
        elif op == "list_services":
            return {
                "status": "ok",
                "services": [{"uuid": s.uuid, "name": s.name} for s in ALL_SERVICES],
            }
        else:
            return {"status": "error", "message": f"Unknown op: {op}"}

    def _handle_client(self, conn: socket.socket):
        try:
            data = b""
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk
                try:
                    request = json.loads(data.decode())
                    response = self._handle_request(request)
                    conn.sendall(json.dumps(response).encode())
                    data = b""
                except json.JSONDecodeError:
                    continue
        finally:
            conn.close()

    def start(self) -> None:
        sock_path = Path(self._socket_path)
        if sock_path.exists():
            sock_path.unlink()
        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.bind(self._socket_path)
        self._server.listen(5)
        self._running = True
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        self._server.settimeout(0.5)
        while self._running:
            try:
                conn, _ = self._server.accept()
                threading.Thread(target=self._handle_client, args=(conn,), daemon=True).start()
            except socket.timeout:
                continue

    def stop(self) -> None:
        self._running = False
        if self._server:
            self._server.close()
        sock_path = Path(self._socket_path)
        if sock_path.exists():
            sock_path.unlink()


class MockPeripheralClient:
    """Client to interact with MockPeripheral from tests."""

    def __init__(self, socket_path: str = SOCKET_PATH):
        self._socket_path = socket_path

    def _send(self, request: dict) -> dict:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(self._socket_path)
        s.sendall(json.dumps(request).encode())
        response = s.recv(65536)
        s.close()
        return json.loads(response.decode())

    def read(self, char_name: str) -> Any:
        return self._send({"op": "read", "char": char_name})

    def write(self, char_name: str, value: Any) -> dict:
        return self._send({"op": "write", "char": char_name, "value": value})

    def list_services(self) -> dict:
        return self._send({"op": "list_services"})
