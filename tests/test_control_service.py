"""Безопасная граница привилегий: управление службой без UAC после регистрации владельца."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from profiles import new_profile
from windows_service import SERVICE_RUNNING, SERVICE_STOPPED, service_name


def profile():
    return new_profile(
        name="Проверка", endpoint="http://localhost:8010", bucket="test-bucket",
        access_key="access", secret_key="secret", drive="S:",
    )


class ControlProtocolTests(unittest.TestCase):
    def test_unknown_operation_rejected_before_privileged_access(self):
        from service_control import parse_request
        p = profile()
        for request in (
            {"action": "remove", "profile_id": p.id},
            {"action": "clear-cache", "profile_id": p.id},
            {"action": "stop", "profile_id": p.id, "path": "C:/Windows"},
            {"action": "stop", "profile_id": "../../Windows"},
            {"action": "stop", "profile_id": p.id.upper()},
            {"action": "start", "profile_id": p.id, "fingerprint": "wrong"},
            {"action": "start", "profile_id": p.id, "fingerprint": "a" * 64, "extra": True},
            {"action": "start", "profile_id": p.id},
            {"action": "stop", "profile_id": p.id, "fingerprint": "a" * 64},
        ):
            with self.subTest(request=request), self.assertRaises(ValueError):
                parse_request(request)

    def test_valid_requests_are_bounded_and_explicit(self):
        from service_control import parse_request
        p = profile()
        self.assertEqual(
            parse_request({"action": "start", "profile_id": p.id, "fingerprint": "a" * 64}),
            ("start", p.id, "a" * 64),
        )
        self.assertEqual(
            parse_request({"action": "stop", "profile_id": p.id}),
            ("stop", p.id, None),
        )

    def test_enroll_preserves_credentials_and_records_explicit_owner(self):
        from service_control import enroll_profile, load_enrollment, config_fingerprint
        from windows_service import config_text
        p = profile()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "rclone.conf").write_text(config_text(p), encoding="utf-8")
            with patch("service_control.service_dir", return_value=root), \
                 patch("service_control.protect_file"):
                enroll_profile(p.id, "S-1-5-21-100-200-300-1000", drive=p.drive)
                saved = load_enrollment(p.id)
                self.assertEqual(saved["owner_sid"], "S-1-5-21-100-200-300-1000")
                self.assertEqual(saved["fingerprint"], config_fingerprint(p))
                self.assertEqual(saved["drive"], "S:")
                self.assertNotIn("secret", (root / "owner.json").read_text("utf-8"))

    def test_cannot_enroll_without_protected_configuration(self):
        from service_control import enroll_profile
        with tempfile.TemporaryDirectory() as directory:
            with patch("service_control.service_dir", return_value=Path(directory)):
                with self.assertRaises(FileNotFoundError):
                    enroll_profile(profile().id, "S-1-5-21-100-200-300-1000", drive="S:")

    def test_wrong_owner_cannot_start_or_stop(self):
        from service_control import handle_request
        p = profile()
        metadata = {"owner_sid": "S-1-5-21-100-200-300-1000",
                    "fingerprint": "a" * 64, "drive": "S:"}
        with patch("service_control.load_enrollment", return_value=metadata), \
             patch("service_control.service_status") as status, \
             patch("service_control.stop_service") as stop, \
             patch("service_control._sc") as sc:
            for action in ("start", "stop"):
                message = {"action": action, "profile_id": p.id}
                if action == "start":
                    message["fingerprint"] = "a" * 64
                with self.assertRaises(PermissionError):
                    handle_request(message, "S-1-5-21-100-200-300-1001")
            status.assert_not_called()
            stop.assert_not_called()
            sc.assert_not_called()

    def test_start_checks_fingerprint_and_existing_service(self):
        from service_control import handle_request
        p = profile()
        metadata = {"owner_sid": "S-1-5-21-100-200-300-1000",
                    "fingerprint": "a" * 64, "drive": "S:"}
        with patch("service_control.load_enrollment", return_value=metadata), \
             patch("service_control.service_status", return_value=SERVICE_STOPPED), \
             patch("service_control.check_drive_free") as check_drive, \
             patch("service_control._sc") as sc:
            with self.assertRaisesRegex(RuntimeError, "настройки"):
                handle_request({"action": "start", "profile_id": p.id, "fingerprint": "b" * 64},
                               metadata["owner_sid"])
            sc.assert_not_called()
            handle_request({"action": "start", "profile_id": p.id, "fingerprint": "a" * 64},
                           metadata["owner_sid"])
            check_drive.assert_called_once_with("S:")
            sc.assert_called_once_with("start", service_name(p.id))

    def test_stop_must_execute_existing_vfs_guard(self):
        from service_control import handle_request
        p = profile()
        owner = "S-1-5-21-100-200-300-1000"
        with patch("service_control.load_enrollment", return_value={
            "owner_sid": owner, "fingerprint": "a" * 64, "drive": "S:"
        }), patch("service_control.service_status", return_value=SERVICE_RUNNING), \
             patch("service_control.stop_service", side_effect=RuntimeError("uploads queued")) as stop:
            with self.assertRaisesRegex(RuntimeError, "uploads queued"):
                handle_request({"action": "stop", "profile_id": p.id}, owner)
            stop.assert_called_once_with(p.id)

    def test_named_pipe_nonadmin_acl_allows_sync_without_instance_creation(self):
        # Synchronous CreateFile requests SYNCHRONIZE in addition to data R/W.
        # Admin-only CI sessions can mask a missing SYNCHRONIZE ACE.
        import win32security
        from service_control import _pipe_security_attributes, PIPE_CLIENT_ACCESS

        self.assertEqual(PIPE_CLIENT_ACCESS, 0x00100003)
        attrs = _pipe_security_attributes()
        acl = attrs.SECURITY_DESCRIPTOR.GetSecurityDescriptorDacl()
        masks = [
            acl.GetAce(i)[1]
            for i in range(acl.GetAceCount())
            if win32security.ConvertSidToStringSid(acl.GetAce(i)[2]) == "S-1-5-11"
        ]
        self.assertEqual(masks, [PIPE_CLIENT_ACCESS])
        # FILE_CREATE_PIPE_INSTANCE and FILE_APPEND_DATA overlap at bit 0x4.
        self.assertEqual(masks[0] & 0x0004, 0)

    def test_access_denied_at_client_pipe_open_requires_broker_repair(self):
        import pywintypes
        from unittest.mock import Mock
        import sys
        from service_control import send_control, ControlAccessDenied

        p = profile()
        fake_pipe = Mock()
        fake_file = Mock()
        fake_con = Mock()
        fake_con.OPEN_EXISTING = 3
        fake_file.CreateFile.side_effect = pywintypes.error(
            5, "CreateFile", "Access is denied"
        )
        with patch.dict(sys.modules, {
            "win32pipe": fake_pipe, "win32file": fake_file, "win32con": fake_con
        }):
            with self.assertRaises(ControlAccessDenied):
                send_control("stop", p)

    def test_existing_broker_is_upgraded_without_touching_rclone(self):
        import sys
        import zipfile
        from unittest.mock import Mock
        from service_control_installer import install_control_service

        native = Mock()
        native.SC_MANAGER_CONNECT = 1
        native.SC_MANAGER_CREATE_SERVICE = 2
        native.SERVICE_QUERY_CONFIG = 4
        native.SERVICE_QUERY_STATUS = 8
        native.SERVICE_START = 16
        native.SERVICE_STOP = 32
        native.SERVICE_CONTROL_STOP = 1
        native.SERVICE_RUNNING = 4
        native.SERVICE_STOPPED = 1
        state = [native.SERVICE_RUNNING]
        native.ControlService.side_effect = lambda *_: state.__setitem__(
            0, native.SERVICE_STOPPED
        )
        native.StartService.side_effect = lambda *_: state.__setitem__(
            0, native.SERVICE_RUNNING
        )
        query = Mock()
        query.QueryServiceStatus.side_effect = lambda *_: (None, state[0])
        native.OpenSCManager.return_value = "scm"
        native.OpenService.return_value = "broker"
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            exe = base / "S3DriveControl.exe"
            exe.write_bytes(b"old privileged helper")
            bundle = base / "payload.zip"
            with zipfile.ZipFile(bundle, "w") as z:
                z.writestr("S3DriveControl.exe", b"new privileged helper")
            config = [None] * 8
            config[3] = '"' + str(exe) + '"'
            config[7] = "LocalSystem"
            native.QueryServiceConfig.return_value = tuple(config)
            with patch.dict(sys.modules, {
                "win32service": native, "win32serviceutil": query
            }), patch("service_control_installer.service_executable_dir",
                      return_value=base), patch(
                          "service_control_installer.protect_directory"
                      ), patch("service_control_installer.protect_file"):
                install_control_service(bundle)
            self.assertEqual(exe.read_bytes(), b"new privileged helper")
            native.ControlService.assert_called_once_with("broker", 1)
            native.StartService.assert_called_once_with("broker", None)
            self.assertEqual(state[0], native.SERVICE_RUNNING)

    def test_broker_does_not_expose_admin_only_actions(self):
        from service_control import parse_request
        p = profile()
        for action in ("install", "remove", "clear-cache", "configure", "upgrade"):
            with self.subTest(action=action), self.assertRaises(ValueError):
                parse_request({"action": action, "profile_id": p.id})

    def test_release_contains_broker_and_no_default_gui_elevation(self):
        from auto_update import PAYLOAD_FILES
        root = Path(__file__).resolve().parents[1]
        workflow = (root / ".github/workflows/build-windows.yml").read_text("utf-8")
        # Existing v1.5.4 updater requires the original exact 11-file ZIP.
        self.assertNotIn("S3DriveControl.exe", PAYLOAD_FILES)
        self.assertEqual(len(PAYLOAD_FILES), 11)
        self.assertIn("dist/S3DriveControl.exe", workflow)
        self.assertIn("--include-data-files=dist/S3DriveControl-bundled.zip=S3DriveControl-bundled.zip", workflow)
        self.assertIn("service_control_host.py", workflow)
        gui = (root / "main.py").read_text("utf-8")
        self.assertIn("send_control", gui)
        self.assertIn("setup-control", gui)
        self.assertNotIn('run_admin("stop"', gui)


if __name__ == "__main__":
    unittest.main()
