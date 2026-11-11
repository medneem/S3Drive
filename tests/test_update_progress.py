"""Regression tests: visible update percentage, verification and window shutdown."""
from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from auto_update import stage_update, UpdateInfo
from main import MainWindow


class UpdateProgressTests(unittest.TestCase):
    def test_report_actual_download_percentage_and_verification_phase(self):
        from tests.test_auto_update import bundle_bytes
        data = bundle_bytes()
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
            MainWindow.on_release_progress(gui, 63)
            self.assertIn("63%", button.setText.call_args.args[0])
            self.assertIn("63%", status.showMessage.call_args.args[0])
            set_language("ru")
            MainWindow.on_release_progress(gui, 94)
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
