"""Safe removal of a per-profile rclone VFS disk cache after a clean unmount."""
from __future__ import annotations

import shutil
from pathlib import Path


def ensure_uploads_idle(stats: dict) -> None:
    """Allow unmount only when VFS has no pending/active/failed S3 writes.

    rclone v1.75.2 initialises VFS.inUse to 1 even when no file is open.
    That counter tracks VFS references, not open file handles; it must
    never be treated as a number of open files for normal unmount.
    """
    disk = stats["diskCache"]
    queued = int(disk["uploadsQueued"])
    uploading = int(disk["uploadsInProgress"])
    failed = int(disk["erroredFiles"])
    if queued or uploading or failed:
        raise RuntimeError(
            f"В очереди {queued}, загружаются {uploading}, "
            f"ошибок загрузки {failed}. "
            "Закройте изменяемые файлы и дождитесь отправки в S3."
        )


def ensure_cache_idle(stats: dict) -> None:
    """Conservative precheck for DESTRUCTIVE cache deletion.

    Regular unmount uses ensure_uploads_idle() instead. Unlike a stop,
    deleting the cache can irreversibly discard buffered writes, so
    additional VFS references beyond its initial own reference are
    treated as ambiguous and deletion is refused.
    """
    ensure_uploads_idle(stats)
    references = int(stats["inUse"])
    if references > 1:
        raise RuntimeError(
            f"Нельзя очищать кэш: дополнительных ссылок VFS {references - 1}. "
            "Закройте файлы и дождитесь завершения операций с диском."
        )


def erase_cache_directory(path: Path, *, parent: Path) -> bool:
    """Remove only a known child of the configured private cache directory.

    Caller must already have verified a clean VFS and stopped rclone.
    Never traverse symlink/junction directories or arbitrary user paths.
    """
    path = Path(path)
    parent = Path(parent)
    if (path.parent != parent or not path.name or path.name in {".", ".."}):
        raise ValueError("Недопустимый путь к кэшу.")
    for candidate in (parent, path):
        if candidate.is_symlink() or (hasattr(candidate, "is_junction") and candidate.is_junction()):
            raise RuntimeError("Очистка запрещена: каталог кэша является ссылкой.")
    if not path.exists():
        return False
    if not path.is_dir():
        raise RuntimeError("Путь кэша не является каталогом.")
    shutil.rmtree(path)
    return True
