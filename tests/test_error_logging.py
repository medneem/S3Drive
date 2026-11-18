"""Regression guard: ERROR-only logging without breaking S3 service safety."""
import inspect
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from profiles import new_profile
from mounts import mount_command
from windows_service import service_command


def profile():
    return new_profile(
        name="Test disk", endpoint="http://nas.local:8010", bucket="bucket",
        drive="S:", access_key="test", secret_key="secret",
    )


class ErrorOnlyLoggingTests(unittest.TestCase):
    def test_user_mount_logs_only_errors(self):
        command = mount_command(profile(), "rclone.exe", Path("cache"), 45330)
        self.assertEqual(command[command.index("--log-level") + 1], "ERROR")
        self.assertNotIn("--stats", command)
        self.assertNotIn("--stats-log-level", command)

    def test_service_logs_only_errors_without_stats(self):
        cmd = service_command(
            profile(), Path("rclone.exe"), Path("rclone.conf"),
            Path("cache"), Path("rclone.log"), Path("htpasswd"), 45677,
        )
        self.assertIn("--log-level ERROR", cmd)
        self.assertIn("--log-file", cmd)
        self.assertIn("--use-json-log", cmd)
        self.assertNotIn("--stats ", cmd)
        self.assertNotIn("--stats-log-level", cmd)

    def test_gui_never_polls_services_for_transfer_progress(self):
        from main import MainWindow
        service = SimpleNamespace(id="service", service_mode=True)
        user = SimpleNamespace(id="user", service_mode=False)
        ui = SimpleNamespace(
            monitor_pending=False,
            profiles=[service, user],
            mounts=SimpleNamespace(is_mounted=lambda _id: True),
            transfer_popup_profile=None, transfer_popup=None,
            monitor_index=0, on_transfers=Mock(),
        )
        with patch("main._MonitorTask") as task, \
             patch("main.QThreadPool.globalInstance") as pool:
            MainWindow.poll_transfers(ui)
        task.assert_called_once_with(user, ui.mounts)
        pool.return_value.start.assert_called_once_with(task.return_value)
        self.assertTrue(ui.monitor_pending)

    def test_gui_does_not_create_error_status_bar_or_notifications(self):
        from main import MainWindow
        source = inspect.getsource(MainWindow)
        init = inspect.getsource(MainWindow.__init__)
        updates = inspect.getsource(MainWindow.on_transfers)
        self.assertNotIn("self.transfer_error_link", init)
        self.assertNotIn("self.error_notices", init)
        self.assertNotIn("self.statusBar()", source)
        self.assertNotIn("error_notices", updates)
        self.assertNotIn("snapshot.errors", updates)

    def test_gui_does_not_publish_polling_errors_as_status_messages(self):
        from main import MainWindow
        ui = SimpleNamespace(
            monitor_pending=True,
            profiles=[SimpleNamespace(id="user", service_mode=False)],
            monitor_last_error="",
        )
        MainWindow.on_transfers(ui, "user", None, "rc unavailable")
        self.assertFalse(ui.monitor_pending)

    def test_broker_stop_still_checks_vfs_queue(self):
        from windows_service import stop_service, SERVICE_RUNNING
        p = profile()
        with patch("windows_service.service_status", return_value=SERVICE_RUNNING), \
             patch("windows_service.service_dir", return_value=Path("testservice")), \
             patch("pathlib.Path.read_text", return_value='{"port":42,"password":"pw"}'), \
             patch("windows_service.ensure_vfs_idle", side_effect=RuntimeError("pending")) as guard, \
             patch("windows_service._sc") as scm:
            with self.assertRaisesRegex(RuntimeError, "pending"):
                stop_service(p.id)
            guard.assert_called_once()
            scm.assert_not_called()


if __name__ == "__main__":
    unittest.main()
