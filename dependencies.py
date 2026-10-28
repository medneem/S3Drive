"""Bundled WinFsp installer discovery; installation always requires user action."""
from __future__ import annotations

import os
import sys
from pathlib import Path

WINFSP_INSTALLER = "winfsp-2.2.26215.msi"


def application_directory() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def winfsp_installer() -> Path:
    return application_directory() / WINFSP_INSTALLER


def winfsp_installed() -> bool:
    """Check the documented WinFsp registry installation path, not merely the MSI."""
    if os.name != "nt":
        return False
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WinFsp",
            0, winreg.KEY_READ | winreg.KEY_WOW64_32KEY,
        ) as key:
            directory, _ = winreg.QueryValueEx(key, "InstallDir")
        return (Path(directory) / "bin" / "winfsp-x64.dll").is_file()
    except (OSError, ValueError, TypeError):
        return False
