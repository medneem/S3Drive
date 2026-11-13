"""Windows service integration: global mount command and safe shutdown policy."""
import base64
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from profiles import new_profile
from windows_service import (
    SERVICE_PREFIX, SERVICE_RUNNING, SERVICE_STOPPED, config_text, ensure_vfs_idle, service_command, service_name,
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
        self.assertEqual(cmd.count("--dir-cache-time"), 1)
        self.assertIn("--dir-cache-time 0s", cmd)
        self.assertIn("--rc-htpasswd", cmd)
        # Must be a real command line option, not code hidden by \n in a comment.
        self.assertIn(" -o FileSecurity=D:P(A;;FA;;;WD)", cmd)
        self.assertNotIn("FileSecurity=D:P(A;;FRFW;;;WD)", cmd)
        self.assertNotIn(p.secret_key, cmd)
        self.assertNotIn(p.access_key, cmd)

    def test_service_writes_machine_readable_stats_with_http_auth_kept(self):
        p = sample()
        cmd = service_command(
            p, Path("C:/rclone.exe"), Path("C:/rclone.conf"),
            Path("C:/cache"), Path("C:/rclone.log"), Path("C:/rc.htpasswd"),
            45999,
        )
        self.assertIn("--use-json-log", cmd)
        self.assertIn("--stats 2s", cmd)
        self.assertIn("--rc-htpasswd", cmd)
        self.assertNotIn("--rc-no-auth", cmd)
        self.assertNotIn(p.secret_key, cmd)

    def test_monitor_log_is_read_only_for_interactive_users(self):
        import os
        if os.name != "nt":
            self.skipTest("Тест ACL доступен только в Windows.")
        import win32con
        import win32file
        import win32security
        from windows_service import protect_monitor_log
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "rclone.log"
            with patch("win32security.SetNamedSecurityInfo") as setter:
                protect_monitor_log(path)
            self.assertTrue(path.exists())
            acl = setter.call_args.args[5]
            self.assertEqual(acl.GetAceCount(), 3)
            self.assertEqual(acl.GetAce(0)[1], win32file.FILE_ALL_ACCESS)
            self.assertEqual(acl.GetAce(1)[1], win32file.FILE_ALL_ACCESS)
            self.assertEqual(acl.GetAce(2)[1], win32con.GENERIC_READ)

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
        self.assertIn('--option "volname=Личный сетевой диск"', command)
        self.assertIn('--volname ', command)
        self.assertGreater(len(profile.name.encode("utf-8")), 32)
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
        self.assertIn("--dir-cache-time 0s", command)
        self.assertNotIn("s3drive_mount:my-backup", command)

    def test_service_deletes_s3_directory_markers(self):
        for bucket in ("my-backup", ""):
            with self.subTest(bucket=bucket):
                profile = sample()
                profile.bucket = bucket
                command = service_command(
                    profile, Path("C:/rclone.exe"), Path("C:/rclone.conf"),
                    Path("C:/cache"), Path("C:/log"), Path("C:/auth"), 33333,
                )
                self.assertEqual(command.count("--s3-directory-markers"), 1)

    def test_service_rclone_binary_is_isolated_per_profile(self):
        import hashlib
        from windows_service import service_rclone_path
        p1 = sample()
        p2 = sample()
        sha = hashlib.sha256(b"bundled rclone").hexdigest()
        with patch("windows_service.service_dir", side_effect=lambda ident: Path("C:/ProgramData/S3Drive/services") / ident):
            self.assertNotEqual(service_rclone_path(p1.id, sha), service_rclone_path(p2.id, sha))
            self.assertEqual(service_rclone_path(p1.id, sha).name, f"rclone-{sha[:16]}.exe")
            with self.assertRaises(ValueError):
                service_rclone_path(p1.id, "not-a-hash")

    def test_install_service_rclone_never_replaces_mapped_legacy_exe(self):
        import hashlib
        from windows_service import install_service_rclone
        profile = sample()
        payload = b"new trusted rclone"
        sha = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            legacy = root / "rclone.exe"
            legacy.write_bytes(b"old locked service image")
            source = root / "portable-rclone.exe"
            source.write_bytes(payload)
            with patch("windows_service.service_dir", return_value=root), \
                 patch("windows_service.protect_file"), \
                 patch("windows_service.os.replace", side_effect=PermissionError(5, "mapped EXE")):
                installed = install_service_rclone(profile.id, source, sha)
            self.assertEqual(installed.name, f"rclone-{sha[:16]}.exe")
            self.assertEqual(installed.read_bytes(), payload)
            self.assertEqual(legacy.read_bytes(), b"old locked service image")
            self.assertFalse(list(root.glob("rclone-stage-*.tmp")))

    def test_install_service_rclone_reuses_verified_immutable_exe(self):
        import hashlib
        from windows_service import install_service_rclone
        profile = sample()
        payload = b"official rclone executable"
        sha = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "download.exe"
            source.write_bytes(payload)
            with patch("windows_service.service_dir", return_value=root), \
                 patch("windows_service.protect_file"):
                first = install_service_rclone(profile.id, source, sha)
                with patch("shutil.copy2", side_effect=AssertionError("Must reuse installed EXE")):
                    second = install_service_rclone(profile.id, source, sha)
            self.assertEqual(first, second)
            self.assertEqual(second.read_bytes(), payload)

    def test_install_service_rclone_refuses_corrupted_existing_version(self):
        import hashlib
        from windows_service import install_service_rclone, service_rclone_path
        profile = sample()
        payload = b"trusted rclone executable"
        sha = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "download.exe"
            source.write_bytes(payload)
            with patch("windows_service.service_dir", return_value=root), \
                 patch("windows_service.protect_file"):
                candidate = service_rclone_path(profile.id, sha)
                candidate.write_bytes(b"altered executable")
                with self.assertRaisesRegex(RuntimeError, "SHA-256"):
                    install_service_rclone(profile.id, source, sha)
                self.assertEqual(candidate.read_bytes(), b"altered executable")

    def test_service_install_uses_versioned_rclone_in_scm_command(self):
        import hashlib
        from windows_service import install_and_start
        profile = sample()
        payload = b"verified rclone for install"
        sha = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "portable-rclone.exe"
            source.write_bytes(payload)
            with patch.dict("os.environ", {"PROGRAMDATA": str(root)}), \
                 patch("windows_service.service_status", return_value=None), \
                 patch("windows_service.service_dir", side_effect=lambda ident: root / "services" / ident), \
                 patch("windows_service.check_drive_free"), \
                 patch("windows_service.verify_bundled_rclone", return_value=sha), \
                 patch("windows_service.protect_directory", side_effect=lambda path: path.mkdir(parents=True, exist_ok=True)), \
                 patch("windows_service.protect_file"), \
                 patch("windows_service.protect_monitor_log"), \
                 patch("windows_service.install_explorer_label"), \
                 patch("windows_service._choose_rc_port", return_value=45321), \
                 patch("windows_service._sc") as sc:
                install_and_start(profile, source)
            self.assertEqual(sc.call_count, 2)
            create_call, start_call = (call.args for call in sc.call_args_list)
            self.assertEqual(create_call[0], "create")
            self.assertEqual(start_call[0], "start")
            command = create_call[3]
            self.assertIn(f"rclone-{sha[:16]}.exe", command)
            self.assertNotIn("rclone.exe mount", command)

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

    def test_native_scm_create_start_stop_config_delete_without_sc_subprocess(self):
        import sys
        from unittest.mock import Mock
        from windows_service import _sc

        native = Mock()
        native.SC_MANAGER_CONNECT = 1
        native.SC_MANAGER_CREATE_SERVICE = 2
        native.SERVICE_ALL_ACCESS = 0xF01FF
        native.SERVICE_WIN32_OWN_PROCESS = 0x10
        native.SERVICE_AUTO_START = 2
        native.SERVICE_ERROR_NORMAL = 1
        native.SERVICE_NO_CHANGE = 0xFFFFFFFF
        native.SERVICE_CONTROL_STOP = 1
        native.OpenSCManager.return_value = "scm"
        native.CreateService.return_value = "created"
        native.OpenService.return_value = "service"

        name = "S3Drive-0123456789abcdef0123456789abcdef"
        with patch.dict(sys.modules, {"win32service": native}):
            with patch("windows_service.subprocess.run") as spawn:
                _sc("create", name, "binPath=", "C:\\rclone.exe mount", "start=", "auto",
                    "obj=", "LocalSystem", "DisplayName=", "S3Drive S:")
                native.CreateService.assert_called_once()
                self.assertEqual(native.CreateService.call_args.args[2], "S3Drive S:")
                self.assertEqual(native.CreateService.call_args.args[7], "C:\\rclone.exe mount")
                # pywin32 CreateService requires 13 positional arguments:
                # dependencies occupies index 10, account index 11.
                self.assertEqual(len(native.CreateService.call_args.args), 13)
                self.assertIsNone(native.CreateService.call_args.args[10])
                self.assertEqual(native.CreateService.call_args.args[11], "LocalSystem")
                self.assertIsNone(native.CreateService.call_args.args[12])
                _sc("start", name)
                native.StartService.assert_called_once_with("service", None)
                _sc("stop", name)
                native.ControlService.assert_called_once_with("service", 1)
                _sc("config", name, "binPath=", "C:\\rclone.exe mount", "start=", "auto")
                native.ChangeServiceConfig.assert_called_once()
                self.assertEqual(native.ChangeServiceConfig.call_args.args[4], "C:\\rclone.exe mount")
                _sc("delete", name)
                native.DeleteService.assert_called_once_with("service")
                spawn.assert_not_called()
        self.assertEqual(native.CloseServiceHandle.call_count, 10)

    def test_native_scm_rejects_unknown_operations_without_side_effects(self):
        from windows_service import _sc
        with self.assertRaises(ValueError):
            _sc("run", "unexpected")
        with self.assertRaises(ValueError):
            _sc("create", "unexpected", "binPath=", "not-enough")

    def test_stop_fails_with_pending_uploads(self):
        with patch("windows_service._control_request", return_value={
            "inUse": 0,
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
            "inUse": 0,
            "diskCache": {"uploadsQueued": 0, "uploadsInProgress": 0, "erroredFiles": 0}
        }):
            ensure_vfs_idle({"port": 42, "password": "secret"})


    def test_service_stop_allowed_with_vfs_baseline_in_use_one(self):
        from windows_service import stop_service
        profile_id = sample().id
        with patch("windows_service.service_status", return_value=SERVICE_RUNNING):
            with patch("windows_service.service_dir", return_value=Path("C:/fixture")):
                with patch("pathlib.Path.read_text", return_value='{"port":42,"password":"pw"}'):
                    with patch("windows_service._control_request", return_value={
                        "inUse": 1,
                        "diskCache": {"uploadsQueued": 0, "uploadsInProgress": 0,
                                      "erroredFiles": 0},
                    }):
                        with patch("windows_service._sc") as sc:
                            stop_service(profile_id)
                            sc.assert_called_once_with("stop", service_name(profile_id))

    def test_missing_in_use_counter_does_not_block_service_stop(self):
        with patch("windows_service._control_request", return_value={
            "diskCache": {"uploadsQueued": 0, "uploadsInProgress": 0,
                          "erroredFiles": 0},
        }):
            ensure_vfs_idle({"port": 42, "password": "pw"})

    def test_cache_clear_refuses_stopped_service(self):
        from windows_service import clear_service_cache
        with patch("windows_service.service_status", return_value=SERVICE_STOPPED):
            with patch("windows_service.erase_cache_directory") as erase:
                with self.assertRaisesRegex(RuntimeError, "остановленной службы"):
                    clear_service_cache(sample().id)
                erase.assert_not_called()

    def test_cache_clear_refuses_pending_uploads_before_stopping(self):
        from windows_service import clear_service_cache
        with tempfile.TemporaryDirectory() as root:
            cache = Path(root)
            (cache / "control.json").write_text(
                json.dumps({"port": 4321, "password": "fixture"}), encoding="utf-8"
            )
            with patch("windows_service.service_dir", return_value=cache):
                with patch("windows_service.service_status", return_value=SERVICE_RUNNING):
                    with patch("windows_service._control_request", return_value={
                        "inUse": 0,
                        "diskCache": {"uploadsQueued": 1, "uploadsInProgress": 0, "erroredFiles": 0},
                    }):
                        with patch("windows_service.stop_service") as stop:
                            with self.assertRaisesRegex(RuntimeError, "Нельзя очищать"):
                                clear_service_cache(sample().id)
                            stop.assert_not_called()


if __name__ == "__main__":
    unittest.main()
