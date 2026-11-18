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
        gui.name.setText.assert_called_once_with("Personal network drive")
        gui.path_style.setChecked.assert_called_once_with(True)
        gui.shortcut_checkbox.setChecked.assert_called_once_with(True)
        gui.open_checkbox.setChecked.assert_called_once_with(False)
        gui.background_checkbox.setChecked.assert_called_once_with(False)
        gui.service_checkbox.setChecked.assert_called_once_with(True)

    @staticmethod
    def window_for_bucket_save(bucket, *, service_mode=False):
        """Exercise real GUI Save logic without displaying a Qt window."""
        from unittest.mock import Mock
        from types import SimpleNamespace
        p = new_profile(
            name="SILO", endpoint="http://localhost:9010", bucket=bucket,
            access_key="key", secret_key="secret", drive="M:",
            service_mode=service_mode,
        )
        ui = SimpleNamespace(
            settings_load_error=False, draft=Mock(return_value=p),
            profiles=[], mounts=Mock(), store=Mock(),
            bucket=Mock(), refresh_items=Mock(), status=Mock(),
            pending_mounts={}, update_status=Mock(),
        )
        ui.mounts.is_mounted.return_value = False
        ui.save_profile = lambda: MainWindow.save_profile(ui)
        ui.selected_profile = lambda: ui.profiles[0] if ui.profiles else None
        return ui, p

    def test_save_normalizes_nested_path_in_config_and_visible_bucket_field(self):
        from unittest.mock import patch
        for source in ("users/medneem", "/users/medneem",
                       "/users/medneem/", "users/medneem/"):
            with self.subTest(source=source):
                ui, p = self.window_for_bucket_save(source)
                with patch("main.service_status", return_value=None):
                    self.assertTrue(MainWindow.save_profile(ui))
                expected = source if source.endswith("/") else source + "/"
                self.assertEqual(p.bucket, expected)
                self.assertEqual(ui.profiles[0].bucket, expected)
                ui.store.save.assert_called_once()
                self.assertEqual(ui.store.save.call_args.args[0][0].bucket, expected)
                ui.bucket.setText.assert_called_once_with(expected)

    def test_save_does_not_add_slash_to_bucket_only_or_empty_path(self):
        from unittest.mock import patch
        for source in ("users", "", "/users"):
            with self.subTest(source=source):
                ui, p = self.window_for_bucket_save(source)
                with patch("main.service_status", return_value=None):
                    self.assertTrue(MainWindow.save_profile(ui))
                self.assertEqual(ui.profiles[0].bucket, source)
                ui.bucket.setText.assert_called_once_with(source)

    def test_connect_normalizes_nested_path_before_mount(self):
        from unittest.mock import patch
        for service in (False, True):
            with self.subTest(service=service):
                ui, p = self.window_for_bucket_save("users/medneem", service_mode=service)
                with patch("main.service_status", return_value=None), \
                     patch("main.winfsp_installed", return_value=True), \
                     patch("main.run_admin") as admin, \
                     patch("main.notify_explorer"), \
                     patch("main.QMessageBox.warning") as warning:
                    MainWindow.mount_profile(ui)
                self.assertEqual(ui.profiles[0].bucket, "users/medneem/")
                ui.bucket.setText.assert_called_once_with("users/medneem/")
                ui.store.save.assert_called_once()
                warning.assert_not_called()
                if service:
                    admin.assert_called_once_with("install", p.id)
                    ui.mounts.start.assert_not_called()
                else:
                    ui.mounts.start.assert_called_once_with(p)
                    admin.assert_not_called()

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
        self.assertIn('QPushButton(tr("Очистить кэш"))', source)
        self.assertIn('self.clear_cache_button.clicked.connect(self.clear_cache_profile)', source)
        self.assertIn('self.monitor_timer.timeout.connect(self.poll_transfers)', source)
        self.assertIn("class TransferWindow(QDialog)", inspect.getsource(__import__("main")))
        self.assertIn("self.transfer_popup.show()", source)
        self.assertNotIn('QGroupBox("Загрузка в S3")', source)
        self.assertNotIn('self.transfer_label = QLabel', source)


    def test_window_closes_normally_and_does_not_hide_to_tray(self):
        import inspect
        import main as desktop
        source = inspect.getsource(desktop.MainWindow)
        self.assertNotIn("QSystemTrayIcon", source)
        self.assertNotIn("self.tray", source)
        self.assertNotIn("setQuitOnLastWindowClosed(False)", inspect.getsource(desktop.MainWindow.closeEvent))
        # A user-requested update temporarily suppresses GUI quit only while
        # staging the external updater; the ordinary close path is unchanged.
        self.assertIn("setQuitOnLastWindowClosed(False)", inspect.getsource(desktop.MainWindow.on_release_downloaded))
        self.assertIn("setQuitOnLastWindowClosed(True)", inspect.getsource(desktop.MainWindow.on_release_downloaded))
        self.assertNotIn("self.hide()", inspect.getsource(desktop.MainWindow.closeEvent))
        self.assertIn("self.hide_transfer_popup()", inspect.getsource(desktop.MainWindow.closeEvent))

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

    def test_stop_repairs_old_broker_once_then_retries_safe_stop(self):
        """Updating the GUI alone does not update an already installed broker."""
        from unittest.mock import patch
        import main

        profile = SimpleNamespace(id="test-service-id", drive="U:", service_mode=True)
        window = SimpleNamespace(
            selected_profile=Mock(return_value=profile),
            mounts=Mock(), pending_mounts={}, status=Mock(), update_status=Mock(),
        )
        with patch("main.service_status", return_value=main.SERVICE_RUNNING), \
             patch("main.send_control", side_effect=[
                 RuntimeError("Внутренняя ошибка управления службой."), None
             ]) as send, \
             patch("main.run_admin") as admin, \
             patch("main.notify_explorer"), \
             patch("main.QMessageBox.warning") as warning:
            MainWindow.unmount_profile(window)
        self.assertEqual(send.call_count, 2)
        admin.assert_called_once_with("repair-control", profile.id)
        warning.assert_not_called()

    def test_stop_does_not_retry_or_force_shutdown_on_upload_error(self):
        from unittest.mock import patch
        import main

        profile = SimpleNamespace(id="test-service-id", drive="U:", service_mode=True)
        window = SimpleNamespace(
            selected_profile=Mock(return_value=profile),
            mounts=Mock(), pending_mounts={}, status=Mock(), update_status=Mock(),
        )
        with patch("main.service_status", return_value=main.SERVICE_RUNNING), \
             patch("main.send_control",
                   side_effect=RuntimeError("Нельзя остановить службу: файлы в очереди")) as send, \
             patch("main.run_admin") as admin, \
             patch("main.QMessageBox.warning") as warning:
            MainWindow.unmount_profile(window)
        send.assert_called_once_with("stop", profile)
        admin.assert_not_called()
        warning.assert_called_once()
        self.assertEqual(warning.call_args.args[2], "Нельзя остановить службу: файлы в очереди")

    def test_transfer_dialog_has_no_hidden_main_window_parent(self):
        import inspect
        from main import MainWindow
        source = inspect.getsource(MainWindow)
        self.assertIn("TransferWindow(profile, None)", source)
        self.assertNotIn("QSystemTrayIcon(", source)
        self.assertIn("self.monitor_timer.timeout.connect(self.poll_transfers)", source)



if __name__ == "__main__":
    unittest.main()
