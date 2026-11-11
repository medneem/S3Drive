"""Security regressions for the rclone binary shipped with S3Drive."""
from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from rclone_integrity import verify_bundled_rclone, verify_rclone_hash


class RcloneIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.binary = self.root / "rclone.exe"
        self.manifest = self.root / "rclone.sha256"
        self.data = b"official binary fixture"
        self.binary.write_bytes(self.data)
        self.digest = hashlib.sha256(self.data).hexdigest()
        self.manifest.write_text(self.digest.upper() + "\n", encoding="ascii")

    def test_verified_bundle_returns_expected_digest(self):
        self.assertEqual(verify_bundled_rclone(self.binary), self.digest)

    def test_binary_replacement_is_rejected(self):
        self.binary.write_bytes(b"replaced binary")
        with self.assertRaisesRegex(RuntimeError, "SHA-256"):
            verify_bundled_rclone(self.binary)

    def test_missing_manifest_is_rejected(self):
        self.manifest.unlink()
        with self.assertRaisesRegex(RuntimeError, "rclone.sha256"):
            verify_bundled_rclone(self.binary)

    def test_missing_binary_is_rejected(self):
        self.binary.unlink()
        with self.assertRaisesRegex(RuntimeError, "rclone.exe"):
            verify_bundled_rclone(self.binary)

    def test_invalid_manifest_is_rejected(self):
        for content in ("", "0" * 63, self.digest + " more", "../anotherfile", "\ufeff" + self.digest):
            with self.subTest(content=content):
                self.manifest.write_text(content, encoding="utf-8")
                with self.assertRaisesRegex(RuntimeError, "rclone.sha256"):
                    verify_bundled_rclone(self.binary)

    def test_staged_copy_can_be_verified_with_same_digest(self):
        staged = self.root / "service-rclone.exe.new"
        staged.write_bytes(self.data)
        verify_rclone_hash(staged, self.digest)
        staged.write_bytes(self.data + b"tamper")
        with self.assertRaisesRegex(RuntimeError, "SHA-256"):
            verify_rclone_hash(staged, self.digest)

    def test_mount_never_falls_back_to_an_untrusted_path_executable(self):
        from mounts import find_rclone
        with patch("mounts.__file__", str(self.root / "mounts.py")):
            with patch("mounts.sys.frozen", False, create=True):
                self.assertEqual(find_rclone(), str(self.binary))
                self.binary.unlink()
                with self.assertRaisesRegex(RuntimeError, "rclone.exe"):
                    find_rclone()

    def test_service_install_rejects_changed_binary_before_writing_files(self):
        from windows_service import install_and_start
        profile = MagicMock()
        profile.id = "4ef5373c-fc38-4862-afb7-3fc07022af8e"
        profile.drive = "S:"
        profile.name = "Test S3"
        profile.access_key = "abc"
        profile.secret_key = "xyz"
        profile.region = "us-east-1"
        profile.endpoint = "https://s3.example.invalid"
        self.binary.write_bytes(b"changed")
        with patch("windows_service.service_status", return_value=None):
            with patch("windows_service.check_drive_free"):
                with patch("windows_service.protect_directory") as protect:
                    with self.assertRaisesRegex(RuntimeError, "SHA-256"):
                        install_and_start(profile, self.binary)
        protect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
