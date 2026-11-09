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
    def test_update_button_hidden_initially_and_manual_check_is_in_settings(self):
        source = inspect.getsource(MainWindow.__init__)
        self.assertIn('QPushButton(tr("Обновить"))', source)
        self.assertIn("self.update_button.setVisible(False)", source)
        self.assertIn("self.update_button.clicked.connect(self.start_release_update)", source)
        self.assertIn("self.release_check_timer.timeout.connect(self.check_release_on_startup)", source)
        self.assertIn('QPushButton(tr("Проверить обновление"))', inspect.getsource(MainWindow.open_settings))
        self.assertIn('QCheckBox(tr("Проверять новую версию при запуске"))', inspect.getsource(MainWindow.open_settings))
        self.assertNotIn("stage_update(", source)

    def test_check_starts_with_metadata_only(self):
        with patch("main.check_latest_release", return_value=release()) as check:
            with patch("main.stage_update") as download:
                task = _ReleaseCheckTask()
                task.signals.checked.connect(lambda offered, error: None)
                task.run()
                check.assert_called_once()
                download.assert_not_called()

    def test_found_release_exposes_update_button(self):
        status = Mock()
        gui = SimpleNamespace(
            available_release=None,
            _manual_release_check=False,
            _release_check_running=True,
            update_button=Mock(),
            statusBar=Mock(return_value=status),
        )
        MainWindow.on_release_checked(gui, release(), "")
        self.assertEqual(gui.available_release.version, "1.3.0")
        gui.update_button.setVisible.assert_called_once_with(True)
        gui.update_button.setEnabled.assert_called_once_with(True)

    def test_update_button_click_starts_only_download_worker(self):
        gui = SimpleNamespace(
            available_release=release(),
            update_button=Mock(),
            update_pool=Mock(),
            statusBar=Mock(),
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
        gui.update_button.setText.assert_called_once_with("Downloading…")

    def test_cancelled_close_never_starts_updater(self):
        from auto_update import StagedUpdate
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as td:
            folder = Path(td) / "staged"
            folder.mkdir()
            archive = folder / "update.zip"
            archive.write_bytes(b"archive")
            staged = StagedUpdate(archive, "a" * 64, "1.3.0", folder)
            gui = SimpleNamespace(
                update_button=Mock(), profiles=[], mounts=Mock(),
                close=Mock(return_value=False), show=Mock(),
            )
            with patch("main.QApplication") as app, patch("main.launch_updater") as launch:
                MainWindow.on_release_downloaded(gui, staged, "")
                launch.assert_not_called()
                gui.update_button.setEnabled.assert_called_once_with(True)
                app.instance.return_value.setQuitOnLastWindowClosed.assert_any_call(True)
            self.assertFalse(folder.exists())

    def test_active_background_disk_blocks_update(self):
        from auto_update import StagedUpdate
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as td:
            folder = Path(td) / "staged"
            folder.mkdir()
            archive = folder / "update.zip"
            archive.write_bytes(b"archive")
            staged = StagedUpdate(archive, "a" * 64, "1.3.0", folder)
            gui = SimpleNamespace(
                update_button=Mock(),
                profiles=[SimpleNamespace(id="p1", drive="S:", keep_running=True)],
                mounts=Mock(), close=Mock(),
            )
            gui.mounts.is_mounted.return_value = True
            with patch("main.QMessageBox") as message, patch("main.launch_updater") as launch:
                MainWindow.on_release_downloaded(gui, staged, "")
                launch.assert_not_called()
                gui.close.assert_not_called()
                message.warning.assert_called_once()
            self.assertFalse(folder.exists())

    def test_accepted_close_launches_updater_without_touching_services(self):
        from auto_update import StagedUpdate
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as td:
            folder = Path(td) / "staged"
            folder.mkdir()
            archive = folder / "update.zip"
            archive.write_bytes(b"archive")
            staged = StagedUpdate(archive, "a" * 64, "1.3.0", folder)
            gui = SimpleNamespace(
                update_button=Mock(),
                profiles=[SimpleNamespace(id="s1", drive="S:", keep_running=False,
                                         service_mode=True)],
                mounts=Mock(), close=Mock(return_value=True), show=Mock(),
            )
            with patch("main.QApplication") as app, patch("main.launch_updater") as launch:
                MainWindow.on_release_downloaded(gui, staged, "")
                gui.close.assert_called_once()
                launch.assert_called_once()
                app.instance.return_value.quit.assert_called_once()
            self.assertTrue(folder.exists())  # updater owns it after GUI exits

    def test_click_without_release_does_not_download(self):
        gui = SimpleNamespace(
            available_release=None, update_button=Mock(), update_pool=Mock(),
        )
        MainWindow.start_release_update(gui)
        gui.update_pool.start.assert_not_called()

    def test_manual_check_runs_even_if_startup_check_is_disabled(self):
        gui = SimpleNamespace(
            store=SimpleNamespace(check_updates_on_startup=False),
            _release_check_running=False, _manual_release_check=False,
            update_pool=Mock(), statusBar=Mock(),
        )
        MainWindow.check_release(gui, manual=True)
        gui.update_pool.start.assert_called_once()
        self.assertTrue(gui._manual_release_check)

    def test_startup_check_honors_persisted_disabled_setting(self):
        gui = SimpleNamespace(
            store=SimpleNamespace(check_updates_on_startup=False),
            _release_check_running=False, update_pool=Mock(),
        )
        MainWindow.check_release_on_startup(gui)
        gui.update_pool.start.assert_not_called()

    def test_user_is_told_when_manual_check_fails(self):
        gui = SimpleNamespace(
            available_release=None, _manual_release_check=True,
            _release_check_running=True, update_button=Mock(),
            statusBar=Mock(),
        )
        with patch("main.QMessageBox") as message:
            MainWindow.on_release_checked(gui, None, "GitHub timeout")
            message.warning.assert_called_once()
        self.assertIn("GitHub timeout", gui.statusBar.return_value.showMessage.call_args.args[0])
        self.assertFalse(gui._release_check_running)

    def test_automatic_check_failure_retries_without_interrupting_user(self):
        gui = SimpleNamespace(
            available_release=None, _manual_release_check=False,
            _release_check_running=True, update_button=Mock(),
            statusBar=Mock(), release_check_timer=Mock(),
            store=SimpleNamespace(check_updates_on_startup=True),
        )
        with patch("main.QMessageBox") as message:
            MainWindow.on_release_checked(gui, None, "offline")
            message.warning.assert_not_called()
        gui.release_check_timer.start.assert_called_once_with(5 * 60 * 1000)
        self.assertFalse(gui._release_check_running)

    def test_disabled_startup_check_does_not_schedule_retry(self):
        gui = SimpleNamespace(
            available_release=None, _manual_release_check=False,
            _release_check_running=True, update_button=Mock(),
            statusBar=Mock(), release_check_timer=Mock(),
            store=SimpleNamespace(check_updates_on_startup=False),
        )
        MainWindow.on_release_checked(gui, None, "offline")
        gui.release_check_timer.start.assert_not_called()

    def test_no_new_release_does_not_expose_update_button(self):
        gui = SimpleNamespace(
            available_release=None, _manual_release_check=True,
            _release_check_running=True, update_button=Mock(),
            statusBar=Mock(),
        )
        MainWindow.on_release_checked(gui, None, "")
        gui.update_button.setVisible.assert_not_called()
        self.assertFalse(gui._release_check_running)


if __name__ == "__main__":
    unittest.main()
