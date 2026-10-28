"""WinFsp startup policy, separated from the GUI for deterministic testing."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import Callable


class StartupStatus(Enum):
    READY = auto()
    INSTALLER_STARTED = auto()
    DECLINED = auto()
    INSTALLER_MISSING = auto()
    INSTALLER_FAILED = auto()


@dataclass(frozen=True)
class StartupOutcome:
    status: StartupStatus
    error: str | None = None


def ensure_winfsp(
    installed: Callable[[], bool],
    installer: Path,
    confirm: Callable[[], bool],
    launch: Callable[[Path], None],
) -> StartupOutcome:
    """Continue only with WinFsp present; never install without approval.

    After launching MSI the application closes; a subsequent launch rechecks
    driver readiness, since installation may require a restart.
    """
    if installed():
        return StartupOutcome(StartupStatus.READY)
    if not installer.is_file():
        return StartupOutcome(StartupStatus.INSTALLER_MISSING)
    if not confirm():
        return StartupOutcome(StartupStatus.DECLINED)
    try:
        launch(installer)
    except OSError as exc:
        return StartupOutcome(StartupStatus.INSTALLER_FAILED, str(exc))
    return StartupOutcome(StartupStatus.INSTALLER_STARTED)
