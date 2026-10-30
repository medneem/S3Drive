"""Автозапуск пользовательского монитора; службу и ключи S3 не изменяет."""
import unittest
from unittest.mock import Mock, patch

from tray_startup import startup_command, set_tray_autostart, get_tray_autostart


class TrayStartupTests(unittest.TestCase):
    def test_frozen_app_runs_in_hidden_tray_mode(self):
        with patch("tray_startup.sys.frozen", True, create=True):
            with patch("tray_startup.sys.executable", r"C:\\Apps\\S3Drive\\S3Drive.exe"):
                cmd = startup_command()
        self.assertIn("S3Drive.exe", cmd)
        self.assertIn("--tray", cmd)

    def test_autorun_sets_hkcu_key_only(self):
        winreg = Mock()
        winreg.HKEY_CURRENT_USER = 1
        winreg.KEY_SET_VALUE = 2
        winreg.REG_SZ = 1
        reg_key = Mock()
        reg_key.__enter__ = Mock(return_value=reg_key)
        reg_key.__exit__ = Mock(return_value=None)
        winreg.CreateKeyEx.return_value = reg_key
        with patch("tray_startup._registry", return_value=winreg):
            with patch("tray_startup.startup_command", return_value="S3Drive.exe --tray"):
                set_tray_autostart(True)
        winreg.SetValueEx.assert_called_once_with(
            reg_key, "S3DriveMonitor", 0, winreg.REG_SZ, "S3Drive.exe --tray"
        )

    def test_disabled_when_autorun_not_set(self):
        winreg = Mock()
        winreg.HKEY_CURRENT_USER = 1
        winreg.KEY_READ = 2
        winreg.QueryValueEx.side_effect = FileNotFoundError()
        key = Mock()
        key.__enter__ = Mock(return_value=key)
        key.__exit__ = Mock(return_value=None)
        winreg.OpenKey.return_value = key
        with patch("tray_startup._registry", return_value=winreg):
            self.assertFalse(get_tray_autostart())


if __name__ == "__main__":
    unittest.main()
