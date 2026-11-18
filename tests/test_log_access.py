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


if __name__ == "__main__":
    unittest.main()
