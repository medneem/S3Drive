"""Human-readable, profile-unique WinFsp network shares for Windows Explorer."""
from __future__ import annotations

import re
import uuid


# rclone 1.75.2 truncates --volname to 32 UTF-8 bytes before passing it
# to WinFsp. Pass the complete UNC prefix with --fuse-flag as well.
_RCLONE_VOLNAME_BYTES = 32


def _network_parts(profile_id: str, display_name: str) -> tuple[str, str]:
    server = "s3d" + uuid.UUID(profile_id).hex[:12]
    # WinFsp's network share component follows Windows filename restrictions.
    share = re.sub(r'[<>:"/\\|?*]', "_", display_name).strip().rstrip(" .")
    return server, share or "S3Drive"


def network_volume_name(profile_id: str, display_name: str) -> str:
    """Short, valid fallback for rclone --volname, without splitting UTF-8."""
    server, share = _network_parts(profile_id, display_name)
    prefix = "\\\\" + server + "\\"
    remaining = _RCLONE_VOLNAME_BYTES - len(prefix.encode("utf-8"))
    short = ""
    for char in share:
        if len((short + char).encode("utf-8")) > remaining:
            break
        short += char
    return prefix + (short.rstrip(" .") or "S3Drive")


def native_network_volume_prefix(profile_id: str, display_name: str) -> str:
    """Untruncated WinFsp UNC prefix: share is the Explorer-visible name.

    WinFsp expects one initial backslash for --VolumePrefix, unlike rclone's
    --volname flag, which accepts a full two-backslash UNC path.
    """
    server, share = _network_parts(profile_id, display_name)
    return "\\" + server + "\\" + share
