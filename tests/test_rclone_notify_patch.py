"""Guard isolated upstream patch against drift and unintended notification on failure."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.patch_rclone_notify_experiment import patch_tree


class RcloneNotificationPatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        directory = self.root / "cmd" / "cmount"
        directory.mkdir(parents=True)
        self.fs = directory / "fs.go"
        self.mount = directory / "mount.go"
        self.fs.write_text(
            "type FS struct {\n\tready     chan (struct{})\n}\n"
            "func (fsys *FS) Rmdir(dirPath string) (errc int) {\n"
            "\treturn translateError(parentDir.RemoveName(leaf))\n}\n\n"
            "// Rename renames a file.\n",
            encoding="utf-8",
        )
        self.mount.write_text(
            "host := ignored\n\thost := fuse.NewFileSystemHost(fsys)\n",
            encoding="utf-8",
        )

    def test_successful_delete_gets_exactly_one_directory_notification(self):
        patch_tree(self.root)
        source = self.fs.read_text(encoding="utf-8")
        self.assertIn("if err == nil && runtime.GOOS == \"windows\"", source)
        self.assertIn("fsys.notifyHost.Notify(dirPath, fuse.NOTIFY_RMDIR)", source)
        self.assertEqual(source.count("fuse.NOTIFY_RMDIR"), 1)
        self.assertIn("fsys.notifyHost = host", self.mount.read_text(encoding="utf-8"))

    def test_upstream_drift_aborts_without_partially_modifying_source(self):
        self.mount.write_text("host := absent\n", encoding="utf-8")
        before_fs = self.fs.read_bytes()
        before_mount = self.mount.read_bytes()
        with self.assertRaisesRegex(ValueError, "cmount host binding"):
            patch_tree(self.root)
        self.assertEqual(self.fs.read_bytes(), before_fs)
        self.assertEqual(self.mount.read_bytes(), before_mount)


if __name__ == "__main__":
    unittest.main()
