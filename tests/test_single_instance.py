"""Windows GUI singleton: race-free named mutex, cleanup and startup gating."""
import os
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

from single_instance import SingleInstance, MUTEX_NAME


class MutexTests(unittest.TestCase):
    def test_first_instance_acquires_and_releases_mutex(self):
        kernel = Mock()
        kernel.CreateMutexW.return_value = 123
        with SingleInstance(kernel32=kernel, get_last_error=lambda: 0) as instance:
            self.assertTrue(instance.acquired)
            kernel.CreateMutexW.assert_called_once_with(None, False, MUTEX_NAME)
            kernel.CloseHandle.assert_not_called()
        kernel.CloseHandle.assert_called_once_with(123)

    def test_second_instance_is_rejected_and_closes_extra_handle(self):
        kernel = Mock()
        kernel.CreateMutexW.return_value = 456
        with SingleInstance(kernel32=kernel, get_last_error=lambda: 183) as instance:
            self.assertFalse(instance.acquired)
        kernel.CloseHandle.assert_called_once_with(456)

    def test_api_error_prevents_launch(self):
        kernel = Mock()
        kernel.CreateMutexW.return_value = 0
        with self.assertRaises(OSError):
            with SingleInstance(kernel32=kernel, get_last_error=lambda: 5):
                pass

    def test_lock_released_even_if_gui_raises(self):
        kernel = Mock()
        kernel.CreateMutexW.return_value = 789
        with self.assertRaisesRegex(ValueError, "GUI failed"):
            with SingleInstance(kernel32=kernel, get_last_error=lambda: 0):
                raise ValueError("GUI failed")
        kernel.CloseHandle.assert_called_once_with(789)

    @unittest.skipUnless(os.name == "nt", "Windows named mutex integration")
    def test_two_real_processes_are_mutually_exclusive(self):
        code = (
            "from single_instance import SingleInstance; "
            "with SingleInstance() as instance: print(int(instance.acquired))"
        )
        # Python compound statements cannot follow semicolon; use newlines.
        code = code.replace("; with ", "\nwith ")
        def child():
            result = subprocess.run(
                [sys.executable, "-c", code], text=True,
                capture_output=True, timeout=15, check=True,
            )
            return result.stdout.strip()
        with SingleInstance() as first:
            self.assertTrue(first.acquired)
            self.assertEqual(child(), "0")
        self.assertEqual(child(), "1")


class GUIStartupTests(unittest.TestCase):
    def test_second_launch_exits_before_win_fsp_check_and_main_window(self):
        import main as desktop
        with patch.object(desktop, "SingleInstance") as guard:
            guard.return_value.__enter__.return_value.acquired = False
            with patch.object(desktop, "QApplication"):
                with patch.object(desktop, "startup_ready") as startup:
                    with patch.object(desktop, "MainWindow") as window:
                        with patch.object(desktop, "QMessageBox") as message:
                            self.assertEqual(desktop.main(), 0)
                            startup.assert_not_called()
                            window.assert_not_called()
                            message.information.assert_called_once()

    def test_old_tray_autostart_cleans_registry_without_opening_gui(self):
        import main as desktop
        with patch.object(desktop, "SingleInstance") as guard:
            guard.return_value.__enter__.return_value.acquired = True
            with patch.object(desktop, "QApplication") as app:
                with patch.object(desktop, "remove_legacy_tray_autostart") as cleanup:
                    with patch.object(desktop, "startup_ready") as startup_check:
                        with patch.object(desktop, "MainWindow") as window:
                            with patch.object(desktop.sys, "argv", ["S3Drive.exe", "--tray"]):
                                self.assertEqual(desktop.main(), 0)
                                cleanup.assert_called_once_with()
                                startup_check.assert_not_called()
                                window.assert_not_called()
                                app.return_value.exec.assert_not_called()

    def test_first_launch_still_uses_regular_gui_startup(self):
        import main as desktop
        with patch.object(desktop, "SingleInstance") as guard:
            guard.return_value.__enter__.return_value.acquired = True
            with patch.object(desktop, "QApplication") as app:
                with patch.object(desktop, "startup_ready", return_value=True):
                    with patch.object(desktop, "MainWindow") as window:
                        with patch.object(desktop, "app_icon"):
                            desktop.main()
                            window.assert_called_once()
                            window.return_value.show.assert_called_once()
                            app.return_value.exec.assert_called_once()


if __name__ == "__main__":
    unittest.main()
