"""Regression tests: visible update percentage, verification and window shutdown."""
from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
import zipfile
from types import SimpleNamespace
from unittest.mock import Mock, patch

from auto_update import PAYLOAD_FILES, stage_update, UpdateInfo
from main import MainWindow


class UpdateProgressTests(unittest.TestCase):
    def test_report_actual_download_percentage_and_verification_phase(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name in PAYLOAD_FILES:
                archive.writestr(name, "1.4.0\n" if name == "VERSION.txt" else "test")
        data = stream.getvalue()
        total = len(data)
        info = UpdateInfo("1.4.0", "https://example.invalid/update.zip",
                          hashlib.sha256(data).hexdigest(), total)
        progress = []
        phases = []
        with tempfile.TemporaryDirectory() as folder:
            stage_update(info, opener=lambda *_args, **_kwargs: io.BytesIO(data),
                         parent=Path(folder),
                         on_progress=lambda written, expected: progress.append((written, expected)),
                         on_verifying=lambda: phases.append("verify"))
        self.assertTrue(progress)
        self.assertEqual(progress[-1], (total, total))
        self.assertTrue(all(0 < written <= expected == total for written, expected in progress))
        self.assertEqual(phases, ["verify"])

    def test_progress_button_displays_percentage_in_both_languages(self):
        from localization import set_language
        button = Mock()
        status = Mock()
        gui = SimpleNamespace(update_button=button, statusBar=lambda: status,
                              available_release=SimpleNamespace(version="1.5.3"),
                              _downloading_update=True)
        try:
            set_language("en")
            MainWindow.on_release_progress(gui, 63, 100)
            self.assertIn("63%", button.setText.call_args.args[0])
            self.assertIn("63%", status.showMessage.call_args.args[0])
            set_language("ru")
            MainWindow.on_release_progress(gui, 94, 100)
            self.assertIn("94%", button.setText.call_args.args[0])
            self.assertIn("94%", status.showMessage.call_args.args[0])
        finally:
            set_language("en")

    def test_verification_replaces_percentage_without_claiming_success(self):
        button = Mock()
        status = Mock()
        gui = SimpleNamespace(update_button=button, statusBar=lambda: status,
                              available_release=SimpleNamespace(version="1.5.3"),
                              _downloading_update=True)
        MainWindow.on_release_verifying(gui)
        self.assertNotIn("100%", button.setText.call_args.args[0])
        self.assertIn("SHA-256", button.setText.call_args.args[0])

    def test_update_shutdown_restores_qt_quit_and_stops_gui_timers(self):
        app = Mock()
        button = Mock()
        popup = SimpleNamespace()
        timers = [Mock() for _ in range(5)]
        gui = SimpleNamespace(
            update_button=button, update_spinner=Mock(),
            profiles=[], mounts=Mock(), close=Mock(return_value=True),
            show=Mock(), _closing_for_update=False,
            mount_timer=timers[0], label_timer=timers[1],
            timer=timers[2], monitor_timer=timers[3],
            release_check_timer=timers[4],
        )
        with patch("main.QApplication.instance", return_value=app):
            with patch("main.launch_updater") as updater:
                with patch("main.QTimer.singleShot") as defer:
                    MainWindow.on_release_downloaded(gui, popup, "")
        updater.assert_called_once()
        gui.close.assert_called_once()
        self.assertTrue(gui._closing_for_update)
        app.setQuitOnLastWindowClosed.assert_any_call(False)
        app.setQuitOnLastWindowClosed.assert_any_call(True)
        defer.assert_called_once_with(0, app.quit)
        for timer in timers:
            timer.stop.assert_called_once()

    def test_cancelled_close_never_launches_updater(self):
        app = Mock()
        gui = SimpleNamespace(
            update_button=Mock(), update_spinner=Mock(),
            profiles=[], mounts=Mock(), close=Mock(return_value=False),
            _closing_for_update=False,
        )
        with patch("main.QApplication.instance", return_value=app):
            with patch("main.launch_updater") as updater:
                with patch("main.discard_update") as discard:
                    MainWindow.on_release_downloaded(gui, SimpleNamespace(), "")
        updater.assert_not_called()
        discard.assert_called_once()
        self.assertFalse(gui._closing_for_update)
        app.setQuitOnLastWindowClosed.assert_any_call(True)

    def test_cannot_reopen_hidden_gui_when_update_is_finishing(self):
        from main import MainWindow
        gui = SimpleNamespace(_closing_for_update=True, isMinimized=Mock())
        gui.showNormal = Mock()
        gui.show = Mock()
        gui.raise_ = Mock()
        gui.activateWindow = Mock()
        MainWindow.activate_existing_window(gui)
        gui.show.assert_not_called()
        gui.showNormal.assert_not_called()

    def test_activation_restores_hidden_or_minimized_existing_gui(self):
        gui = SimpleNamespace(_closing_for_update=False,
                              isMinimized=Mock(return_value=True),
                              showNormal=Mock(), show=Mock(), raise_=Mock(),
                              activateWindow=Mock())
        MainWindow.activate_existing_window(gui)
        gui.showNormal.assert_called_once()
        gui.raise_.assert_called_once()
        gui.activateWindow.assert_called_once()


if __name__ == "__main__":
    unittest.main()
