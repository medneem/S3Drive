"""Windows Explorer drive-label refresh and native WinFsp label diagnostics."""
from __future__ import annotations

import ctypes
import os
import re


def notify_explorer() -> None:
    """Force Shell to re-read labels after changing DriveIcons registry keys."""
    if os.name != "nt":
        return
    # SHCNE_ASSOCCHANGED / SHCNF_IDLIST — refresh Shell drive metadata.
    ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)


def read_native_volume_label(drive: str) -> str | None:
    """GetVolumeInformationW reports the actual filesystem label, not tile text."""
    if not re.fullmatch(r"[A-Z]:", drive):
        raise ValueError("Некорректная буква диска.")
    if os.name != "nt":
        return None
    label = ctypes.create_unicode_buffer(256)
    ok = ctypes.windll.kernel32.GetVolumeInformationW(
        drive + "\\", label, len(label),
        None, None, None, None, 0,
    )
    return label.value if ok else None
