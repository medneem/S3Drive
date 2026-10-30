"""Cache deletion safety and nonprivileged transfer telemetry regression tests."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cache_ops import ensure_cache_idle, erase_cache_directory
from transfer_monitor import parse_snapshot, service_rc_port, _public_rc


def clean_stats():
    return {"inUse": 0, "diskCache": {
        "uploadsQueued": 0, "uploadsInProgress": 0, "erroredFiles": 0,
    }}


class CacheSafetyTests(unittest.TestCase):
    def test_idle_allows_cleanup(self):
        ensure_cache_idle(clean_stats())

    def test_queued_active_failed_or_open_file_blocks_cache_clear(self):
        for key in ("uploadsQueued", "uploadsInProgress", "erroredFiles"):
            stats = clean_stats()
            stats["diskCache"][key] = 1
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                ensure_cache_idle(stats)
        stats = clean_stats()
        stats["inUse"] = 1
        with self.assertRaises(RuntimeError):
            ensure_cache_idle(stats)

    def test_missing_status_fails_closed(self):
        with self.assertRaises(KeyError):
            ensure_cache_idle({"diskCache": {"uploadsQueued": 0}})

    def test_only_specific_cache_subdirectory_is_deleted(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp) / "cache"
            target = parent / "profile-id"
            target.mkdir(parents=True)
            (target / "data").write_text("cached", encoding="utf-8")
            unrelated = parent / "other-profile"
            unrelated.mkdir()
            (unrelated / "data").write_text("keep", encoding="utf-8")
            self.assertTrue(erase_cache_directory(target, parent=parent))
            self.assertFalse(target.exists())
            self.assertTrue(unrelated.exists())
            self.assertFalse(erase_cache_directory(target, parent=parent))

    def test_rejects_path_escape_and_directory_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent = root / "cache"
            parent.mkdir()
            with self.assertRaises(ValueError):
                erase_cache_directory(root / "other", parent=parent)
            actual = root / "outside"
            actual.mkdir()
            (actual / "sensitive").write_text("keep", encoding="utf-8")
            symlink = parent / "linked"
            try:
                symlink.symlink_to(actual, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("Symlinks require privileges on this runner.")
            with self.assertRaises(RuntimeError):
                erase_cache_directory(symlink, parent=parent)
            self.assertTrue((actual / "sensitive").exists())


class TransferMonitorTests(unittest.TestCase):
    def test_parse_upload_count_errors_and_active_file(self):
        vfs = {"diskCache": {
            "uploadsQueued": 3, "uploadsInProgress": 1, "erroredFiles": 0,
            "bytesUsed": 2048,
        }}
        queue = {"queue": [{"name": "backup.zip", "uploading": True, "size": 4096}]}
        core = {"transferring": [{
            "name": "backup.zip", "percentage": 67, "speedAvg": 131072,
        }]}
        state = parse_snapshot(vfs, queue, core)
        self.assertEqual((state.queued, state.uploading, state.errors), (3, 1, 0))
        self.assertEqual(state.percent, 67)
        self.assertEqual(state.speed, 131072)

    def test_never_treat_missing_vfs_cache_as_upload_complete(self):
        with self.assertRaises(KeyError):
            parse_snapshot({}, {}, {})

    def test_scm_port_parser_accepts_loopback_only(self):
        self.assertEqual(service_rc_port(
            r'C:\\bin\\rclone.exe --rc-addr 127.0.0.1:45833 --rc-htpasswd a'
        ), 45833)
        with self.assertRaises(RuntimeError):
            service_rc_port("--rc-addr 0.0.0.0:45833")
        with self.assertRaises(ValueError):
            service_rc_port("--rc-addr 127.0.0.1:99999")

    def test_public_rc_refuses_privileged_methods(self):
        with self.assertRaises(ValueError):
            _public_rc(1234, "core/quit")


if __name__ == "__main__":
    unittest.main()
