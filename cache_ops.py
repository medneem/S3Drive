"""Safe removal of a per-profile rclone VFS disk cache after a clean unmount."""
from __future__ import annotations

import shutil
from pathlib import Path


def ensure_cache_idle(stats: dict) -> None:
    """Fail closed: no queued, running, failed or still-open cached files."""
    disk = stats["diskCache"]
    queued = int(disk["uploadsQueued"])
    uploading = int(disk["uploadsInProgress"])
    failed = int(disk["erroredFiles"])
    open_files = int(stats["inUse"])
    if queued or uploading or failed or open_files:
        raise RuntimeError(
            f"Нельзя очищать кэш: в очереди {queued}, загружаются {uploading}, "
            f"ошибок {failed}, открытых файлов {open_files}. "
            "Закройте файлы и дождитесь завершения передачи в S3."
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
