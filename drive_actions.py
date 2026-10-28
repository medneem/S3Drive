"""Windows disk readiness, desktop shortcut and post-mount operations."""
from __future__ import annotations

import os
import re
from enum import Enum
from pathlib import Path


class MountState(Enum):
    WAITING = "waiting"
    READY = "ready"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


def mount_state(*, process_alive: bool, drive_ready: bool,
                elapsed_seconds: float, timeout_seconds: float = 40) -> MountState:
    """Never report success just because the rclone process started."""
    if not process_alive:
        return MountState.FAILED
    if drive_ready:
        return MountState.READY
    if elapsed_seconds >= timeout_seconds:
        return MountState.TIMED_OUT
    return MountState.WAITING


def disk_root(drive: str) -> str:
    if not re.fullmatch(r"[A-Z]:", drive):
        raise ValueError("Некорректная буква диска.")
    return drive + "\\"


def create_desktop_shortcut(drive: str, shell=None) -> tuple[Path, bool]:
    """Add a shortcut to Windows' actual Desktop (including redirected Desktops).

    Never overwrite an existing shortcut. The desktop location comes from
    Windows Shell, not a hard-coded user profile path.
    """
    root = disk_root(drive)
    if shell is None:
        if os.name != "nt":
            raise OSError("Ярлыки поддерживаются только в Windows.")
        from win32com.client import Dispatch
        shell = Dispatch("WScript.Shell")
    desktop = Path(shell.SpecialFolders("Desktop"))
    destination = desktop / f"S3Drive ({drive[0]}).lnk"
    if destination.exists():
        return destination, False
    shortcut = shell.CreateShortcut(str(destination))
    shortcut.TargetPath = root
    shortcut.Description = f"Диск S3Drive {drive}"
    shortcut.Save()
    return destination, True


def open_mounted_disk(drive: str, startfile=None) -> None:
    """Open the mounted root in Windows Explorer."""
    if startfile is None:
        if os.name != "nt":
            raise OSError("Открытие диска поддерживается только в Windows.")
        startfile = os.startfile
    startfile(disk_root(drive))
