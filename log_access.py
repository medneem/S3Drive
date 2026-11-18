"""Resolve per-profile rclone diagnostic log paths without exposing credentials."""
from __future__ import annotations

from pathlib import Path

from profiles import app_data_dir
from windows_service import service_dir


def profile_log_path(profile) -> Path:
    """Service logs stay in restricted ProgramData; user logs stay in AppData."""
    if profile.service_mode:
        return service_dir(profile.id) / "rclone.log"
    return app_data_dir() / "logs" / f"{profile.id}.log"
