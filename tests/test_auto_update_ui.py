"""GUI update button must remain hidden until a newer stable release is found."""
from __future__ import annotations
import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from auto_update import UpdateInfo
from main import MainWindow, _ReleaseCheckTask


def release() -> UpdateInfo:
    return UpdateInfo(
        "1.3.0",
        "https://github.com/medneem/S3Drive/releases/download/v1.3.0/S3Drive-v1.3.0-windows-x64.zip",
        "a" * 64,
        1024,
    )


class ManualUpdateButtonTests(unittest.TestCase):
    def test_hidden_initially_with_no_unprompted_download(self):
        source = inspect.getsource(MainWindow.__init__)
        self.assertIn('QPushButton("Обновить")', source)
        self.assertIn("self.update_button.setVisible(False)", source)
        self.assertIn("self.update_button.clicked.connect(self.start_release_update)", source)
        self.assertNotIn("stage_update(", source)

    def test_check_starts_with_metadata_only(self):
        with patch("main.check_latest_release", return_value=release()) as check:
            with patch("main.stage_update") as download:
                task = _ReleaseCheckTask()
                task.signals.checked.connect(lambda offered, error: None)
                task.run()
                check.assert_called_once()
                download.assert_not_called()

    def test_button_appears_only_when_release_is_available(self):
        status = Mock()
        gui = SimpleNamespace(
            available_release=None,
            update_button=Mock(),
            statusBar=Mock(return_value=status),
        )
        MainWindow.on_release_checked(gui, None, "")
        MainWindow.on_release_checked(gui, None, "offline")
        gui.update_button.setVisible.assert_not_called()
        MainWindow.on_release_checked(gui, release(), "")
        self.assertEqual(gui.available_release.version, "1.3.0")
        gui.update_button.setVisible.assert_called_once_with(True)
        gui.update_button.setEnabled.assert_called_once_with(True)

    def test_update_button_click_starts_only_download_worker(self):
        gui = SimpleNamespace(
            available_release=release(),
            update_button=Mock(),
            update_pool=Mock(),
            on_release_downloaded=Mock(),
        )
        gui.update_button.isEnabled.return_value = True
        with patch("main.stage_update") as download:
            MainWindow.start_release_update(gui)
            gui.update_pool.start.assert_called_once()
            worker = gui.update_pool.start.call_args.args[0]
            self.assertEqual(worker.release.version, "1.3.0")
            download.assert_not_called()  # Worker queued, not run implicitly.
        gui.update_button.setEnabled.assert_called_once_with(False)
        gui.update_button.setText.assert_called_once_with("Загрузка…")

    def test_click_does_nothing_if_no_release(self):
        gui = SimpleNamespace(
            available_release=None, update_button=Mock(), update_pool=Mock(),
        )
        MainWindow.start_release_update(gui)
        gui.update_pool.start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
