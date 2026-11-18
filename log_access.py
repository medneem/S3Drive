"""Locate rclone logs and suppress duplicate transfer-error notifications."""
from __future__ import annotations

from pathlib import Path

from profiles import app_data_dir
from windows_service import service_dir


def profile_log_path(profile) -> Path:
    """Service logs are stored outside the interactive user's log directory."""
    if profile.service_mode:
        return service_dir(profile.id) / "rclone.log"
    return app_data_dir() / "logs" / f"{profile.id}.log"


class TransferErrorNotices:
    """Show one warning per positive error-count change, per profile.

    Acknowledging hides the current warning without concealing subsequent
    errors. Counter reset (zero) starts a fresh episode.
    """

    def __init__(self):
        self._counts: dict[str, int] = {}
        self._pending: set[str] = set()

    def observe(self, profile_id: str, errors: int) -> bool:
        if errors <= 0:
            self._counts.pop(profile_id, None)
            self._pending.discard(profile_id)
            return False
        previous = self._counts.get(profile_id)
        if previous is None or errors > previous:
            self._counts[profile_id] = errors
            self._pending.add(profile_id)
            return True
        # An rclone restart can reset the count before the next poll. Avoid
        # treating a lower nonzero value as a new error until a zero is seen.
        return False

    def pending(self, profile_id: str) -> bool:
        return profile_id in self._pending

    def acknowledge(self, profile_id: str) -> None:
        self._pending.discard(profile_id)
