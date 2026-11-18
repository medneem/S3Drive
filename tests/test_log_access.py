"""Profile log paths are separated; no automatic error notifications."""
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from log_access import profile_log_path


class LogPathTests(unittest.TestCase):
    def test_profile_log_paths_for_user_mount_and_windows_service(self):
        user = SimpleNamespace(id="test-id", service_mode=False)
        service = SimpleNamespace(id="service-id", service_mode=True)
        with patch("log_access.app_data_dir", return_value=Path("user-data")), \
             patch("log_access.service_dir", return_value=Path("service-data")):
            self.assertEqual(profile_log_path(user), Path("user-data/logs/test-id.log"))
            self.assertEqual(profile_log_path(service), Path("service-data/rclone.log"))

    def test_diagnostic_log_lookup_remains_available_to_settings(self):
        from main import MainWindow
        import inspect
        settings = inspect.getsource(MainWindow.open_settings)
        self.assertIn("profile_log_path(profile)", settings)
        self.assertNotIn("open_transfer_error_logs", inspect.getsource(MainWindow))


if __name__ == "__main__":
    unittest.main()
