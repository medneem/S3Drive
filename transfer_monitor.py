"""Read-only VFS upload telemetry for both interactive and Windows service mounts."""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class TransferSnapshot:
    queued: int
    uploading: int
    errors: int
    cache_bytes: int
    current_file: str = ""
    percent: int = 0
    speed: float = 0.0


def parse_snapshot(vfs: dict, queue: dict, core: dict) -> TransferSnapshot:
    disk = vfs["diskCache"]
    items = queue.get("queue", [])
    if not isinstance(items, list):
        raise ValueError("Некорректный ответ очереди rclone.")
    active = core.get("transferring") or []
    transfer = active[0] if active else {}
    # core/stats may be empty for mounts; queue still exposes the active file.
    if not transfer:
        transfer = next((item for item in items if item.get("uploading")), {})
    return TransferSnapshot(
        queued=int(disk["uploadsQueued"]),
        uploading=int(disk["uploadsInProgress"]),
        errors=int(disk["erroredFiles"]),
        cache_bytes=int(disk["bytesUsed"]),
        current_file=str(transfer.get("name") or ""),
        percent=max(0, min(100, int(transfer.get("percentage") or 0))),
        speed=max(0.0, float(transfer.get("speedAvg") or transfer.get("speed") or 0)),
    )


def service_rc_port(configured_command: str) -> int:
    """Read only the loopback port from SCM's public executable command line."""
    match = re.search(r'--rc-addr(?:=|\s+)"?127\.0\.0\.1:(\d{1,5})(?="|\s|$)',
                      configured_command)
    if not match:
        raise RuntimeError("Не найден локальный порт мониторинга службы.")
    port = int(match.group(1))
    if not 1 <= port <= 65535:
        raise ValueError("Некорректный порт мониторинга.")
    return port


def read_service_rc_port(service: str) -> int:
    result = subprocess.run(
        ["sc.exe", "qc", service], capture_output=True, text=True,
        timeout=4, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("Не удалось прочитать конфигурацию службы.")
    return service_rc_port(result.stdout)


def _public_rc(port: int, method: str) -> dict:
    """Only rclone NoAuth telemetry endpoints; never use --rc-no-auth."""
    if method not in ("vfs/stats", "vfs/queue", "core/stats"):
        raise ValueError("Запрещён метод управления rclone.")
    request = Request(
        f"http://127.0.0.1:{port}/{method}",
        data=b"{}", method="POST", headers={"Content-Type": "application/json"},
    )
    with urlopen(request, timeout=1.5) as response:
        return json.loads(response.read(1024 * 1024).decode("utf-8"))


def read_service_snapshot(profile_id: str) -> TransferSnapshot:
    from windows_service import service_name
    port = read_service_rc_port(service_name(profile_id))
    return parse_snapshot(
        _public_rc(port, "vfs/stats"),
        _public_rc(port, "vfs/queue"),
        _public_rc(port, "core/stats"),
    )


def read_user_snapshot(manager, profile_id: str) -> TransferSnapshot:
    session = manager.sessions[profile_id]
    return parse_snapshot(
        manager._rc(session, "vfs/stats", timeout=1.5),
        manager._rc(session, "vfs/queue", timeout=1.5),
        manager._rc(session, "core/stats", timeout=1.5),
    )
