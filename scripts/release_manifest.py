"""Create the verified release metadata sidecar for the web-only update fallback."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile

DOWNLOAD_ROOT = "https://github.com/medneem/S3Drive/releases/download/"
STABLE_VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")
MAX_ARCHIVE_BYTES = 300 * 1024 * 1024


def make_manifest(version: str, archive: Path) -> dict:
    """Bind version, filename, size and SHA-256 to the official GitHub URL."""
    if STABLE_VERSION.fullmatch(version) is None:
        raise ValueError("Release version must be stable semver.")
    archive = Path(archive)
    name = f"S3Drive-v{version}-windows-x64.zip"
    if archive.name != name or not zipfile.is_zipfile(archive):
        raise ValueError("Expected a valid official S3Drive release ZIP.")
    with zipfile.ZipFile(archive) as bundle:
        if bundle.read("VERSION.txt").decode("utf-8-sig").strip() != version:
            raise ValueError("Archive version does not match the GitHub release.")
    size = archive.stat().st_size
    if size <= 0 or size > MAX_ARCHIVE_BYTES:
        raise ValueError("Invalid release archive size.")
    digest = hashlib.sha256()
    with archive.open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return {
        "tag_name": f"v{version}",
        "draft": False,
        "prerelease": False,
        "assets": [{
            "name": name,
            "size": size,
            "digest": "sha256:" + digest.hexdigest(),
            "browser_download_url": DOWNLOAD_ROOT + f"v{version}/{name}",
        }],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = make_manifest(args.version, args.archive)
    args.output.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
