"""Настройка автозапуска интерфейса S3Drive в пользовательском сеансе.

Служба Windows сама не может показывать окна на рабочем столе (Session 0).
По желанию пользователя включаем HKCU Run без прав администратора.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

RUN_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "S3DriveMonitor"


def _registry():
    import winreg
    return winreg


def startup_command() -> str:
    if getattr(sys, "frozen", False):
        args = [str(Path(sys.executable).resolve()), "--tray"]
    else:
        args = [str(Path(sys.executable).resolve()), str(Path(__file__).resolve().parent / "main.py"), "--tray"]
    return subprocess.list2cmdline(args)


def get_tray_autostart() -> bool:
    registry = _registry()
    try:
        with registry.OpenKey(registry.HKEY_CURRENT_USER, RUN_PATH, 0, registry.KEY_READ) as key:
            value, _ = registry.QueryValueEx(key, VALUE_NAME)
            return value == startup_command()
    except FileNotFoundError:
        return False


def set_tray_autostart(enabled: bool) -> None:
    registry = _registry()
    with registry.CreateKeyEx(
        registry.HKEY_CURRENT_USER, RUN_PATH, 0, registry.KEY_SET_VALUE
    ) as key:
        if enabled:
            registry.SetValueEx(key, VALUE_NAME, 0, registry.REG_SZ, startup_command())
        else:
            try:
                registry.DeleteValue(key, VALUE_NAME)
            except FileNotFoundError:
                pass
