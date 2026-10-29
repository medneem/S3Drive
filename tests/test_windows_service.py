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

    def test_volume_label_uses_exact_profile_name_in_service(self):
        profile = sample()
        profile.name = "Личный сетевой диск"
        command = service_command(
            profile,
            Path("C:/ProgramData/S3Drive/bin/rclone.exe"),
            Path("C:/ProgramData/S3Drive/services/rclone.conf"),
            Path("C:/ProgramData/S3Drive/services/cache"),
            Path("C:/ProgramData/S3Drive/services/rclone.log"),
            Path("C:/ProgramData/S3Drive/services/rc.htpasswd"),
            45532,
        )
        self.assertIn('--volname "Личный сетевой диск"', command)
        self.assertNotIn("S3Drive - Личный сетевой диск", command)

    def test_empty_bucket_mounts_s3_account_root_in_windows_service(self):
        profile = sample()
        profile.bucket = ""
        command = service_command(
            profile, Path("C:/ProgramData/S3Drive/bin/rclone.exe"),
            Path("C:/ProgramData/S3Drive/services/rclone.conf"),
            Path("C:/ProgramData/S3Drive/services/cache"),
            Path("C:/ProgramData/S3Drive/services/rclone.log"),
            Path("C:/ProgramData/S3Drive/services/rc.htpasswd"), 45532,
        )
        self.assertIn("mount s3drive_mount: S:", command)
        self.assertNotIn("s3drive_mount:my-backup", command)

    def test_service_rclone_binary_is_isolated_per_profile(self):
        from windows_service import service_rclone_path
        p1 = sample()
        p2 = sample()
        with patch("windows_service.service_dir", side_effect=lambda ident: Path("C:/ProgramData/S3Drive/services") / ident):
            self.assertNotEqual(service_rclone_path(p1.id), service_rclone_path(p2.id))
            self.assertEqual(service_rclone_path(p1.id).name, "rclone.exe")

    def test_drive_label_registration_uses_exact_unicode_name(self):
        from windows_service import drive_label_registry_path
        self.assertTrue(drive_label_registry_path("S:").endswith(r"DriveIcons\S\DefaultLabel"))
        profile = sample()
        profile.name = "Личный сетевой диск"
        self.assertEqual(len(profile.name), 19)
        self.assertEqual(len(profile.name.encode("utf-8")), 36)

    def test_explorer_label_override_preserves_full_russian_name_and_restores(self):
        """Simulate HKLM without changing this runner's Windows registry."""
        import sys
        from unittest.mock import Mock
        from windows_service import install_explorer_label, restore_explorer_label
        profile = sample()
        profile.name = "Личный сетевой диск"
        fake = Mock()
        fake.HKEY_LOCAL_MACHINE = 1
        fake.KEY_READ = 0x20019
        fake.KEY_WRITE = 0x20006
        fake.REG_SZ = 1
        values = {}

        class Key:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass

        fake.CreateKeyEx.return_value = Key()
        fake.OpenKey.return_value = Key()

        def get_value(key, name):
            if name not in values:
                raise FileNotFoundError(name)
            return values[name]
        def set_value(key, name, reserved, dtype, value):
            values[name] = (value, dtype)
        def delete_value(key, name):
            del values[name]

        fake.QueryValueEx.side_effect = get_value
        fake.SetValueEx.side_effect = set_value
        fake.DeleteValue.side_effect = delete_value

        with tempfile.TemporaryDirectory() as td:
            with patch.dict(sys.modules, {"winreg": fake}):
                with patch("windows_service.service_dir", return_value=Path(td)):
                    with patch("windows_service.protect_file"):
                        install_explorer_label(profile)
                        self.assertEqual(values[""][0], "Личный сетевой диск")
                        self.assertEqual(len(values[""][0]), 19)
                        restore_explorer_label(profile.id)
                        self.assertNotIn("", values)

    def test_acl_argument_is_a_real_command_option_not_a_comment(self):
        p = sample()
        cmd = service_command(
            p, Path("C:/rclone.exe"), Path("C:/rclone.conf"),
            Path("C:/cache"), Path("C:/log"), Path("C:/auth"), 33333,
        )
        self.assertIn(" -o FileSecurity=D:P(A;;FA;;;WD)", cmd)

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
