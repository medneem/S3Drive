"""Elevated Windows Service Control Manager helper bundled with S3Drive.

This EXE is invoked through the UAC dialog. No AWS keys are passed on the
command line; it reads config.json and Windows DPAPI credentials instead.
"""
from __future__ import annotations

import ctypes
import json
import sys
from pathlib import Path

from profiles import ProfileStore, config_path
from service_control import enroll_profile, validate_owner_sid
from service_control_installer import install_control_service
from windows_service import (
    clear_service_cache, install_and_start, remove_service, service_dir,
    service_status, stop_service,
)


def bundled_broker_archive() -> Path:
    """Nuitka onefile data lives beside __file__, not the portable config."""
    return Path(__file__).resolve().parent / "S3DriveControl-bundled.zip"


def run_action(action: str, profile_id: str, owner_sid: str | None = None) -> None:
    import uuid
    uuid.UUID(profile_id)
    if not ctypes.windll.shell32.IsUserAnAdmin():
        raise PermissionError("Управление службой требует прав администратора.")
    if action in ("install", "setup-control"):
        if owner_sid is None:
            raise ValueError("Требуется SID текущего пользователя S3Drive.")
        validate_owner_sid(owner_sid)
        install_control_service(bundled_broker_archive())
        if action == "install":
            # DPAPI user scope: an alternate administrator account may fail to
            # decrypt the original user's profile; never pass keys on argv.
            profile = next(
                (p for p in ProfileStore().load() if p.id == profile_id), None
            )
            if profile is None:
                raise ValueError("Профиль отсутствует в config.json.")
            install_and_start(profile, config_path().parent / "rclone.exe")
            enroll_profile(profile_id, owner_sid, drive=profile.drive)
        else:
            if service_status(profile_id) is None:
                raise RuntimeError("Служба ещё не установлена.")
            # For legacy installations, trust only the protected previous
            # label backup. The GUI config may have been edited since install.
            path = service_dir(profile_id) / "explorer_label_backup.json"
            if path.is_symlink() or not path.is_file():
                raise RuntimeError("Не удалось определить букву старой службы.")
            drive = json.loads(path.read_text(encoding="utf-8"))["drive"]
            enroll_profile(profile_id, owner_sid, drive=drive)
    elif action == "stop":
        stop_service(profile_id)
    elif action == "remove":
        remove_service(profile_id)
    elif action == "clear-cache":
        clear_service_cache(profile_id)
    else:
        raise ValueError("Недопустимое действие службы.")


def portable_config_probe(executable: Path) -> bool:
    """Validate onefile config resolution without reading any S3 credentials."""
    return config_path().resolve() == (executable.resolve().parent / "config.json")


def main() -> int:
    # CI packaging probe: no UAC, no service changes or config reads.
    if len(sys.argv) == 2 and sys.argv[1] == "--self-test":
        import zipfile
        archive = bundled_broker_archive()
        if not portable_config_probe(Path(sys.argv[0])) or not archive.is_file():
            return 2
        with zipfile.ZipFile(archive) as packaged:
            return 0 if packaged.namelist() == ["S3DriveControl.exe"] else 2
    try:
        if len(sys.argv) not in (3, 4):
            raise ValueError(
                "Использование: S3DriveAdmin.exe install|setup-control|stop|remove|clear-cache <profile-id> [owner-SID]"
            )
        action = sys.argv[1]
        if (action in ("install", "setup-control")) != (len(sys.argv) == 4):
            raise ValueError("Неверное количество параметров административной операции.")
        run_action(action, sys.argv[2], sys.argv[3] if len(sys.argv) == 4 else None)
        return 0
    except Exception as exc:
        ctypes.windll.user32.MessageBoxW(
            0, str(exc), "S3Drive — управление службой", 0x10,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
