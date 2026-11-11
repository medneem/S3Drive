"""Fail-closed SHA-256 verification for the rclone shipped with S3Drive.

The manifest is generated from the binary in the pinned, SHA-256-verified
upstream rclone archive. Both files ship inside the verified S3Drive ZIP.
"""
from __future__ import annotations

import hashlib
import hmac
import re
from pathlib import Path

_CHUNK_SIZE = 1024 * 1024


def verify_rclone_hash(binary: Path, expected_sha256: str) -> None:
    """Reject a missing or altered executable, including a staged copy."""
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
    """Verify the paired bundled rclone and return its expected digest.

    The sidecar is a consistency check inside the official release package,
    not a digital signature or an antivirus verdict.
    """
    manifest = binary.with_name("rclone.sha256")
    if not manifest.is_file():
        raise RuntimeError("Отсутствует rclone.sha256. Распакуйте полный архив S3Drive.")
    try:
        expected = manifest.read_text(encoding="ascii").strip()
    except (UnicodeError, OSError) as exc:
        raise RuntimeError("Некорректный rclone.sha256 в архиве S3Drive.") from exc
    if re.fullmatch(r"[0-9a-fA-F]{64}", expected) is None:
        raise RuntimeError("Некорректный rclone.sha256 в архиве S3Drive.")
    verify_rclone_hash(binary, expected)
    return expected.lower()
