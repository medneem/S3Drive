"""GUI defaults for new S3 profiles; existing saved choices must remain intact."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from main import MainWindow
from profiles import new_profile


def stub_window():
    props = ("items", "name", "endpoint", "bucket", "access", "secret", "drive",
             "path_style", "shortcut_checkbox", "open_checkbox",
             "background_checkbox", "service_checkbox", "region", "status")
    return SimpleNamespace(**{name: Mock() for name in props})


class NewProfileDefaultsTests(unittest.TestCase):
    def test_new_profile_has_user_selected_defaults(self):
        gui = stub_window()
        MainWindow.new_profile(gui)
        gui.name.setText.assert_called_once_with("Личный сетевой диск")
        gui.path_style.setChecked.assert_called_once_with(True)
        gui.shortcut_checkbox.setChecked.assert_called_once_with(True)
        gui.open_checkbox.setChecked.assert_called_once_with(False)
        gui.background_checkbox.setChecked.assert_called_once_with(False)
        gui.service_checkbox.setChecked.assert_called_once_with(True)

    def test_loading_saved_profile_preserves_existing_checkbox_choices(self):
        p = new_profile(
            name="Existing", endpoint="http://nas.local:8010", bucket="backup",
            access_key="key", secret_key="secret",
            path_style=False, create_shortcut=False, open_after_mount=True,
            keep_running=True, service_mode=False,
        )
        gui = stub_window()
        gui.selected_profile = Mock(return_value=p)
        gui.update_status = Mock()
        MainWindow.select_profile(gui, None, None)
        gui.path_style.setChecked.assert_called_once_with(False)
        gui.shortcut_checkbox.setChecked.assert_called_once_with(False)
        gui.open_checkbox.setChecked.assert_called_once_with(True)
        gui.background_checkbox.setChecked.assert_called_once_with(True)
        gui.service_checkbox.setChecked.assert_called_once_with(False)


    def test_main_window_includes_cache_clear_button_and_transfer_monitor(self):
        import inspect
        source = inspect.getsource(MainWindow)
        self.assertIn('QPushButton("Очистить кэш")', source)
        self.assertIn('self.clear_cache_button.clicked.connect(self.clear_cache_profile)', source)
        self.assertIn('self.monitor_timer.timeout.connect(self.poll_transfers)', source)
        self.assertIn("class TransferWindow(QDialog)", inspect.getsource(__import__("main")))
        self.assertIn("self.transfer_popup.show()", source)
        self.assertNotIn('QGroupBox("Загрузка в S3")', source)
        self.assertNotIn('self.transfer_label = QLabel', source)


    def test_closing_main_window_hides_to_tray_and_keeps_monitor_active(self):
        from main import MainWindow
        from unittest.mock import Mock
        from types import SimpleNamespace
        ui = SimpleNamespace(
            tray=Mock(), quitting=False, hide=Mock(),
            tray_notice_shown=True,
        )
        ui.tray.isVisible.return_value = True
        event = Mock()
        MainWindow.closeEvent(ui, event)
        event.ignore.assert_called_once_with()
        ui.hide.assert_called_once_with()

    def test_user_can_explicitly_quit_from_tray(self):
        from main import MainWindow
        from unittest.mock import Mock, patch
        from types import SimpleNamespace
        ui = SimpleNamespace(quitting=False, close=Mock(return_value=True), tray=Mock())
        with patch("main.QApplication") as app:
            MainWindow.exit_from_tray(ui)
        self.assertTrue(ui.quitting)
        ui.close.assert_called_once_with()
        ui.tray.hide.assert_called_once_with()
        app.quit.assert_called_once_with()

    def test_monitor_checks_running_service_even_if_other_profile_is_selected(self):
        from main import MainWindow
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        other = SimpleNamespace(id="other", service_mode=True)
        running = SimpleNamespace(id="running", service_mode=True)
        ui = SimpleNamespace(
            monitor_pending=False, profiles=[other, running],
            mounts=Mock(), transfer_popup_profile=None, transfer_popup=None,
            monitor_index=0, on_transfers=Mock(),
        )
        with patch("main.service_status",
                   side_effect=lambda pid: 4 if pid == "running" else 1):
            with patch("main._MonitorTask") as task:
                with patch("main.QThreadPool.globalInstance") as pool:
                    MainWindow.poll_transfers(ui)
        task.assert_called_once_with(running, ui.mounts)
        pool.return_value.start.assert_called_once_with(task.return_value)
        self.assertTrue(ui.monitor_pending)

    def test_transfer_dialog_has_no_hidden_main_window_parent(self):
        import inspect
        from main import MainWindow
        source = inspect.getsource(MainWindow)
        self.assertIn("TransferWindow(profile, None)", source)
        self.assertIn("QSystemTrayIcon(", source)
        self.assertIn("self.monitor_timer.timeout.connect(self.poll_transfers)", source)



if __name__ == "__main__":
    unittest.main()
