"""Transfer-error notification episodes and profile log lookup."""
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from log_access import TransferErrorNotices, profile_log_path


class TransferErrorNoticeTests(unittest.TestCase):
    def test_unchanged_error_counter_notifies_only_once_even_after_acknowledgement(self):
        notices = TransferErrorNotices()
        self.assertTrue(notices.observe("profile", 2))
        self.assertTrue(notices.pending("profile"))
        self.assertFalse(notices.observe("profile", 2))
        notices.acknowledge("profile")
        self.assertFalse(notices.pending("profile"))
        self.assertFalse(notices.observe("profile", 2))
        self.assertTrue(notices.observe("profile", 3))
        self.assertTrue(notices.pending("profile"))

    def test_clear_error_resets_episode_and_isolated_profiles(self):
        notices = TransferErrorNotices()
        self.assertTrue(notices.observe("one", 1))
        self.assertTrue(notices.observe("two", 1))
        self.assertFalse(notices.observe("one", 0))
        self.assertFalse(notices.pending("one"))
        self.assertTrue(notices.pending("two"))
        self.assertTrue(notices.observe("one", 1))

    def test_profile_log_paths_for_user_mount_and_windows_service(self):
        user = SimpleNamespace(id="test-id", service_mode=False)
        service = SimpleNamespace(id="service-id", service_mode=True)
        with patch("log_access.app_data_dir", return_value=Path("user-data")), \
             patch("log_access.service_dir", return_value=Path("service-data")):
            self.assertEqual(profile_log_path(user), Path("user-data/logs/test-id.log"))
            self.assertEqual(profile_log_path(service), Path("service-data/rclone.log"))


    def test_gui_displays_new_errors_once_and_hides_when_log_opened(self):
        from unittest.mock import Mock
        from main import MainWindow
        from transfer_monitor import TransferSnapshot

        profile = SimpleNamespace(id="profile", service_mode=False)
        ui = SimpleNamespace(
            monitor_pending=True, profiles=[profile], monitor_last_error="",
            error_notices=TransferErrorNotices(), transfer_error_profile_id=None,
            transfer_error_link=Mock(), idle_polls=2,
            uploads_seen=set(), dismissed_uploads=set(),
            hide_transfer_popup=Mock(), statusBar=Mock(),
            _open_profile_logs=Mock(return_value=True),
        )
        ui._hide_transfer_error = lambda pid: MainWindow._hide_transfer_error(ui, pid)
        snapshot = TransferSnapshot(queued=0, uploading=0, errors=2, cache_bytes=0)
        MainWindow.on_transfers(ui, profile.id, snapshot, "")
        self.assertEqual(ui.transfer_error_profile_id, profile.id)
        ui.transfer_error_link.show.assert_called_once()

        MainWindow.open_transfer_error_logs(ui)
        self.assertIsNone(ui.transfer_error_profile_id)
        self.assertFalse(ui.error_notices.pending(profile.id))
        ui.transfer_error_link.hide.assert_called_once()
        MainWindow.on_transfers(ui, profile.id, snapshot, "")
        ui.transfer_error_link.show.assert_called_once()

        MainWindow.on_transfers(ui, profile.id, TransferSnapshot(0, 0, 3, 0), "")
        self.assertEqual(ui.transfer_error_link.show.call_count, 2)

    def test_service_log_opens_file_but_user_mount_opens_directory(self):
        import tempfile
        from unittest.mock import Mock
        from main import MainWindow
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            logfile = folder / "rclone.log"
            logfile.write_text("INFO", encoding="utf-8")
            with (patch("main.profile_log_path", return_value=logfile),
                  patch("main.os.startfile", create=True) as opener):
                self.assertTrue(MainWindow._open_profile_logs(
                    Mock(), SimpleNamespace(service_mode=True)))
                opener.assert_called_once_with(str(logfile))
                opener.reset_mock()
                self.assertTrue(MainWindow._open_profile_logs(
                    Mock(), SimpleNamespace(service_mode=False)))
                opener.assert_called_once_with(str(folder))


if __name__ == "__main__":
    unittest.main()
