"""One-time elevated installation of the protected S3DriveControl broker."""
from __future__ import annotations

import os
import time
import zipfile
from pathlib import Path

from windows_service import protect_directory, protect_file, service_executable_dir

CONTROL_EXE = "S3DriveControl.exe"
CONTROL_SERVICE = "S3DriveControl"


def install_control_service(source_archive: Path) -> None:
    """Install the embedded broker from Admin's verified onefile data archive.

    Keeping the broker inside S3DriveAdmin preserves the exact 11-file ZIP
    contract required by the updater in already installed S3Drive v1.5.4.
    """
    if os.name != "nt":
        raise OSError("Службы доступны только в Windows.")
    import pywintypes
    import win32service
    import win32serviceutil

    if source_archive.is_symlink() or not source_archive.is_file():
        raise FileNotFoundError("В S3DriveAdmin.exe отсутствует компонент службы управления.")
    source_archive = source_archive.resolve()
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
            with zipfile.ZipFile(source_archive) as bundle:
                members = bundle.infolist()
                if (len(members) != 1
                        or members[0].filename != CONTROL_EXE
                        or members[0].is_dir()
                        or not 0 < members[0].file_size <= 128 * 1024 * 1024):
                    raise ValueError("Некорректный пакет системного компонента.")
                with bundle.open(members[0]) as incoming, temporary.open("wb") as output:
                    size = 0
                    while chunk := incoming.read(1024 * 1024):
                        size += len(chunk)
                        if size > 128 * 1024 * 1024:
                            raise ValueError("Слишком большой пакет системного компонента.")
                        output.write(chunk)
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
