"""Persistent background mount and safe unmount tests."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from background import BackgroundRecord, BackgroundRegistry, sessions_to_stop


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


if __name__ == "__main__":
    unittest.main()
