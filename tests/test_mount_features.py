"""Desktop shortcut and mount-confirmation contract tests."""
import json
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import Mock

from drive_actions import MountState, create_desktop_shortcut, disk_root, mount_state, open_mounted_disk
from profiles import Profile, ProfileStore


def profile():
    return Profile(
        id="test-id", name="QNAP", endpoint="http://nas.local:8010",
        bucket="my-backup", access_key="access-fixture", secret_key="secret-fixture", drive="S:",
    )


def encrypted_store(path):
    return ProfileStore(
        path,
        encrypt=lambda value: "sealed:" + value[::-1],
        decrypt=lambda value: value.removeprefix("sealed:")[::-1],
    )


class ProfilePreferencesTests(unittest.TestCase):
    def test_defaults_are_backward_compatible(self):
        values = asdict(profile())
        del values["create_shortcut"]
        del values["open_after_mount"]
        previous = Profile(**values)
        previous.validate()
        self.assertFalse(previous.create_shortcut)
        self.assertFalse(previous.open_after_mount)

    def test_saves_and_reloads_both_preferences_in_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            p = profile()
            p.create_shortcut = True
            p.open_after_mount = True
            store = encrypted_store(path)
            store.save([p])
            doc = json.loads(path.read_text(encoding="utf-8"))
            self.assertIs(doc["profiles"][0]["create_shortcut"], True)
            self.assertIs(doc["profiles"][0]["open_after_mount"], True)
            self.assertEqual(store.load(), [p])
            self.assertNotIn(p.secret_key, path.read_text(encoding="utf-8"))

    def test_old_config_loads_with_unchecked_preferences(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            store = encrypted_store(path)
            store.save([profile()])
            doc = json.loads(path.read_text(encoding="utf-8"))
            del doc["profiles"][0]["create_shortcut"]
            del doc["profiles"][0]["open_after_mount"]
            path.write_text(json.dumps(doc), encoding="utf-8")
            self.assertFalse(store.load()[0].create_shortcut)
            self.assertFalse(store.load()[0].open_after_mount)

    def test_non_boolean_preference_is_rejected(self):
        p = profile()
        p.create_shortcut = "false"
        with self.assertRaises(ValueError):
            p.validate()


class MountReadinessTests(unittest.TestCase):
    def test_started_process_is_not_yet_a_success(self):
        self.assertEqual(
            mount_state(process_alive=True, drive_ready=False, elapsed_seconds=1),
            MountState.WAITING,
        )

    def test_only_visible_running_mount_is_successful(self):
        self.assertEqual(
            mount_state(process_alive=True, drive_ready=True, elapsed_seconds=1),
            MountState.READY,
        )

    def test_exited_process_must_not_report_success_even_if_drive_remains(self):
        self.assertEqual(
            mount_state(process_alive=False, drive_ready=True, elapsed_seconds=1),
            MountState.FAILED,
        )

    def test_timeout_while_process_is_running(self):
        self.assertEqual(
            mount_state(process_alive=True, drive_ready=False, elapsed_seconds=40),
            MountState.TIMED_OUT,
        )


class DesktopActionTests(unittest.TestCase):
    def test_creates_shortcut_to_drive_root_in_redirected_desktop(self):
        with tempfile.TemporaryDirectory() as tmp:
            shortcut = Mock()
            shell = Mock()
            shell.SpecialFolders.return_value = tmp
            shell.CreateShortcut.return_value = shortcut
            location, created = create_desktop_shortcut("S:", shell)
            self.assertEqual(location, Path(tmp) / "S3Drive (S).lnk")
            self.assertTrue(created)
            self.assertEqual(shortcut.TargetPath, "S:\\")
            shortcut.Save.assert_called_once()

    def test_existing_shortcut_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            existing = Path(tmp) / "S3Drive (S).lnk"
            existing.write_text("existing", encoding="utf-8")
            shell = Mock()
            shell.SpecialFolders.return_value = tmp
            location, created = create_desktop_shortcut("S:", shell)
            self.assertEqual(location, existing)
            self.assertFalse(created)
            shell.CreateShortcut.assert_not_called()

    def test_drive_open_action_uses_mount_path(self):
        launcher = Mock()
        open_mounted_disk("S:", startfile=launcher)
        launcher.assert_called_once_with("S:\\")

    def test_drive_letter_is_validated(self):
        with self.assertRaises(ValueError):
            disk_root("C:\\Users")


if __name__ == "__main__":
    unittest.main()
