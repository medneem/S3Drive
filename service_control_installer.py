"""One-time elevated installation of the protected S3DriveControl broker."""
from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

from windows_service import protect_directory, protect_file, service_executable_dir

CONTROL_EXE = "S3DriveControl.exe"
CONTROL_SERVICE = "S3DriveControl"


def install_control_service(source: Path) -> None:
    """Create the protected LocalSystem broker once; do not overwrite a running EXE."""
    if os.name != "nt":
        raise OSError("Службы доступны только в Windows.")
    import pywintypes
    import win32service
    import win32serviceutil

    if source.is_symlink() or not source.is_file():
        raise FileNotFoundError("S3DriveControl.exe отсутствует в папке приложения.")
    source = source.resolve()
    base = service_executable_dir()
    protect_directory(base.parent)
    protect_directory(base)
    target = base / CONTROL_EXE

    try:
        status = win32serviceutil.QueryServiceStatus(CONTROL_SERVICE)[1]
    except pywintypes.error as exc:
        if exc.winerror != 1060:
            raise
        status = None

    if status is None:
        # Only admins/SYSTEM may modify the installed executable or its folder.
        temporary = base / "S3DriveControl.exe.new"
        if target.is_symlink() or temporary.is_symlink():
            raise RuntimeError("Недопустимая ссылка вместо системного компонента.")
        try:
            shutil.copy2(source, temporary)
            protect_file(temporary)
            os.replace(temporary, target)
            protect_file(target)
        finally:
            temporary.unlink(missing_ok=True)
    elif not target.is_file() or target.is_symlink():
        raise RuntimeError("Защищённый компонент управления повреждён.")

    scm = win32service.OpenSCManager(
        None, None, win32service.SC_MANAGER_CREATE_SERVICE
        if status is None else win32service.SC_MANAGER_CONNECT
    )
    try:
        if status is None:
            handle = win32service.CreateService(
                scm, CONTROL_SERVICE, "S3Drive - Safe service controller",
                win32service.SERVICE_ALL_ACCESS,
                win32service.SERVICE_WIN32_OWN_PROCESS,
                win32service.SERVICE_AUTO_START,
                win32service.SERVICE_ERROR_NORMAL,
                '"' + str(target) + '"',
                None, 0, "LocalSystem", None,
            )
        else:
            handle = win32service.OpenService(
                scm, CONTROL_SERVICE,
                win32service.SERVICE_QUERY_CONFIG
                | win32service.SERVICE_QUERY_STATUS
                | win32service.SERVICE_START,
            )
        try:
            config = win32service.QueryServiceConfig(handle)
            expected = '"' + str(target) + '"'
            if (config[3].strip('"') != expected.strip('"')
                    or config[7] not in ("LocalSystem", "NT AUTHORITY\\SYSTEM")):
                raise RuntimeError("Служба управления имеет неожиданную конфигурацию.")
            if status != win32service.SERVICE_RUNNING:
                win32service.StartService(handle, None)
        finally:
            win32service.CloseServiceHandle(handle)
    finally:
        win32service.CloseServiceHandle(scm)

    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if win32serviceutil.QueryServiceStatus(CONTROL_SERVICE)[1] == win32service.SERVICE_RUNNING:
            return
        time.sleep(0.2)
    raise RuntimeError("S3DriveControl не перешла в состояние Running.")
