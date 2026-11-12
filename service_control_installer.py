"""Protected S3DriveControl installation and side-by-side SCM upgrades.

Never replace an executable image used by a Windows service: even after SCM
reports SERVICE_STOPPED, Windows can retain its mapped image for a short time.
"""
from __future__ import annotations

import hashlib
import os
import re
import secrets
import time
import zipfile
from pathlib import Path

from windows_service import protect_directory, protect_file, service_executable_dir

CONTROL_EXE = "S3DriveControl.exe"
CONTROL_SERVICE = "S3DriveControl"
MAX_PAYLOAD_BYTES = 128 * 1024 * 1024
_VERSIONED_EXE = re.compile(r"S3DriveControl-[0-9a-f]{16}\.exe\Z")


def _unsafe_path(path: Path) -> bool:
    return path.is_symlink() or (
        hasattr(path, "is_junction") and path.is_junction()
    )


def _stage_broker(archive_path: Path, temporary: Path) -> str:
    """Extract exactly one bounded EXE without trusting ZIP paths or size."""
    digest = hashlib.sha256()
    with zipfile.ZipFile(archive_path) as bundle:
        files = bundle.infolist()
        if (len(files) != 1 or files[0].filename != CONTROL_EXE
                or files[0].is_dir()
                or not 0 < files[0].file_size <= MAX_PAYLOAD_BYTES):
            raise ValueError("Некорректный пакет системного компонента.")
        with bundle.open(files[0]) as source, temporary.open("xb") as output:
            total = 0
            while chunk := source.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_PAYLOAD_BYTES:
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


def _configured_executable(config: tuple, base: Path) -> Path:
    """Accept only our previous fixed path or an immutable versioned EXE."""
    command = config[3]
    if (not isinstance(command, str) or len(command) < 3
            or not command.startswith('"') or not command.endswith('"')):
        raise RuntimeError("Непредвиденная командная строка службы управления.")
    current = Path(command[1:-1])
    if (_unsafe_path(current) or current.parent.resolve() != base.resolve()
            or (current.name != CONTROL_EXE
                and _VERSIONED_EXE.fullmatch(current.name) is None)):
        raise RuntimeError("Служба управления имеет неожиданное расположение.")
    if not current.is_file():
        raise RuntimeError("Защищённый компонент управления отсутствует.")
    if config[7] not in ("LocalSystem", "NT AUTHORITY\\SYSTEM"):
        raise RuntimeError("Служба управления запущена не от LocalSystem.")
    return current


def _set_service_executable(win32service, service_handle, path: Path) -> None:
    """Change only the SCM image path; retain account, dependencies and flags."""
    win32service.ChangeServiceConfig(
        service_handle,
        win32service.SERVICE_NO_CHANGE,
        win32service.SERVICE_NO_CHANGE,
        win32service.SERVICE_NO_CHANGE,
        '"' + str(path) + '"',
        None, 0, None, None, None, None,
    )


def install_control_service(source_archive: Path) -> None:
    """Install or upgrade only the broker; never touch rclone or VFS caches.

    Every broker version has its own protected filename derived from SHA-256.
    The original EXE remains untouched, including after a failed upgrade.
    """
    if os.name != "nt":
        raise OSError("Службы доступны только в Windows.")
    import pywintypes
    import win32service
    import win32serviceutil

    if _unsafe_path(source_archive) or not source_archive.is_file():
        raise FileNotFoundError(
            "В S3DriveAdmin.exe отсутствует компонент службы управления."
        )
    base = service_executable_dir()
    protect_directory(base.parent)
    protect_directory(base)

    try:
        status = win32serviceutil.QueryServiceStatus(CONTROL_SERVICE)[1]
    except pywintypes.error as exc:
        if exc.winerror != 1060:
            raise
        status = None

    if status not in (None, win32service.SERVICE_RUNNING, win32service.SERVICE_STOPPED):
        raise RuntimeError("Служба управления меняет состояние.")

    scm = win32service.OpenSCManager(
        None, None, (win32service.SC_MANAGER_CREATE_SERVICE
                     if status is None else win32service.SC_MANAGER_CONNECT)
    )
    handle = None
    temporary = base / ("S3DriveControl-stage-" + secrets.token_hex(8) + ".tmp")
    try:
        previous = None
        if status is not None:
            handle = win32service.OpenService(
                scm, CONTROL_SERVICE,
                win32service.SERVICE_QUERY_CONFIG
                | win32service.SERVICE_QUERY_STATUS
                | win32service.SERVICE_START
                | win32service.SERVICE_STOP
                | win32service.SERVICE_CHANGE_CONFIG,
            )
            previous = _configured_executable(
                win32service.QueryServiceConfig(handle), base
            )

        if _unsafe_path(temporary) or temporary.exists():
            raise RuntimeError("Недопустимый временный файл службы управления.")
        digest = _stage_broker(source_archive.resolve(), temporary)
        candidate = base / f"S3DriveControl-{digest[:16]}.exe"
        if _unsafe_path(candidate):
            raise RuntimeError("Недопустимая ссылка вместо системного компонента.")
        if candidate.exists():
            if not candidate.is_file() or _file_digest(candidate) != digest:
                raise RuntimeError("Контрольная сумма установленного брокера не совпадает.")
        else:
            # Never overwrite a mapped EXE, even if the old service just stopped.
            os.rename(temporary, candidate)
            protect_file(candidate)

        if previous is not None and _file_digest(previous) == digest:
            # Already current: no stop/start or change to the SCM configuration.
            if status == win32service.SERVICE_STOPPED:
                win32service.StartService(handle, None)
                _wait_service_state(
                    win32serviceutil, win32service, win32service.SERVICE_RUNNING
                )
            return

        if status is None:
            handle = win32service.CreateService(
                scm, CONTROL_SERVICE, "S3Drive - Safe service controller",
                win32service.SERVICE_ALL_ACCESS,
                win32service.SERVICE_WIN32_OWN_PROCESS,
                win32service.SERVICE_AUTO_START,
                win32service.SERVICE_ERROR_NORMAL,
                '"' + str(candidate) + '"',
                None, 0, None, "LocalSystem", None,
            )
            win32service.StartService(handle, None)
            _wait_service_state(
                win32serviceutil, win32service, win32service.SERVICE_RUNNING
            )
            return

        # The new EXE is fully written while the previous service is running.
        # Switching SCM ImagePath does not touch the locked old executable.
        changed_config = False
        try:
            if status == win32service.SERVICE_RUNNING:
                win32service.ControlService(
                    handle, win32service.SERVICE_CONTROL_STOP
                )
                _wait_service_state(
                    win32serviceutil, win32service, win32service.SERVICE_STOPPED
                )
            _set_service_executable(win32service, handle, candidate)
            changed_config = True
            win32service.StartService(handle, None)
            _wait_service_state(
                win32serviceutil, win32service, win32service.SERVICE_RUNNING
            )
        except Exception:
            # Fail closed. Roll back only the SCM image path; the previous
            # binary is intact and all mounted rclone services keep running.
            try:
                current = win32serviceutil.QueryServiceStatus(CONTROL_SERVICE)[1]
                if current == win32service.SERVICE_RUNNING:
                    win32service.ControlService(
                        handle, win32service.SERVICE_CONTROL_STOP
                    )
                    _wait_service_state(
                        win32serviceutil, win32service, win32service.SERVICE_STOPPED
                    )
                if changed_config:
                    _set_service_executable(win32service, handle, previous)
                if status == win32service.SERVICE_RUNNING:
                    win32service.StartService(handle, None)
                    _wait_service_state(
                        win32serviceutil, win32service, win32service.SERVICE_RUNNING
                    )
            except Exception as rollback_error:
                raise RuntimeError(
                    "Не удалось восстановить прежнюю службу управления S3Drive."
                ) from rollback_error
            raise
    finally:
        # A staging artifact is never in use as a Windows service image.
        temporary.unlink(missing_ok=True)
        if handle is not None:
            win32service.CloseServiceHandle(handle)
        win32service.CloseServiceHandle(scm)
