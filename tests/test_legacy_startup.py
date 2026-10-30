"""Удаление устаревшего HKCU автозапуска трея без повреждения чужих записей."""
import unittest
from unittest.mock import Mock

from legacy_startup import remove_legacy_tray_autostart


class LegacyStartupCleanupTests(unittest.TestCase):
    def registry(self, value=None):
        api = Mock()
        api.HKEY_CURRENT_USER = 1
        api.KEY_QUERY_VALUE = 1
        api.KEY_SET_VALUE = 2
        key = Mock()
        key.__enter__ = Mock(return_value=key)
        key.__exit__ = Mock(return_value=None)
        api.OpenKey.return_value = key
        if value is None:
            api.QueryValueEx.side_effect = FileNotFoundError()
        else:
            api.QueryValueEx.return_value = (value, api.REG_SZ)
        return api, key

    def test_removes_only_our_previous_tray_run_entry(self):
        api, key = self.registry(r'"C:\Apps\S3Drive\S3Drive.exe" --tray')
        self.assertTrue(remove_legacy_tray_autostart(api))
        api.OpenKey.assert_called_once_with(
            api.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0, api.KEY_QUERY_VALUE | api.KEY_SET_VALUE
        )
        api.DeleteValue.assert_called_once_with(key, "S3DriveMonitor")
        api.SetValueEx.assert_not_called()

    def test_preserves_unrelated_registry_value(self):
        for value in (
            r'C:\Program Files\OtherApp.exe --tray',
            r'"C:\Apps\S3Drive.exe" --normal',
            "other data",
        ):
            with self.subTest(value=value):
                api, _ = self.registry(value)
                self.assertFalse(remove_legacy_tray_autostart(api))
                api.DeleteValue.assert_not_called()

    def test_missing_run_entry_is_safe(self):
        api, _ = self.registry()
        self.assertFalse(remove_legacy_tray_autostart(api))
        api.DeleteValue.assert_not_called()


if __name__ == "__main__":
    unittest.main()
