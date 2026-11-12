"""Elevated installation and safe in-place upgrade of S3DriveControl."""
from __future__ import annotations

import hashlib
import os
import shutil
import time
import zipfile
from pathlib import Path

from windows_service import protect_directory, protect_file, service_executable_dir

CONTROL_EXE = "S3DriveControl.exe"
CONTROL_SERVICE = "S3DriveControl"
MAX_PAYLOAD_BYTES = 128 * 1024 * 1024


def _stage_broker(archive_path: Path, temporary: Path) -> str:
    """Extract exactly one bounded embedded executable to a protected directory."""
    digest = hashlib.sha256()
    with zipfile.ZipFile(archive_path) as bundle:
        items = bundle.infolist()
        if (len(items) != 1 or items[0].filename != CONTROL_EXE
                or items[0].is_dir()
                or not 0 < items[0].file_size <= MAX_PAYLOAD_BYTES):
            raise ValueError("Некорректный пакет системного компонента.")
        with bundle.open(items[0]) as source, temporary.open("xb") as output:
            size = 0
            while chunk := source.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_PAYLOAD_BYTES:
                    raise ValueError("Слишком большой пакет системного компонента.")
                output.write(chunk)
                digest.update(chunk)
    protect_file(temporary)
    return digest.hexdigest()


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _wait_service_state(win32serviceutil, win32service, desired: int) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if win32serviceutil.QueryServiceStatus(CONTROL_SERVICE)[1] == desired:
            return
        time.sleep(0.2)
    raise TimeoutError("S3DriveControl не перешла в ожидаемое состояние.")


def install_control_service(source_archive: Path) -> None:
    """Create or update ONLY the control broker with explicit administrator rights.

    A running broker is stopped before its EXE is replaced. The rclone mount
    services, active transfers, credentials, and their VFS caches are untouched.
    """
    if os.name != "nt":
        raise OSError("Службы доступны только в Windows.")
    import pywintypes
    import win32service
    import win32serviceutil

    if source_archive.is_symlink() or not source_archive.is_file():
        raise FileNotFoundError(
            "В S3DriveAdmin.exe отсутствует компонент службы управления."
        )
    base = service_executable_dir()
    protect_directory(base.parent)
    protect_directory(base)
    target = base / CONTROL_EXE
    temporary = base / "S3DriveControl.exe.new"
    backup = base / "S3DriveControl.exe.bak"
    if any(path.is_symlink() for path in (target, temporary, backup)):
        raise RuntimeError("Недопустимая ссылка в системном каталоге S3Drive.")
    if temporary.exists() or backup.exists():
        raise RuntimeError(
            "Незавершённое обновление S3DriveControl: проверьте системный каталог."
        )

    try:
        status = win32serviceutil.QueryServiceStatus(CONTROL_SERVICE)[1]
    except pywintypes.error as exc:
        if exc.winerror != 1060:
            raise
        status = None

    scm = win32service.OpenSCManager(
        None, None, (win32service.SC_MANAGER_CREATE_SERVICE
                     if status is None else win32service.SC_MANAGER_CONNECT)
    )
    handle = None
    try:
        if status is not None:
            handle = win32service.OpenService(
                scm, CONTROL_SERVICE,
                win32service.SERVICE_QUERY_CONFIG
                | win32service.SERVICE_QUERY_STATUS
                | win32service.SERVICE_START
                | win32service.SERVICE_STOP,
            )
            config = win32service.QueryServiceConfig(handle)
            expected = str(target)
            if (config[3].strip('"') != expected
                    or config[7] not in ("LocalSystem", "NT AUTHORITY\\SYSTEM")):
                raise RuntimeError("Служба управления имеет неожиданную конфигурацию.")
            if not target.is_file():
                raise RuntimeError("Защищённый компонент управления повреждён.")
            if status not in (win32service.SERVICE_RUNNING, win32service.SERVICE_STOPPED):
                raise RuntimeError("Служба управления меняет состояние.")

        try:
            new_digest = _stage_broker(source_archive.resolve(), temporary)
            changed = status is None or _file_digest(target) != new_digest
            if not changed:
                if status == win32service.SERVICE_STOPPED:
                    win32service.StartService(handle, None)
                    _wait_service_state(win32serviceutil, win32service, win32service.SERVICE_RUNNING)
                return

            if status == win32service.SERVICE_RUNNING:
                win32service.ControlService(handle, win32service.SERVICE_CONTROL_STOP)
                _wait_service_state(win32serviceutil, win32service, win32service.SERVICE_STOPPED)

            # Backup is private and protected; no service disk/cache is touched.
            if status is not None:
                shutil.copy2(target, backup)
                protect_file(backup)
            replaced = False
            try:
                os.replace(temporary, target)
                replaced = True
                protect_file(target)
                if status is None:
                    handle = win32service.CreateService(
                        scm, CONTROL_SERVICE, "S3Drive - Safe service controller",
                        win32service.SERVICE_ALL_ACCESS,
                        win32service.SERVICE_WIN32_OWN_PROCESS,
                        win32service.SERVICE_AUTO_START,
                        win32service.SERVICE_ERROR_NORMAL,
                        '"' + str(target) + '"',
                        None, 0, None, "LocalSystem", None,
                    )
                win32service.StartService(handle, None)
                _wait_service_state(win32serviceutil, win32service, win32service.SERVICE_RUNNING)
            except Exception:
                if status is not None:
                    # Roll back the broker binary; restore it if it was running.
                    try:
                        if win32serviceutil.QueryServiceStatus(CONTROL_SERVICE)[1] == win32service.SERVICE_RUNNING:
                            win32service.ControlService(handle, win32service.SERVICE_CONTROL_STOP)
                            _wait_service_state(win32serviceutil, win32service, win32service.SERVICE_STOPPED)
                        if replaced and backup.is_file():
                            os.replace(backup, target)
                            protect_file(target)
                        if status == win32service.SERVICE_RUNNING:
                            win32service.StartService(handle, None)
                            _wait_service_state(win32serviceutil, win32service, win32service.SERVICE_RUNNING)
                    except Exception as rollback_error:
                        raise RuntimeError(
                            "Не удалось восстановить прежнюю службу управления S3Drive."
                        ) from rollback_error
                raise
            else:
                backup.unlink(missing_ok=True)
        finally:
            temporary.unlink(missing_ok=True)
    finally:
        if handle is not None:
            win32service.CloseServiceHandle(handle)
        win32service.CloseServiceHandle(scm)
