"""Направления передач и полнота снимка rclone/VFS."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from transfer_monitor import (
    TransferItem, parse_snapshot, parse_service_stats, latest_service_stats,
)


def vfs(queued=0, uploading=0):
    return {"diskCache": {
        "uploadsQueued": queued, "uploadsInProgress": uploading,
        "erroredFiles": 0, "bytesUsed": 4096,
    }}


class BidirectionalStatsTests(unittest.TestCase):
    def test_rc_distinguishes_simultaneous_upload_and_download(self):
        queue = {"queue": [
            {"name": "folder/upload.zip", "uploading": True, "size": 300},
        ]}
        core = {"transferring": [
            {"name": "folder/upload.zip", "size": 300, "bytes": 150,
             "percentage": 50, "speedAvg": 120, "eta": 1.25},
            {"name": "downloads/photo.jpg", "size": 800, "bytes": 200,
             "percentage": 25, "speedAvg": 60, "eta": 10},
        ]}
        state = parse_snapshot(vfs(1, 1), queue, core)
        self.assertEqual((state.uploading, state.downloading), (1, 1))
        self.assertEqual([item.direction for item in state.transfers], ["upload", "download"])
        self.assertEqual(state.transfers[1].bytes_done, 200)
        self.assertEqual(state.transfers[1].size, 800)
        self.assertEqual(state.transfers[1].eta, 10)
        self.assertEqual(state.transfers[0].percent, 50)

    def test_queued_file_is_not_misidentified_as_download(self):
        queue = {"queue": [{"name": "queued.txt", "size": 100, "uploading": False}]}
        state = parse_snapshot(vfs(1, 0), queue, {"transferring": []})
        self.assertEqual([(t.name, t.direction) for t in state.transfers],
                         [("queued.txt", "queued")])
        self.assertEqual(state.downloading, 0)

    def test_unknown_upload_with_mismatched_names_not_labeled_download(self):
        queue = {"queue": [{"name": "some/prefix", "uploading": True}]}
        state = parse_snapshot(vfs(1, 1), queue, {
            "transferring": [{"name": "different", "percentage": 40}]})
        self.assertEqual(state.transfers[0].direction, "unknown")
        self.assertEqual(state.downloading, 0)

    def test_download_only_active_in_core_stats(self):
        state = parse_snapshot(vfs(), {"queue": []}, {
            "transferring": [{"name": "video.mkv", "size": 1000, "bytes": 500}]})
        self.assertEqual(state.uploading, 0)
        self.assertEqual(state.downloading, 1)
        self.assertEqual(state.transfers[0].direction, "download")

    def test_service_log_classifies_upload_events_and_downloads(self):
        now = datetime.now(timezone.utc)
        records = [
            {"time": (now - timedelta(seconds=12)).isoformat(),
             "level": "info", "object": "upload.zip",
             "msg": "vfs cache: queuing for upload in 5s"},
            {"time": (now - timedelta(seconds=3)).isoformat(),
             "level": "info", "stats": {"transferring": [
                 {"name": "upload.zip", "bytes": 20, "size": 100},
                 {"name": "download.jpg", "bytes": 20, "size": 100},
             ]}},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rclone.log"
            path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
            state = latest_service_stats(path, now=now)
        self.assertEqual([t.direction for t in state.transfers], ["upload", "download"])
        self.assertEqual((state.uploading, state.downloading), (1, 1))

    def test_service_success_event_removes_upload_from_tracking(self):
        now = datetime.now(timezone.utc)
        records = [
            {"time": (now - timedelta(seconds=20)).isoformat(),
             "object": "item", "msg": "vfs cache: queuing for upload in 5s"},
            {"time": (now - timedelta(seconds=3)).isoformat(),
             "object": "item", "msg": "vfs cache: upload succeeded try #1"},
            {"time": now.isoformat(), "stats": {
                "transferring": [{"name": "item", "percentage": 80}]}}
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rclone.log"
            path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
            state = latest_service_stats(path, now=now)
        self.assertEqual(state.transfers[0].direction, "download")

    def test_bad_service_log_stat_rows_are_ignored(self):
        with self.assertRaises(ValueError):
            parse_service_stats({"transferring": "oops"})
        with self.assertRaises(ValueError):
            parse_snapshot(vfs(), {"queue": "oops"}, {"transferring": []})

    def test_total_active_includes_unknown_and_queue(self):
        state = parse_service_stats({"transferring": [
            {"name": "x", "size": 10, "bytes": 1}]}, upload_names=None)
        self.assertEqual(state.transfers[0].direction, "unknown")
        self.assertTrue(state.has_activity)

    def test_service_logs_are_not_misread_after_stale_sample(self):
        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rclone.log"
            record = {"time": (now - timedelta(minutes=2)).isoformat(),
                      "stats": {"transferring": [{"name": "file"}]}}
            path.write_text(json.dumps(record) + "\n", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                latest_service_stats(path, now=now)



class TransferDialogTests(unittest.TestCase):
    def test_download_only_opens_independent_transfer_window(self):
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        from main import MainWindow
        from profiles import new_profile

        profile = new_profile(name="QNAP", endpoint="http://nas:8010", bucket="backup",
                              access_key="test", secret_key="test")
        state = parse_snapshot(vfs(), {"queue": []}, {
            "transferring": [{"name": "movie.mkv", "bytes": 100, "size": 200}],
        })
        ui = SimpleNamespace(
            monitor_pending=True, profiles=[profile], monitor_last_error="",
            idle_polls=0, uploads_seen=set(), dismissed_uploads=set(),
            transfer_popup=None, transfer_popup_profile=None,
        )
        with patch("main.TransferWindow") as dialog:
            MainWindow.on_transfers(ui, profile.id, state, "")
        dialog.assert_called_once_with(profile, None)
        dialog.return_value.show.assert_called_once_with()
        dialog.return_value.display.assert_called_once_with(state)
        self.assertEqual(ui.transfer_popup_profile, profile.id)
        self.assertEqual(state.downloading, 1)
        self.assertFalse(ui.monitor_pending)

    def test_dialog_lists_both_directions_in_separate_rows(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from main import TransferWindow
        state = parse_snapshot(vfs(1, 1), {
            "queue": [{"name": "up.zip", "uploading": True}],
        }, {"transferring": [
            {"name": "up.zip", "size": 1000, "bytes": 300},
            {"name": "down.zip", "size": 1000, "bytes": 700},
        ]})
        ui = SimpleNamespace(
            summary=Mock(), table=Mock(), overall_progress=Mock(),
        )
        TransferWindow.display(ui, state)
        ui.table.setRowCount.assert_called_once_with(2)
        self.assertEqual(ui.table.setItem.call_count, 10)
        self.assertIn("На S3: 1", ui.summary.setText.call_args.args[0])
        self.assertIn("С S3: 1", ui.summary.setText.call_args.args[0])
        ui.overall_progress.setValue.assert_called_once_with(50)


if __name__ == "__main__":
    unittest.main()
