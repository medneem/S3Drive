"""Windows Explorer drive-label refresh and native WinFsp label diagnostics."""
from __future__ import annotations

import ctypes
import os
import re

from network_drive import native_network_volume_prefix


def network_label_registry_path(profile_id: str, display_name: str) -> str:
    """Explorer's per-user MountPoints2 entry for this exact WinFsp UNC share."""
    prefix = native_network_volume_prefix(profile_id, display_name)
    # WinFsp receives a single leading slash, while Explorer indexes UNC
    # shares with two leading slashes, encoded as ##server#share.
    encoded_unc = ("\\" + prefix).replace("\\", "#")
    return (
        r"Software\Microsoft\Windows\CurrentVersion\Explorer\MountPoints2"
        + "\\" + encoded_unc
    )


def install_explorer_network_label(profile_id: str, display_name: str) -> bool:
    """Name a WinFsp network mount like an Explorer user Rename operation.

    HKLM DriveIcons changes the volume title, but Explorer still adds a
    server name in parentheses for network drives. HKCU MountPoints2's
    _LabelFromReg is the per-user network drive name override, without
    the server suffix. Keep the opaque UNC server unchanged for uniqueness.
    """
    if os.name != "nt":
        return False
    import winreg

    path = network_label_registry_path(profile_id, display_name)
    with winreg.CreateKeyEx(
        winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_READ | winreg.KEY_WRITE
    ) as key:
        try:
            current, _ = winreg.QueryValueEx(key, "_LabelFromReg")
        except FileNotFoundError:
            current = None
        if current == display_name:
            return False
        winreg.SetValueEx(key, "_LabelFromReg", 0, winreg.REG_SZ, display_name)
    notify_explorer()
    return True


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
