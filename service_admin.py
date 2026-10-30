"""Elevated Windows Service Control Manager helper bundled with S3Drive.

This EXE is invoked through the UAC dialog. No AWS keys are passed on the
command line; it reads config.json and Windows DPAPI credentials instead.
"""
from __future__ import annotations

import ctypes
import sys
from pathlib import Path

from profiles import ProfileStore, config_path
from windows_service import clear_service_cache, install_and_start, remove_service, stop_service


def run_action(action: str, profile_id: str) -> None:
    import uuid
    uuid.UUID(profile_id)
    if not ctypes.windll.shell32.IsUserAnAdmin():
        raise PermissionError("Управление службой требует прав администратора.")
    if action == "install":
        # DPAPI user scope: when UAC uses a *different* admin account, decrypt
        # may fail. We do not fall back to passing the secret on the CLI.
        profile = next(
            (p for p in ProfileStore().load() if p.id == profile_id), None
        )
        if profile is None:
            raise ValueError("Профиль отсутствует в config.json.")
        rclone = config_path().parent / "rclone.exe"
        install_and_start(profile, rclone)
    elif action == "stop":
        stop_service(profile_id)
    elif action == "remove":
        remove_service(profile_id)
    elif action == "clear-cache":
        clear_service_cache(profile_id)
    else:
        raise ValueError("Недопустимое действие службы.")


def main() -> int:
    try:
        if len(sys.argv) != 3:
            raise ValueError("Использование: S3DriveAdmin.exe install|stop|remove|clear-cache <profile-id>")
        run_action(sys.argv[1], sys.argv[2])
        return 0
    except Exception as exc:
        ctypes.windll.user32.MessageBoxW(
            0, str(exc), "S3Drive — управление службой", 0x10,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
