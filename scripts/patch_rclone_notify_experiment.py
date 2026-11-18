"""Patch *pinned* rclone v1.76.0 for an isolated WinFsp notification experiment.

This does not modify S3Drive's official bundled binary or any production
service. The caller must verify the upstream Git commit SHA before invoking.
The exact source patterns deliberately fail closed when rclone changes.
"""
from __future__ import annotations

import argparse
from pathlib import Path


def replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise ValueError(f"{label}: expected exactly one upstream match, found {count}")
    return source.replace(old, new)


def patch_tree(root: Path) -> None:
    fs_path = root / "cmd" / "cmount" / "fs.go"
    mount_path = root / "cmd" / "cmount" / "mount.go"
    original_fs = fs_path.read_text(encoding="utf-8")
    original_mount = mount_path.read_text(encoding="utf-8")

    fs_code = replace_once(
        original_fs,
        "\tready     chan (struct{})\n",
        "\tready     chan (struct{})\n\tnotifyHost *fuse.FileSystemHost // experiment: Windows directory changes\n",
        "cmount FS host member",
    )
    fs_code = replace_once(
        fs_code,
        """\treturn translateError(parentDir.RemoveName(leaf))
}

// Rename renames a file.""",
        """\terr := parentDir.RemoveName(leaf)
\tif err == nil && runtime.GOOS == "windows" && fsys.notifyHost != nil {
\t\tif !fsys.notifyHost.Notify(dirPath, fuse.NOTIFY_RMDIR) {
\t\t\tfs.Debugf(dirPath, "WinFsp RMDIR notification unavailable")
\t\t}
\t}
\treturn translateError(err)
}

// Rename renames a file.""",
        "cmount Rmdir notification",
    )
    mount_code = replace_once(
        original_mount,
        "\thost := fuse.NewFileSystemHost(fsys)\n",
        "\thost := fuse.NewFileSystemHost(fsys)\n\tfsys.notifyHost = host\n",
        "cmount host binding",
    )
    # All patches must be valid before writing any source.
    fs_path.write_text(fs_code, encoding="utf-8", newline="\n")
    mount_path.write_text(mount_code, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Verified rclone v1.76.0 checkout")
    arguments = parser.parse_args()
    patch_tree(arguments.source)
