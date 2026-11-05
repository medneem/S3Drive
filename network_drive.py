"""Unique WinFsp network share path per S3Drive profile."""
from __future__ import annotations

import uuid


def network_volume_name(profile_id: str) -> str:
    """Short stable UNC share, so --volname survives rclone 1.75.2 32-byte limit.

    Use a deterministic share per profile (not per drive letter or display name)
    to avoid collisions when multiple S3Drive services run at once.
    """
    return "\\\\S3Drive\\d" + uuid.UUID(profile_id).hex[:16]
