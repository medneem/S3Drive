"""Windows service integration: global mount command and safe shutdown policy."""
import base64
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from profiles import new_profile
from windows_service import (
    SERVICE_PREFIX, config_text, ensure_vfs_idle, service_command, service_name,
    windows_command_line,
)


def sample():
    return new_profile(
        name="Тест S3", endpoint="http://nas.local:8010", bucket="my-backup",
        drive="S:", access_key="unit-key", secret_key="unit-secret",
    )


class ServiceTests(unittest.TestCase):
    def test_service_name_is_stable_per_profile(self):
        p = sample()
        self.assertEqual(service_name(p.id), SERVICE_PREFIX + p.id.replace("-", ""))

    def test_service_command_uses_explicit_s3_config_without_cli_credentials(self):
        p = sample()
        cmd = service_command(
            p, Path("C:/Program Files/S3Drive/rclone.exe"),
            Path("C:/ProgramData/S3Drive/rclone.conf"),
            Path("C:/ProgramData/S3Drive/cache"),
            Path("C:/ProgramData/S3Drive/rclone.log"),
            Path("C:/ProgramData/S3Drive/htpasswd"), 45333,
        )
        self.assertIn(" mount ", cmd)
        self.assertIn("S:", cmd)
        self.assertIn("--vfs-cache-mode writes", cmd)
        self.assertIn("--rc-htpasswd", cmd)
        # rclone/WinFsp: FRFW grants read/write, but Windows Explorer\n        # requires DELETE access for removing files and directories.\n        self.assertIn("FileSecurity=D:P(A;;FA;;;WD)", cmd)\n        self.assertNotIn("FileSecurity=D:P(A;;FRFW;;;WD)", cmd)
        self.assertNotIn(p.secret_key, cmd)
        self.assertNotIn(p.access_key, cmd)

    def test_service_config_has_correct_qnap_path_style(self):
        p = sample()
        config = config_text(p)
        self.assertIn("force_path_style = true", config)
        self.assertIn("endpoint = http://nas.local:8010", config)

    def test_configuration_rejects_crlf_in_credentials(self):
        p = sample()
        p.secret_key = "abc\nsecret"
        with self.assertRaises(ValueError):
            config_text(p)

    def test_acl_protect_uses_real_pywin32_file_access_mask(self):
        # Regression for S3Drive v0.1.18:
        # AttributeError: module 'win32con' has no attribute 'FILE_ALL_ACCESS'.
        import os
        if os.name != "nt":
            self.skipTest("Windows-only pywin32 ACL test")
        import win32file
        import win32security
        from windows_service import _protect_acl
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / "s3-secret.conf"
            file.write_text("test", encoding="utf-8")
            with patch("win32security.SetNamedSecurityInfo") as setter:
                _protect_acl(file, directory=False)
            acl = setter.call_args.args[5]
            self.assertEqual(acl.GetAceCount(), 2)
            for i in range(2):
                self.assertEqual(acl.GetAce(i)[1], win32file.FILE_ALL_ACCESS)

    def test_stop_fails_with_pending_uploads(self):
        with patch("windows_service._control_request", return_value={
            "diskCache": {"uploadsQueued": 1, "uploadsInProgress": 0, "erroredFiles": 0}
        }):
            with self.assertRaisesRegex(RuntimeError, "Нельзя остановить"):
                ensure_vfs_idle({"port": 42, "password": "secret"})

    def test_stop_fails_if_no_disk_cache_status(self):
        with patch("windows_service._control_request", return_value={}):
            with self.assertRaises(KeyError):
                ensure_vfs_idle({"port": 42, "password": "secret"})

    def test_stop_allowed_when_queue_empty(self):
        with patch("windows_service._control_request", return_value={
            "diskCache": {"uploadsQueued": 0, "uploadsInProgress": 0, "erroredFiles": 0}
        }):
            ensure_vfs_idle({"port": 42, "password": "secret"})


if __name__ == "__main__":
    unittest.main()
