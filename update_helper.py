"""Standalone updater for the portable Windows distribution.

This program is copied into the download staging directory before execution.
It waits until the GUI process exits, validates the archive *again*, replaces
only a strict allowlist, rolls back on failure, and relaunches S3Drive.
It never stops services, deletes caches, or writes config.json.
"""
from __future__ import annotations

import argparse
import ctypes
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile

from auto_update import PAYLOAD_FILES, verify_archive


def wait_for_parent_exit(pid: int, *, timeout_ms: int = 120_000) -> None:
    """Wait for the exact GUI process HANDLE (not a PID polling race)."""
    if os.name != "nt":
        raise OSError("Автоматическая установка поддерживается только Windows.")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = (ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong)
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_ulong)
    kernel.WaitForSingleObject.restype = ctypes.c_ulong
    kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
    kernel.CloseHandle.restype = ctypes.c_int

    handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
    if not handle:
        raise RuntimeError("Не удалось убедиться, что S3Drive завершён.")
    try:
        if kernel.WaitForSingleObject(handle, timeout_ms) != 0:  # WAIT_OBJECT_0
            raise TimeoutError("S3Drive не завершился, обновление отменено.")
    finally:
        kernel.CloseHandle(handle)


def apply_bundle(archive_path: Path, digest: str, version: str, root: Path) -> None:
    """Replace only shipped binaries/assets; restore originals on any error."""
    root = Path(root).resolve()
    if not root.is_dir() or not (root / "S3Drive.exe").is_file():
        raise ValueError("Некорректная папка установки S3Drive.")
    verify_archive(Path(archive_path), digest, version)
    work = Path(tempfile.mkdtemp(prefix=".s3drive-replace-", dir=root))
    before: dict[str, bool] = {}
    applied: list[str] = []
    try:
        incoming = work / "new"
        backups = work / "old"
        incoming.mkdir()
        backups.mkdir()
        with zipfile.ZipFile(archive_path) as archive:
            for name in sorted(PAYLOAD_FILES):
                path = root / name
                if path.is_symlink():
                    raise RuntimeError(f"Файл программы является ссылкой: {name}")
                before[name] = path.is_file()
                if path.exists() and not before[name]:
                    raise RuntimeError(f"Невозможно заменить каталог: {name}")
                if before[name]:
                    shutil.copy2(path, backups / name)
                with archive.open(name) as source, (incoming / name).open("wb") as target:
                    shutil.copyfileobj(source, target, length=1024 * 1024)

        try:
            for name in sorted(PAYLOAD_FILES):
                os.replace(incoming / name, root / name)
                applied.append(name)
        except OSError as exc:
            rollback_errors = []
            for name in reversed(applied):
                try:
                    if before[name]:
                        os.replace(backups / name, root / name)
                    else:
                        (root / name).unlink(missing_ok=True)
                except OSError as rollback_exc:
                    rollback_errors.append(f"{name}: {rollback_exc}")
            if rollback_errors:
                raise RuntimeError(
                    f"Не удалось обновить ({exc}); откат не завершён: "
                    + "; ".join(rollback_errors)
                ) from exc
            raise
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _show_error(message: str) -> None:
    if os.name == "nt":
        ctypes.windll.user32.MessageBoxW(
            None, message, "S3Drive — ошибка обновления", 0x10
        )
    else:
        print(message, file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--parent-pid", type=int, required=True)
    args = parser.parse_args()
    try:
        wait_for_parent_exit(args.parent_pid)
        root = Path(args.destination).resolve()
        apply_bundle(Path(args.archive), args.sha256, args.version, root)
        # The updated GUI reads config.json in the same folder.
        subprocess.Popen([str(root / "S3Drive.exe")], cwd=str(root))
        # Updating never modifies service files under ProgramData.
        try:
            Path(args.archive).unlink()
        except OSError:
            pass
    except Exception as exc:
        _show_error(
            f"Не удалось установить S3Drive v{args.version}:\n{exc}\n\n"
            "Работающие S3-диски и VFS-кэш не отключались. "
            "При ошибке замены файлов выполнен откат."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
