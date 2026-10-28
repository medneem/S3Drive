"""Persistent background mount and safe unmount tests."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from background import BackgroundRecord, BackgroundRegistry, sessions_to_stop
from mounts import MountManager, Session


class RegistryTests(unittest.TestCase):
    def test_rc_password_is_encrypted_and_can_be_restored(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "sessions.json"
            store = BackgroundRegistry(path,
                encrypt=lambda s: "cipher:" + s[::-1],
                decrypt=lambda s: s.removeprefix("cipher:")[::-1])
            record = BackgroundRecord("profile-1", "S:", 13531, 4561, "very-secret-rc-pass", True)
            store.put(record)
            content = path.read_text(encoding="utf-8")
            self.assertNotIn("very-secret-rc-pass", content)
            self.assertEqual(store.load()["profile-1"], record)
            store.delete("profile-1")
            self.assertEqual(store.load(), {})

    def test_malformed_registry_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "sessions.json"
            path.write_text('{"sessions": "broken"}', encoding="utf-8")
            store = BackgroundRegistry(path, encrypt=lambda s: s, decrypt=lambda s: s)
            with self.assertRaises(ValueError):
                store.put(BackgroundRecord("x", "S:", 12345, 10, "pw", False))
            self.assertEqual(path.read_text(encoding="utf-8"), '{"sessions": "broken"}')


class LifetimeTests(unittest.TestCase):
    def test_close_only_disconnects_non_background_mounts(self):
        mounts = [
            mock.Mock(profile=mock.Mock(id="a"), keep_running=True),
            mock.Mock(profile=mock.Mock(id="b"), keep_running=False),
        ]
        self.assertEqual(sessions_to_stop(mounts), ["b"])

    def test_all_background_mounts_allow_window_to_close(self):
        mounts = [mock.Mock(profile=mock.Mock(id="a"), keep_running=True)]
        self.assertEqual(sessions_to_stop(mounts), [])




class MountManagerTests(unittest.TestCase):
    def registry(self, root):
        return BackgroundRegistry(Path(root) / "background.json",
            encrypt=lambda s: "cipher:" + s[::-1],
            decrypt=lambda s: s.removeprefix("cipher:")[::-1])

    def profile(self):
        from profiles import new_profile
        return new_profile(name="Test", endpoint="http://nas.local:8010",
                           bucket="my-backup", access_key="foo", secret_key="bar")

    def test_adopts_authenticated_process_with_matching_pid(self):
        with tempfile.TemporaryDirectory() as root:
            profile = self.profile()
            reg = self.registry(root)
            reg.put(BackgroundRecord(profile.id, profile.drive, 4567, 1234, "pw", True))
            manager = MountManager(reg)
            with mock.patch.object(manager, "_rc_pid", return_value=1234):
                manager.recover([profile])
            self.assertTrue(manager.sessions[profile.id].keep_running)
            self.assertIsNone(manager.sessions[profile.id].process)

    def test_does_not_adopt_reused_port_owned_by_other_process(self):
        with tempfile.TemporaryDirectory() as root:
            profile = self.profile()
            reg = self.registry(root)
            reg.put(BackgroundRecord(profile.id, profile.drive, 4567, 1234, "pw", True))
            manager = MountManager(reg)
            with mock.patch.object(manager, "_rc_pid", return_value=9999):
                with mock.patch("mounts.Path.exists", return_value=False):
                    manager.recover([profile])
            self.assertNotIn(profile.id, manager.sessions)

    def test_queued_uploads_prevent_disconnect(self):
        with tempfile.TemporaryDirectory() as root:
            profile = self.profile()
            reg = self.registry(root)
            reg.put(BackgroundRecord(profile.id, profile.drive, 4567, 1234, "pw", True))
            manager = MountManager(reg)
            manager.sessions[profile.id] = Session(profile, None, 4567, "pw", 1234, True)
            with mock.patch.object(manager, "_rc_pid", return_value=1234):
                with mock.patch.object(manager, "_rc", return_value={
                    "diskCache": {"uploadsQueued": 2, "uploadsInProgress": 0, "erroredFiles": 0}
                }) as rc:
                    with self.assertRaisesRegex(RuntimeError, "Диск не отключён"):
                        manager.stop(profile.id)
                    rc.assert_called_once()
            self.assertIn(profile.id, manager.sessions)

    def test_background_mode_toggle_persists_for_live_disk(self):
        with tempfile.TemporaryDirectory() as root:
            profile = self.profile()
            reg = self.registry(root)
            reg.put(BackgroundRecord(profile.id, profile.drive, 4567, 1234, "pw", True))
            manager = MountManager(reg)
            manager.sessions[profile.id] = Session(profile, None, 4567, "pw", 1234, True)
            with mock.patch.object(manager, "_rc_pid", return_value=1234):
                manager.set_keep_running(profile.id, False)
            self.assertFalse(reg.load()[profile.id].keep_running)

if __name__ == "__main__":
    unittest.main()
