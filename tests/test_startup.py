"""Driver dependency gate: no main window should launch without WinFsp."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from startup import StartupStatus, ensure_winfsp


class StartupGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.installer = Path(self.temp.name) / "winfsp-installer.msi"
        self.installer.write_bytes(b"msi-fixture")
        self.ask = Mock(return_value=True)
        self.run = Mock()

    def test_installed_driver_skips_prompt_and_allows_launch(self):
        outcome = ensure_winfsp(lambda: True, self.installer, self.ask, self.run)
        self.assertEqual(outcome.status, StartupStatus.READY)
        self.ask.assert_not_called()
        self.run.assert_not_called()

    def test_missing_driver_and_acceptance_launches_installer_then_exits(self):
        outcome = ensure_winfsp(lambda: False, self.installer, self.ask, self.run)
        self.assertEqual(outcome.status, StartupStatus.INSTALLER_STARTED)
        self.ask.assert_called_once()
        self.run.assert_called_once_with(self.installer)

    def test_decline_blocks_startup_without_launching_installer(self):
        self.ask.return_value = False
        outcome = ensure_winfsp(lambda: False, self.installer, self.ask, self.run)
        self.assertEqual(outcome.status, StartupStatus.DECLINED)
        self.run.assert_not_called()

    def test_missing_installer_blocks_startup_without_prompt(self):
        self.installer.unlink()
        outcome = ensure_winfsp(lambda: False, self.installer, self.ask, self.run)
        self.assertEqual(outcome.status, StartupStatus.INSTALLER_MISSING)
        self.ask.assert_not_called()
        self.run.assert_not_called()

    def test_installer_launch_failure_blocks_startup(self):
        self.run.side_effect = OSError("UAC denied")
        outcome = ensure_winfsp(lambda: False, self.installer, self.ask, self.run)
        self.assertEqual(outcome.status, StartupStatus.INSTALLER_FAILED)
        self.assertIn("UAC denied", outcome.error or "")

    def test_missing_driver_never_creates_main_window(self):
        import main as desktop
        with patch.object(desktop, "QApplication") as app:
            with patch.object(desktop, "startup_ready", return_value=False):
                with patch.object(desktop, "MainWindow") as window:
                    self.assertEqual(desktop.main(), 1)
                    window.assert_not_called()
                    app.return_value.exec.assert_not_called()


if __name__ == "__main__":
    unittest.main()
