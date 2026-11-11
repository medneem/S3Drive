"""GitHub stable-release discovery and verified portable-update staging.

Network operations run in a Qt worker, never on the GUI thread. An update is
downloaded ONLY after the user clicks the Update button.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.request import Request, urlopen
import json
import zipfile
from typing import Callable

RELEASE_API = "https://api.github.com/repos/medneem/S3Drive/releases/latest"
RELEASE_LATEST_PAGE = "https://github.com/medneem/S3Drive/releases/latest"
RELEASE_PAGE_ROOT = "https://github.com/medneem/S3Drive/releases/tag/v"
RELEASE_MANIFEST = "S3Drive-update.json"
MAX_METADATA_BYTES = 1024 * 1024
RELEASE_DOWNLOAD_ROOT = "https://github.com/medneem/S3Drive/releases/download/"
USER_AGENT = "S3Drive-Desktop-Updater"
MAX_ARCHIVE_BYTES = 300 * 1024 * 1024
MAX_UNPACKED_BYTES = 600 * 1024 * 1024
PAYLOAD_FILES = frozenset({
    "S3Drive.exe", "S3DriveAdmin.exe", "S3DriveUpdater.exe", "rclone.exe",
    "winfsp-2.2.26215.msi", "README_SETUP.txt",
    "RCLONE_LICENSE.txt", "app-icon.png", "LICENSE",
    "THIRD_PARTY_NOTICES.md", "VERSION.txt",
})
_STABLE = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")
_SHA256 = re.compile(r"sha256:([a-fA-F0-9]{64})\Z")


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    url: str
    digest: str
    size: int


@dataclass(frozen=True)
class StagedUpdate:
    archive: Path
    digest: str
    version: str
    directory: Path


def _version_parts(version: str) -> tuple[int, int, int] | None:
    match = _STABLE.fullmatch(version)
    if match is None:
        return None
    return tuple(map(int, match.groups()))


def parse_release(data: dict, installed_version: str) -> UpdateInfo | None:
    """Ignore non-stable and old releases; reject malformed release assets."""
    current = _version_parts(installed_version)
    if current is None:
        return None
    if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
        return None
    tag = data.get("tag_name")
    if not isinstance(tag, str) or not tag.startswith("v"):
        return None
    version = tag[1:]
    offered = _version_parts(version)
    if offered is None or offered <= current:
        return None

    filename = f"S3Drive-v{version}-windows-x64.zip"
    assets = data.get("assets")
    if not isinstance(assets, list):
        raise ValueError("В релизе отсутствует список файлов.")
    matches = [a for a in assets if isinstance(a, dict) and a.get("name") == filename]
    if len(matches) != 1:
        raise ValueError("Не найден единственный официальный ZIP-архив S3Drive.")
    asset = matches[0]
    expected_url = RELEASE_DOWNLOAD_ROOT + f"v{version}/{filename}"
    if asset.get("browser_download_url") != expected_url:
        raise ValueError("Некорректная ссылка на обновление.")
    digest = asset.get("digest")
    match = _SHA256.fullmatch(digest) if isinstance(digest, str) else None
    if not match:
        raise ValueError("GitHub не предоставил корректный SHA-256 архива.")
    size = asset.get("size")
    if type(size) is not int or not 0 < size <= MAX_ARCHIVE_BYTES:
        raise ValueError("Недопустимый размер обновления.")
    return UpdateInfo(version, expected_url, match.group(1).lower(), size)


def _read_release_json(request: Request, opener) -> dict:
    """Read bounded release metadata without downloading the ZIP."""
    with opener(request, timeout=10) as response:
        raw = response.read(MAX_METADATA_BYTES + 1)
    if len(raw) > MAX_METADATA_BYTES:
        raise ValueError("Слишком большой ответ сервера обновлений.")
    return json.loads(raw)


def _check_release_on_github_page(current_version: str, opener) -> UpdateInfo | None:
    """Fallback: use the official GitHub page and a SHA-256 manifest asset.

    GitHub's web host is often accessible even when api.github.com times out.
    Never download or install a ZIP without the exact expected digest.
    """
    request = Request(RELEASE_LATEST_PAGE, headers={"User-Agent": USER_AGENT})
    with opener(request, timeout=10) as response:
        final_url = response.geturl()
    match = re.fullmatch(
        re.escape(RELEASE_PAGE_ROOT)
        + r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)/?",
        final_url,
    )
    if match is None:
        raise ValueError("Не удалось определить стабильную версию на странице GitHub.")
    version = ".".join(match.groups())
    if _version_parts(version) <= _version_parts(current_version):
        return None

    manifest_url = RELEASE_DOWNLOAD_ROOT + f"v{version}/{RELEASE_MANIFEST}"
    metadata = _read_release_json(
        Request(manifest_url, headers={"User-Agent": USER_AGENT}), opener
    )
    if not isinstance(metadata, dict) or metadata.get("tag_name") != f"v{version}":
        raise ValueError("Версия манифеста обновления не совпадает с GitHub Release.")
    update = parse_release(metadata, current_version)
    if update is None:
        raise ValueError("Резервный манифест не содержит допустимого обновления.")
    return update


def check_latest_release(current_version: str, *, opener=urlopen) -> UpdateInfo | None:
    """Check GitHub API first; fall back to its release page on network failure."""
    if _version_parts(current_version) is None:
        return None
    request = Request(
        RELEASE_API,
        headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"},
    )
    try:
        data = _read_release_json(request, opener)
    except OSError as api_error:
        try:
            return _check_release_on_github_page(current_version, opener)
        except OSError as web_error:
            raise ConnectionError(
                f"GitHub API: {api_error}; GitHub Releases: {web_error}"
            ) from web_error
    return parse_release(data, current_version)


def verify_archive(path: Path, digest: str, version: str) -> None:
    """Validate full ZIP hash, exact file allowlist and stamped version."""
    if not re.fullmatch("[0-9a-fA-F]{64}", digest):
        raise ValueError("Некорректный SHA-256.")
    actual = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            actual.update(chunk)
    if actual.hexdigest() != digest.lower():
        raise ValueError("Не совпадает SHA-256 загруженного архива.")
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        names = [item.filename for item in infos]
        if len(names) != len(PAYLOAD_FILES) or set(names) != PAYLOAD_FILES:
            raise ValueError("Недопустимый набор файлов обновления.")
        if any(item.is_dir() or item.file_size > MAX_UNPACKED_BYTES
               or ((item.external_attr >> 16) & 0o170000) == 0o120000
               for item in infos):
            raise ValueError("Недопустимый тип файла обновления.")
        if sum(item.file_size for item in infos) > MAX_UNPACKED_BYTES:
            raise ValueError("Слишком большой распакованный архив.")
        actual_version = archive.read("VERSION.txt").decode("utf-8-sig").strip()
        if actual_version != version:
            raise ValueError("Версия в архиве не совпадает с версией релиза.")


def stage_update(update: UpdateInfo, *, opener=urlopen,
                 parent: Path | None = None,
                 on_progress: Callable[[int, int], None] | None = None,
                 on_verifying: Callable[[], None] | None = None) -> StagedUpdate:
    """Download only on user request, verify before any local program changes."""
    staging = Path(tempfile.mkdtemp(prefix="S3Drive-update-", dir=parent))
    archive = staging / "update.zip"
    try:
        request = Request(update.url, headers={"User-Agent": USER_AGENT})
        written = 0
        with opener(request, timeout=30) as response, archive.open("wb") as target:
            while data := response.read(1024 * 1024):
                written += len(data)
                if written > MAX_ARCHIVE_BYTES or written > update.size:
                    raise ValueError("Размер загрузки превысил размер релиза.")
                target.write(data)
                if on_progress is not None:
                    on_progress(written, update.size)
        if written != update.size:
            raise ValueError("Загружен неполный архив.")
        if on_verifying is not None:
            on_verifying()
        verify_archive(archive, update.digest, update.version)
        return StagedUpdate(archive, update.digest, update.version, staging)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def discard_update(staged: StagedUpdate) -> None:
    """Delete only staging directory owned by this update."""
    shutil.rmtree(staged.directory, ignore_errors=True)


def launch_updater(staged: StagedUpdate, install_root: Path,
                   *, parent_pid: int, popen=subprocess.Popen):
    """Run copied helper after close; never overwrite a running updater binary."""
    root = Path(install_root).resolve()
    helper = root / "S3DriveUpdater.exe"
    if not (root / "S3Drive.exe").is_file() or not helper.is_file():
        raise FileNotFoundError("Не найдены исполняемые файлы S3Drive или обновлятора.")
    child = staged.directory / "S3DriveUpdater.exe"
    shutil.copy2(helper, child)
    args = [
        str(child), "--archive", str(staged.archive),
        "--sha256", staged.digest, "--version", staged.version,
        "--destination", str(root), "--parent-pid", str(parent_pid),
    ]
    options = {"cwd": str(staged.directory)}
    if os.name == "nt":
        options["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return popen(args, **options)
