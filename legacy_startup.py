"""Однократная уборка автозапуска старого пользовательского трея S3Drive.

Ранее настройка добавляла HKCU Run/S3DriveMonitor с аргументом --tray.
Нынешняя версия использует только обычное GUI и независимую службу SCM.
"""
from __future__ import annotations

import os

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "S3DriveMonitor"


def remove_legacy_tray_autostart(registry=None) -> bool:
    """Удалить только созданную прежними версиями запись автозапуска.

    Никаких ключей не создаём; неродственные значения не трогаем.
    """
    if registry is None:
        if os.name != "nt":
            return False
        import winreg
        registry = winreg
    try:
        with registry.OpenKey(
            registry.HKEY_CURRENT_USER, RUN_KEY, 0,
            registry.KEY_QUERY_VALUE | registry.KEY_SET_VALUE,
        ) as key:
            try:
                value, _kind = registry.QueryValueEx(key, VALUE_NAME)
            except FileNotFoundError:
                return False
            if not isinstance(value, str):
                return False
            command = value.casefold()
            if ("--tray" not in command.split()
                    or not ("s3drive.exe" in command or "main.py" in command)):
                return False
            registry.DeleteValue(key, VALUE_NAME)
            return True
    except FileNotFoundError:
        return False
