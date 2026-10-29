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


if __name__ == "__main__":
    unittest.main()
