"""Read-only VFS disk usage and targeted cleanup of inactive rclone logs.

Never follow symlinks/junctions and never hide inaccessible service paths by
reporting an incorrect zero. This module has no GUI dependencies.
"""
from __future__ import annotations

import os
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from localization import tr, get_language
from profiles import app_data_dir
from windows_service import service_dir


@dataclass(frozen=True)
class StorageUsage:
    total_cache: int
    total_logs: int
    cache_by_profile: dict[str, int | None]
    logs_by_profile: dict[str, int | None]
    cache_incomplete: bool
    logs_incomplete: bool


def _is_link(st: os.stat_result) -> bool:
    return stat.S_ISLNK(st.st_mode) or bool(
        getattr(st, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def measure_path(path: Path) -> int:
    """Count a file or tree without following reparse points or links.

    If a directory cannot be read, propagate its OSError. The caller can
    then mark its whole profile as inaccessible rather than silently omitting
    protected files and claiming that the reported total is complete.
    """
    path = Path(path)
    total = 0
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            meta = current.lstat()
        except FileNotFoundError:
            continue  # A file was removed during an ongoing background scan.
        if _is_link(meta):
            continue
        if not stat.S_ISDIR(meta.st_mode):
            if stat.S_ISREG(meta.st_mode):
                total += meta.st_size
            continue
        try:
            with os.scandir(current) as entries:
                children = [Path(entry.path) for entry in entries]
        except FileNotFoundError:
            continue
        stack.extend(children)
    return total


def profile_cache_dir(profile) -> Path:
    if profile.service_mode:
        return service_dir(profile.id) / "cache"
    return app_data_dir() / "cache" / profile.id


def profile_log_file(profile) -> Path:
    if profile.service_mode:
        return service_dir(profile.id) / "rclone.log"
    return app_data_dir() / "logs" / (profile.id + ".log")


def storage_usage(profiles: Sequence[object]) -> StorageUsage:
    """Calculate the selected profile paths of all known connections."""
    cache: dict[str, int | None] = {}
    logs: dict[str, int | None] = {}
    for profile in profiles:
        # Each profile UUID is counted once, even if duplicated in the input.
        if profile.id in cache:
            continue
        for target, path in ((cache, profile_cache_dir(profile)),
                             (logs, profile_log_file(profile))):
            try:
                target[profile.id] = measure_path(path)
            except OSError:
                target[profile.id] = None
    return StorageUsage(
        total_cache=sum(x for x in cache.values() if x is not None),
        total_logs=sum(x for x in logs.values() if x is not None),
        cache_by_profile=cache,
        logs_by_profile=logs,
        cache_incomplete=any(x is None for x in cache.values()),
        logs_incomplete=any(x is None for x in logs.values()),
    )


def format_size(value: int | None) -> str:
    if value is None:
        return tr("Недоступно")
    if value < 0:
        raise ValueError("Negative storage size")
    units = ("Б", "КБ", "МБ", "ГБ", "ТБ")
    count = float(value)
    level = 0
    while count >= 1024 and level < len(units) - 1:
        count /= 1024
        level += 1
    numeric = str(int(count)) if level == 0 or count.is_integer() else f"{count:.1f}"
    if get_language() == "ru":
        numeric = numeric.replace(".", ",")
    return f"{numeric} {tr(units[level])}"


def format_total(value: int, incomplete: bool) -> str:
    readable = format_size(value)
    return tr("{size} (неполный)", size=readable) if incomplete else readable


def clear_log_file(path: Path) -> bool:
    """Delete exactly one stopped mount's log; never touch links or folders."""
    path = Path(path)
    try:
        meta = path.lstat()
    except FileNotFoundError:
        return False
    if _is_link(meta) or not stat.S_ISREG(meta.st_mode):
        raise RuntimeError("Небезопасный путь журнала. Очистка отменена.")
    path.unlink()
    return True


def clear_user_log(profile_id: str) -> bool:
    """GUI must check that the user mount is stopped before calling this."""
    canonical = str(uuid.UUID(profile_id))
    if canonical != profile_id:
        raise ValueError("Некорректный идентификатор подключения (UUID).")
    return clear_log_file(app_data_dir() / "logs" / (canonical + ".log"))
