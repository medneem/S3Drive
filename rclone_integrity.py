"""Fail-closed integrity check for S3Drive's official bundled rclone.

This SHA-256 is pinned for the upstream rclone v1.76.0 Windows amd64 EXE,
obtained from the pinned, SHA-256-verified official ZIP in CI. New upstream
versions require an explicit reviewed digest update.
"""
from __future__ import annotations

import hashlib
import hmac
from pathlib import Path

RCLONE_VERSION = "1.76.0"
RCLONE_SHA256 = "acd80683e23151e93bbe0d6ffa74d26e542ec510332f630beecb9944f8d1a623"
_CHUNK_SIZE = 1024 * 1024


def verify_rclone_hash(binary: Path, expected_sha256: str) -> None:
    """Reject a missing or altered executable, including staged service copies."""
    if not binary.is_file():
        raise RuntimeError(f"rclone.exe не найден: {binary}")
    digest = hashlib.sha256()
    with binary.open("rb") as stream:
        for chunk in iter(lambda: stream.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    if not hmac.compare_digest(digest.hexdigest(), expected_sha256.lower()):
        raise RuntimeError(
            "Неверный SHA-256 rclone.exe. Не запускайте этот файл: "
            "скачайте полный официальный архив S3Drive заново."
        )


def verify_bundled_rclone(binary: Path) -> str:
    """Verify the bundled rclone against its compiled-in official checksum."""
    verify_rclone_hash(binary, RCLONE_SHA256)
    return RCLONE_SHA256
