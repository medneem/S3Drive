"""Per-profile maintenance must never discard another mount's cache or log."""
from __future__ import annotations

import os
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from maintenance import (StorageUsage, clear_user_log, format_size,
                         measure_path, storage_usage)


def profile(*, service=False):
    return SimpleNamespace(id=str(uuid.uuid4()), name="S3", drive="S:",
                           service_mode=service)


class StorageUsageTests(unittest.TestCase):
    def test_counts_two_profiles_without_mixing_cache_and_logs(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            user = profile()
            service = profile(service=True)
            (root / "local" / "cache" / user.id).mkdir(parents=True)
            (root / "local" / "cache" / user.id / "upload.bin").write_bytes(b"a" * 20)
            (root / "local" / "logs").mkdir(parents=True)
            (root / "local" / "logs" / (user.id + ".log")).write_bytes(b"l" * 4)
            service_path = root / "service" / service.id
            (service_path / "cache").mkdir(parents=True)
            (service_path / "cache" / "pending.bin").write_bytes(b"b" * 30)
            (service_path / "rclone.log").write_bytes(b"x" * 8)
            with patch("maintenance.app_data_dir", return_value=root / "local"), \
                 patch("maintenance.service_dir", side_effect=lambda x: root / "service" / x):
                report = storage_usage([user, service])
            self.assertEqual(report.total_cache, 50)
            self.assertEqual(report.total_logs, 12)
            self.assertEqual(report.cache_by_profile[user.id], 20)
            self.assertEqual(report.cache_by_profile[service.id], 30)
            self.assertEqual(report.logs_by_profile[user.id], 4)
            self.assertEqual(report.logs_by_profile[service.id], 8)
            self.assertFalse(report.cache_incomplete)
            self.assertFalse(report.logs_incomplete)

    def test_missing_dirs_are_zero_and_symlinks_are_not_followed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            real = root / "outside"
            real.mkdir()
            (real / "secrets.bin").write_bytes(b"z" * 200)
            cache = root / "cache"
            cache.mkdir()
            (cache / "data").write_bytes(b"y" * 5)
            try:
                (cache / "link").symlink_to(real, target_is_directory=True)
            except (OSError, NotImplementedError):
                pass
            self.assertEqual(measure_path(cache), 5)
            self.assertEqual(measure_path(root / "missing"), 0)

    def test_protected_service_cache_marks_total_incomplete_not_zero(self):
        user, service = profile(), profile(service=True)
        with patch("maintenance.app_data_dir", return_value=Path("unused")), \
             patch("maintenance.service_dir", return_value=Path("restricted")), \
             patch("maintenance.measure_path", side_effect=[10, 2, PermissionError("ACL"), 4]):
            report = storage_usage([user, service])
        self.assertEqual(report.total_cache, 10)
        self.assertEqual(report.total_logs, 6)
        self.assertTrue(report.cache_incomplete)
        self.assertFalse(report.logs_incomplete)
        self.assertIsNone(report.cache_by_profile[service.id])

    def test_clear_log_only_selected_uuid_and_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first, second = profile(), profile()
            logs = root / "logs"
            logs.mkdir()
            chosen = logs / (first.id + ".log")
            other = logs / (second.id + ".log")
            chosen.write_text("one", encoding="utf-8")
            other.write_text("two", encoding="utf-8")
            with patch("maintenance.app_data_dir", return_value=root):
                self.assertTrue(clear_user_log(first.id))
                self.assertFalse(chosen.exists())
                self.assertEqual(other.read_text(encoding="utf-8"), "two")
                self.assertFalse(clear_user_log(first.id))
                with self.assertRaises(ValueError):
                    clear_user_log("../unsafe")
                try:
                    chosen.symlink_to(other)
                except (OSError, NotImplementedError):
                    pass
                else:
                    with self.assertRaises(RuntimeError):
                        clear_user_log(first.id)
                    self.assertTrue(other.exists())

    def test_active_user_log_is_truncated_not_unlinked(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first, second = profile(), profile()
            logs = root / "logs"
            logs.mkdir()
            chosen = logs / (first.id + ".log")
            other = logs / (second.id + ".log")
            chosen.write_bytes(b"old data")
            other.write_bytes(b"other")
            with patch("maintenance.app_data_dir", return_value=root):
                self.assertTrue(clear_user_log(first.id, running=True))
            self.assertTrue(chosen.exists())
            self.assertEqual(chosen.stat().st_size, 0)
            self.assertEqual(other.read_bytes(), b"other")

    def test_active_log_blocked_file_keeps_original_data(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            logs = root / "logs"
            logs.mkdir()
            p = profile()
            chosen = logs / (p.id + ".log")
            chosen.write_bytes(b"pending diagnostic")
            with patch("maintenance.app_data_dir", return_value=root), \
                 patch("pathlib.Path.open", side_effect=PermissionError("file is locked")):
                with self.assertRaisesRegex(PermissionError, "locked"):
                    clear_user_log(p.id, running=True)
            self.assertEqual(chosen.read_bytes(), b"pending diagnostic")

    def test_active_log_symlink_does_not_truncate_external_target(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            logs = root / "logs"
            logs.mkdir()
            p = profile()
            target = root / "external.txt"
            target.write_text("keep me", encoding="utf-8")
            chosen = logs / (p.id + ".log")
            try:
                chosen.symlink_to(target)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks are not available")
            with patch("maintenance.app_data_dir", return_value=root):
                with self.assertRaises(RuntimeError):
                    clear_user_log(p.id, running=True)
            self.assertEqual(target.read_text(encoding="utf-8"), "keep me")

    def test_gui_clear_logs_for_running_user_mount(self):
        from unittest.mock import Mock
        from PySide6.QtWidgets import QMessageBox as QtMessageBox
        from main import MainWindow
        p = profile()
        ui = SimpleNamespace(mounts=SimpleNamespace(is_mounted=lambda _id: True),
                             selected_profile=lambda: p)
        with patch("main.service_status", return_value=None), \
             patch("main.QMessageBox.question",
                   return_value=QtMessageBox.StandardButton.Yes), \
             patch("main.QMessageBox.information"), \
             patch("main.clear_user_log", return_value=True) as clear:
            self.assertTrue(MainWindow.clear_logs_profile(ui, profile=p))
            clear.assert_called_once_with(p.id, running=True)

    def test_gui_clear_logs_for_running_windows_service(self):
        from unittest.mock import Mock
        from PySide6.QtWidgets import QMessageBox as QtMessageBox
        from main import MainWindow
        from windows_service import SERVICE_RUNNING
        p = profile(service=True)
        ui = SimpleNamespace(mounts=SimpleNamespace(is_mounted=lambda _id: False),
                             selected_profile=lambda: p)
        with patch("main.service_status", return_value=SERVICE_RUNNING), \
             patch("main.QMessageBox.question",
                   return_value=QtMessageBox.StandardButton.Yes), \
             patch("main.QMessageBox.information"), \
             patch("main.run_admin") as elevated:
            self.assertTrue(MainWindow.clear_logs_profile(ui, profile=p))
            elevated.assert_called_once_with("clear-logs", p.id)

    def test_gui_enables_clear_logs_for_active_mount(self):
        import inspect
        from main import MainWindow
        code = inspect.getsource(MainWindow.open_settings)
        self.assertNotIn("clear_logs.setEnabled(not active", code)
        self.assertIn("clear_logs.setEnabled(service in (None, SERVICE_STOPPED, SERVICE_RUNNING))", code)

    def test_size_formatting_supports_both_languages_and_missing_data(self):
        from localization import set_language
        try:
            set_language("ru")
            self.assertIn("МБ", format_size(125 * 1024 * 1024))
            self.assertIn("Недоступно", format_size(None))
            set_language("en")
            self.assertIn("MB", format_size(125 * 1024 * 1024))
            self.assertIn("Unavailable", format_size(None))
        finally:
            set_language("en")

    def test_statusbar_hidden_without_error_and_maintenance_in_settings(self):
        import inspect
        from main import MainWindow
        init = inspect.getsource(MainWindow.__init__)
        settings = inspect.getsource(MainWindow.open_settings)
        self.assertNotIn("self.open_logs_button", init)
        self.assertNotIn("self.clear_cache_button", init)
        self.assertIn("self.statusBar().hide()", init)
        self.assertIn("self.statusBar().show()", inspect.getsource(MainWindow.on_transfers))
        self.assertIn("Очистить кэш", settings)
        self.assertIn("Очистить логи", settings)
        self.assertIn("Общий размер кэша", settings)
        self.assertIn("Общий размер логов", settings)


if __name__ == "__main__":
    unittest.main()
