"""User-initiated elevation for installing/stopping Windows SCM services."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def helper_executable() -> Path:
    root = (Path(sys.executable).resolve().parent if getattr(sys, "frozen", False)
            else Path(__file__).resolve().parent)
    return root / "S3DriveAdmin.exe"


def run_admin(action: str, profile_id: str, wait_ms: int = 90000) -> None:
    """An explicit GUI click is required before calling this function."""
    if action not in ("install", "stop", "remove"):
        raise ValueError("Недопустимое действие службы.")
    import uuid
    uuid.UUID(profile_id)
    if os.name != "nt":
        raise OSError("Доступно только в Windows.")
    from win32com.shell import shell, shellcon
    import win32con
    import win32event
    import win32process

    helper = helper_executable()
    if not helper.is_file():
        raise FileNotFoundError("S3DriveAdmin.exe отсутствует. Распакуйте полный архив.")
    proc = shell.ShellExecuteEx(
        fMask=shellcon.SEE_MASK_NOCLOSEPROCESS,
        lpVerb="runas", lpFile=str(helper),
        lpParameters=action + " " + profile_id,
        lpDirectory=str(helper.parent), nShow=win32con.SW_HIDE,
    )
    handle = proc["hProcess"]
    try:
        result = win32event.WaitForSingleObject(handle, wait_ms)
        if result != win32con.WAIT_OBJECT_0:
            raise TimeoutError("Управление службой не завершилось вовремя.")
        code = win32process.GetExitCodeProcess(handle)
        if code != 0:
            raise RuntimeError("Операция службы не завершена. Смотрите окно ошибки помощника.")
    finally:
        import win32api
        win32api.CloseHandle(handle)
