"""Cache deletion safety and nonprivileged transfer telemetry regression tests."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from cache_ops import ensure_cache_idle, ensure_uploads_idle, erase_cache_directory
from datetime import datetime, timedelta, timezone
import json

from transfer_monitor import (parse_snapshot, parse_service_stats, latest_service_stats,
                              read_service_snapshot)


def clean_stats():
    return {"inUse": 1, "diskCache": {
        "uploadsQueued": 0, "uploadsInProgress": 0, "erroredFiles": 0,
    }}


class CacheSafetyTests(unittest.TestCase):
    def test_idle_allows_cleanup(self):
        ensure_cache_idle(clean_stats())

    def test_queued_active_or_failed_upload_blocks_both_actions(self):
        for key in ("uploadsQueued", "uploadsInProgress", "erroredFiles"):
            stats = clean_stats()
            stats["diskCache"][key] = 1
            for action in (ensure_uploads_idle, ensure_cache_idle):
                with self.subTest(key=key, action=action.__name__):
                    with self.assertRaises(RuntimeError):
                        action(stats)

    def test_vfs_in_use_baseline_is_not_an_open_file(self):
        # rclone 1.75.2 vfs.New calls inUse.Store(1) before any files open.
        stats = clean_stats()
        self.assertEqual(stats["inUse"], 1)
        ensure_uploads_idle(stats)
        ensure_cache_idle(stats)

    def test_extra_vfs_refs_only_block_destructive_cache_clear(self):
        stats = clean_stats()
        stats["inUse"] = 2
        ensure_uploads_idle(stats)
        with self.assertRaisesRegex(RuntimeError, "ссыл"):
            ensure_cache_idle(stats)

    def test_service_stop_does_not_require_in_use_counter(self):
        stats = clean_stats()
        stats.pop("inUse")
        ensure_uploads_idle(stats)
        with self.assertRaises(KeyError):
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

    def test_core_stats_from_service_log_show_active_upload(self):
        state = parse_service_stats({
            "transferring": [{"name": "backup.zip", "percentage": 68, "speedAvg": 1048576}],
            "errors": 0,
        })
        self.assertEqual(state.uploading, 0)
        self.assertEqual(state.unknown, 1)
        self.assertEqual(state.current_file, "backup.zip")
        self.assertEqual(state.percent, 68)
        self.assertEqual(state.speed, 1048576)
        self.assertEqual(state.queued, 0)

    def test_recent_service_json_stats_read_without_rc_credentials(self):
        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "rclone.log"
            records = [
                {"time": (now - timedelta(seconds=4)).isoformat(),
                 "stats": {"transferring": [{"name": "old.bin"}]}},
                {"time": now.isoformat(), "stats": {
                    "transferring": [{"name": "new.bin", "percentage": 70}],
                    "errors": 0,
                }},
            ]
            log.write_text(
                "старый текстовый лог\n" +
                "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in records),
                encoding="utf-8",
            )
            self.assertEqual(latest_service_stats(log, now=now).current_file, "new.bin")
            with patch("windows_service.service_dir", return_value=Path(td)):
                with patch("transfer_monitor.read_user_snapshot", side_effect=AssertionError):
                    self.assertEqual(read_service_snapshot("profile").downloading, 1)

    def test_old_log_and_stale_stats_do_not_report_an_upload(self):
        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "rclone.log"
            log.write_text("2026/10/30 INFO : old plain-text log\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "нет свежей статистики|Нет свежей статистики"):
                latest_service_stats(log, now=now)
            record = {
                "time": (now - timedelta(minutes=1)).isoformat(),
                "stats": {"transferring": [{"name": "stale.zip"}]},
            }
            log.write_text(json.dumps(record) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "устарела"):
                latest_service_stats(log, now=now)

    def test_cannot_follow_service_log_symlink(self):
        with tempfile.TemporaryDirectory() as td:
            actual = Path(td) / "original"
            actual.write_text("contents", encoding="utf-8")
            symlink = Path(td) / "link"
            try:
                symlink.symlink_to(actual)
            except (OSError, NotImplementedError):
                self.skipTest("Symlink creation is not supported on this runner.")
            with self.assertRaisesRegex(RuntimeError, "ссылк"):
                latest_service_stats(symlink)



if __name__ == "__main__":
    unittest.main()
