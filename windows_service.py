"""Windows SCM integration for S3Drive profile mounts.

Rclone itself is a Windows service (supported by WinFsp). Service runs as
LocalSystem so the mounted letter is global and visible in Windows Explorer.
Only the elevated S3DriveAdmin helper may install/configure/remove services.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import socket
import subprocess
import uuid
from pathlib import Path
from urllib.request import Request, urlopen

SERVICE_PREFIX = "S3Drive-"
SERVICE_STOPPED = 1
SERVICE_START_PENDING = 2
SERVICE_STOP_PENDING = 3
SERVICE_RUNNING = 4


def service_name(profile_id: str) -> str:
    return SERVICE_PREFIX + uuid.UUID(profile_id).hex


def service_status(profile_id: str) -> int | None:
    """SCM status or None when service is not installed."""
    import win32serviceutil
    import pywintypes

    try:
        return win32serviceutil.QueryServiceStatus(service_name(profile_id))[1]
    except pywintypes.error as exc:
        if exc.winerror == 1060:
            return None
        raise


def service_dir(profile_id: str) -> Path:
    if os.name != "nt":
        raise OSError("Службы поддерживаются только в Windows.")
    return Path(os.environ["PROGRAMDATA"]) / "S3Drive" / "services" / uuid.UUID(profile_id).hex


def service_executable_dir() -> Path:
    if os.name != "nt":
        raise OSError("Службы поддерживаются только в Windows.")
    return Path(os.environ["PROGRAMDATA"]) / "S3Drive" / "bin"


def validate_service_profile(profile) -> None:
    profile.validate()
    uuid.UUID(profile.id)
    for value in (profile.name, profile.access_key, profile.secret_key, profile.region, profile.endpoint):
        if any(c in value for c in "\r\n\x00"):
            raise ValueError("S3-параметры не должны содержать переносов строк.")


def config_text(profile) -> str:
    validate_service_profile(profile)
    # Sanitized to avoid writing attacker-provided INI section delimiters.
    import configparser
    from io import StringIO
    config = configparser.ConfigParser(interpolation=None)
    config.optionxform = str
    config["s3drive_mount"] = {
        "type": "s3", "provider": "Other", "env_auth": "false",
        "access_key_id": profile.access_key,
        "secret_access_key": profile.secret_key,
        "endpoint": profile.endpoint.rstrip("/"),
        "region": profile.region,
        "force_path_style": str(profile.path_style).lower(),
    }
    out = StringIO()
    config.write(out)
    return out.getvalue()


def windows_command_line(parts: list[str]) -> str:
    """Quote paths with spaces for SCM lpBinaryPathName."""
    return subprocess.list2cmdline(parts)


def service_command(profile, rclone: Path, config: Path, cache: Path,
                    log: Path, htpasswd: Path, port: int) -> str:
    validate_service_profile(profile)
    return windows_command_line([
        str(rclone), "mount", "s3drive_mount:" + profile.bucket,
        profile.drive, "--config", str(config), "--vfs-cache-mode", "writes",
        "--cache-dir", str(cache), "--log-file", str(log), "--log-level", "INFO",
        "--volname", f"S3Drive - {profile.name}",
        # SYSTEM owns the mount. Grant Explorer users delete rights as well\n        # as read/write; FRFW alone fails Windows directory deletion.\n        "-o", "FileSecurity=D:P(A;;FA;;;WD)",
        "--rc", "--rc-addr", f"127.0.0.1:{port}",
        "--rc-htpasswd", str(htpasswd),
    ])


def _protect_acl(path: Path, *, directory: bool) -> None:
    """Replace (not merge) ACL and owner to prevent service binary tampering."""
    import win32con
    import win32file
    import win32security
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        raise RuntimeError("Отказано: каталог службы является ссылкой или junction.")
    system_sid = win32security.CreateWellKnownSid(
        win32security.WinLocalSystemSid, None,
    )
    admin_sid = win32security.CreateWellKnownSid(
        win32security.WinBuiltinAdministratorsSid, None,
    )
    acl = win32security.ACL()
    flags = (win32con.OBJECT_INHERIT_ACE | win32con.CONTAINER_INHERIT_ACE
             if directory else 0)
    for sid in (system_sid, admin_sid):
        acl.AddAccessAllowedAceEx(
            win32security.ACL_REVISION, flags, win32file.FILE_ALL_ACCESS, sid,
        )
    win32security.SetNamedSecurityInfo(
        str(path), win32security.SE_FILE_OBJECT,
        (win32security.OWNER_SECURITY_INFORMATION
         | win32security.DACL_SECURITY_INFORMATION
         | win32security.PROTECTED_DACL_SECURITY_INFORMATION),
        admin_sid, None, acl, None,
    )


def protect_directory(path: Path) -> None:
    """No user-writable service executable, credentials or RC controls."""
    path.mkdir(parents=True, exist_ok=True)
    _protect_acl(path, directory=True)


def protect_file(path: Path) -> None:
    _protect_acl(path, directory=False)


def check_drive_free(drive: str) -> None:
    # Validate again in the elevated process. Never replace a real disk.
    if not re.fullmatch(r"[A-Z]:", drive):
        raise ValueError("Некорректная буква диска.")
    if Path(drive + "\\").exists():
        raise RuntimeError(f"Буква {drive} занята. Отключите предыдущий диск.")


def _sc(*args: str) -> None:
    proc = subprocess.run(["sc.exe", *args], capture_output=True, text=True, check=False)
    if proc.returncode:
        raise RuntimeError(f"Ошибка управления службой ({proc.returncode}): "
                           + (proc.stderr.strip() or proc.stdout.strip()))


def _choose_rc_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _control_request(port: int, password: str, method: str) -> dict:
    token = base64.b64encode(f"s3drive:{password}".encode("utf-8")).decode("ascii")
    req = Request(
        f"http://127.0.0.1:{port}/{method}", method="POST", data=b"{}",
        headers={"Authorization": "Basic " + token, "Content-Type": "application/json"},
    )
    with urlopen(req, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def ensure_vfs_idle(control: dict) -> None:
    """Fail closed if VFS is unavailable, dirty, or has upload errors."""
    stats = _control_request(control["port"], control["password"], "vfs/stats")
    disk = stats["diskCache"]
    queued = int(disk["uploadsQueued"])
    active = int(disk["uploadsInProgress"])
    errors = int(disk["erroredFiles"])
    if queued or active or errors:
        raise RuntimeError(
            f"Нельзя остановить службу: в очереди {queued}, передаются {active}, "
            f"ошибок загрузки {errors}. Дождитесь завершения записи."
        )


def install_and_start(profile, source_rclone: Path) -> None:
    """Called from elevated helper, never from unelevated GUI."""
    validate_service_profile(profile)
    installed = service_status(profile.id)
    if installed == SERVICE_RUNNING:
        raise RuntimeError("Служба уже запущена. Для изменения настроек остановите её.")
    check_drive_free(profile.drive)
    if not source_rclone.is_file():
        raise FileNotFoundError("Рядом с S3Drive отсутствует rclone.exe.")
    root = Path(os.environ["PROGRAMDATA"]) / "S3Drive"
    protect_directory(root)
    protect_directory(root / "services")
    protect_directory(service_executable_dir())
    dest = service_dir(profile.id)
    protect_directory(dest)
    # Copy to administrator-only location: the service binary path must never
    # point to a user-writable portable ZIP directory.
    import shutil
    binary = service_executable_dir() / "rclone.exe"
    if not binary.is_file():
        shutil.copy2(source_rclone, binary)
    protect_file(binary)
    config = dest / "rclone.conf"
    config.write_text(config_text(profile), encoding="utf-8")
    protect_file(config)
    control_file = dest / "control.json"
    if control_file.is_file():
        control = json.loads(control_file.read_text(encoding="utf-8"))
    else:
        control = {"port": _choose_rc_port(), "password": secrets.token_urlsafe(36)}
    control_file.write_text(json.dumps(control), encoding="utf-8")
    protect_file(control_file)
    sha = base64.b64encode(hashlib.sha1(control["password"].encode()).digest()).decode()
    htpasswd = dest / "rc.htpasswd"
    htpasswd.write_text("s3drive:{SHA}" + sha + "\n", encoding="utf-8")
    protect_file(htpasswd)
    cache = dest / "cache"
    cache.mkdir(exist_ok=True)
    log = dest / "rclone.log"
    cmd = service_command(profile, binary, config, cache, log, htpasswd, int(control["port"]))
    name = service_name(profile.id)
    if installed is None:
        _sc("create", name, "binPath=", cmd, "start=", "auto",
            "obj=", "LocalSystem", "DisplayName=", "S3Drive " + profile.drive)
    else:
        _sc("config", name, "binPath=", cmd, "start=", "auto")
    _sc("start", name)


def stop_service(profile_id: str) -> None:
    state = service_status(profile_id)
    if state is None:
        raise RuntimeError("Служба не установлена.")
    if state == SERVICE_STOPPED:
        return
    if state != SERVICE_RUNNING:
        raise RuntimeError("Служба меняет состояние. Повторите попытку позже.")
    control_file = service_dir(profile_id) / "control.json"
    control = json.loads(control_file.read_text(encoding="utf-8"))
    ensure_vfs_idle(control)
    _sc("stop", service_name(profile_id))


def remove_service(profile_id: str) -> None:
    state = service_status(profile_id)
    if state is None:
        return
    if state != SERVICE_STOPPED:
        raise RuntimeError("Сначала остановите службу и дождитесь отключения диска.")
    _sc("delete", service_name(profile_id))
    # Keep the old VFS cache and log for recovery/debugging; credentials remain
    # protected under ProgramData and are not silently discarded.
