"""Deleting a profile removes only its own data, after an explicit warning."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from profiles import new_profile


def sample(service=False):
    return new_profile(
        name="S3 profile", endpoint="http://nas.local:8010", bucket="bucket",
        drive="S:", access_key="test", secret_key="secret", service_mode=service,
    )


def fake_window(p, other):
    return SimpleNamespace(
        settings_load_error=False, selected_profile=lambda: p,
        profiles=[p, other], mounts=Mock(), store=Mock(),
        refresh_items=Mock(), new_profile=Mock(), pending_mounts={},
        hide_transfer_popup=Mock(),
    )


class ProfileDeletionTests(unittest.TestCase):
    def test_user_delete_stops_mount_then_removes_only_its_cache_and_log(self):
        from main import MainWindow, QMessageBox
        p, other = sample(), sample()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for current in (p, other):
                folder = root / "cache" / current.id
                folder.mkdir(parents=True)
                (folder / "file").write_bytes(b"cache")
                logs = root / "logs"
                logs.mkdir(exist_ok=True)
                (logs / (current.id + ".log")).write_bytes(b"log")
            ui = fake_window(p, other)
            ui.mounts.is_mounted.side_effect = lambda ident: ident == p.id
            def clear_cache(ident):
                from cache_ops import erase_cache_directory
                assert ident == p.id
                return erase_cache_directory(root / "cache" / ident, parent=root / "cache")
            ui.mounts.clear_cache.side_effect = clear_cache
            with patch("main.app_data_dir", return_value=root), \
                 patch("maintenance.app_data_dir", return_value=root), \
                 patch("main.service_status", return_value=None), \
                 patch("main.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes), \
                 patch("main.QMessageBox.warning") as warning, \
                 patch("main.run_admin") as admin:
                MainWindow.remove_profile(ui)
            ui.mounts.clear_cache.assert_called_once_with(p.id)
            admin.assert_not_called()
            ui.store.save.assert_called_once_with([other])
            self.assertEqual(ui.profiles, [other])
            self.assertFalse((root / "cache" / p.id).exists())
            self.assertFalse((root / "logs" / (p.id + ".log")).exists())
            self.assertTrue((root / "cache" / other.id / "file").is_file())
            self.assertTrue((root / "logs" / (other.id + ".log")).is_file())
            warning.assert_not_called()

    def test_pending_upload_prevents_profile_and_data_deletion(self):
        from main import MainWindow, QMessageBox
        p, other = sample(), sample()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cache = root / "cache" / p.id
            cache.mkdir(parents=True)
            (cache / "pending").write_bytes(b"not uploaded")
            ui = fake_window(p, other)
            ui.mounts.is_mounted.return_value = True
            ui.mounts.clear_cache.side_effect = RuntimeError("uploads queued")
            with patch("main.app_data_dir", return_value=root), \
                 patch("main.service_status", return_value=None), \
                 patch("main.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes), \
                 patch("main.QMessageBox.warning") as warning:
                MainWindow.remove_profile(ui)
            self.assertTrue((cache / "pending").exists())
            ui.store.save.assert_not_called()
            self.assertEqual(ui.profiles, [p, other])
            warning.assert_called_once()

    def test_service_delete_uses_elevated_safe_cleanup_before_forgetting_profile(self):
        from main import MainWindow, QMessageBox
        from windows_service import SERVICE_RUNNING
        p, other = sample(service=True), sample()
        ui = fake_window(p, other)
        ui.mounts.is_mounted.return_value = False
        with patch("main.service_status", return_value=SERVICE_RUNNING), \
             patch("main.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes), \
             patch("main.run_admin") as admin, \
             patch("main.erase_cache_directory"), \
             patch("main.clear_user_log"), \
             patch("main.notify_explorer"):
            MainWindow.remove_profile(ui)
        admin.assert_called_once_with("delete-profile-data", p.id)
        ui.store.save.assert_called_once_with([other])

    def test_declining_delete_does_not_stop_or_remove_anything(self):
        from main import MainWindow, QMessageBox
        p, other = sample(), sample()
        ui = fake_window(p, other)
        with patch("main.QMessageBox.question", return_value=QMessageBox.StandardButton.No), \
             patch("main.service_status") as state, \
             patch("main.run_admin") as admin:
            MainWindow.remove_profile(ui)
        state.assert_not_called()
        admin.assert_not_called()
        ui.mounts.clear_cache.assert_not_called()
        ui.store.save.assert_not_called()


class ElevatedServiceDeletionTests(unittest.TestCase):
    def test_running_service_checked_and_stopped_before_private_files_deleted(self):
        from windows_service import delete_service_profile_data, SERVICE_RUNNING, SERVICE_STOPPED
        p = sample(service=True)
        with tempfile.TemporaryDirectory() as td:
            service_folder = Path(td) / p.id.replace("-", "")
            service_folder.mkdir()
            (service_folder / "rclone.log").write_bytes(b"log")
            (service_folder / "rclone.conf").write_bytes(b"config")
            status = [SERVICE_RUNNING]
            def clear_cache(ident):
                self.assertEqual(ident, p.id)
                status[0] = SERVICE_STOPPED
            def remove(ident):
                self.assertEqual(status[0], SERVICE_STOPPED)
                self.assertTrue(service_folder.exists())
                status[0] = None
            with patch("windows_service.service_dir", return_value=service_folder), \
                 patch("windows_service.service_status", side_effect=lambda ident: status[0]), \
                 patch("windows_service.clear_service_cache", side_effect=clear_cache) as clear, \
                 patch("windows_service.remove_service", side_effect=remove) as service_remove:
                delete_service_profile_data(p.id)
            clear.assert_called_once_with(p.id)
            service_remove.assert_called_once_with(p.id)
            self.assertFalse(service_folder.exists())

    def test_failed_safe_stop_never_deletes_private_files(self):
        from windows_service import delete_service_profile_data, SERVICE_RUNNING
        p = sample(service=True)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / p.id.replace("-", "")
            root.mkdir()
            (root / "cache").mkdir()
            (root / "cache" / "pending").write_bytes(b"not uploaded")
            with patch("windows_service.service_dir", return_value=root), \
                 patch("windows_service.service_status", return_value=SERVICE_RUNNING), \
                 patch("windows_service.clear_service_cache", side_effect=RuntimeError("pending")), \
                 patch("windows_service.remove_service") as remove:
                with self.assertRaisesRegex(RuntimeError, "pending"):
                    delete_service_profile_data(p.id)
            remove.assert_not_called()
            self.assertTrue((root / "cache" / "pending").exists())


if __name__ == "__main__":
    unittest.main()
