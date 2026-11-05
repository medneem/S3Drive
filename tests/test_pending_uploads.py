"""Regression tests for orphaned VFS uploads and Unicode path diagnostics."""
from __future__ import annotations

import io
import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from mounts import mount_command
from profiles import Profile
from windows_service import _control_request, ensure_vfs_idle, service_command


def profile() -> Profile:
    return Profile(
        id="4ef5373c-fc38-4862-afb7-3fc07022af8e",
        name="Личный сетевой диск",
        endpoint="http://nas.local:8010",
        access_key="test", secret_key="test",
        drive="S:", bucket="",
    )


class PendingUploadTests(unittest.TestCase):
    def test_user_mount_writes_back_closed_files_immediately(self):
        cmd = mount_command(profile(), "rclone.exe", Path("cache"), 33333)
        self.assertEqual(cmd.count("--vfs-write-back"), 1)
        self.assertEqual(cmd[cmd.index("--vfs-write-back") + 1], "0s")

    def test_service_mount_writes_back_closed_files_immediately(self):
        cmd = service_command(
            profile(), Path("C:/rclone.exe"), Path("C:/rclone.conf"),
            Path("C:/cache"), Path("C:/rclone.log"), Path("C:/rc.htpasswd"), 33333,
        )
        self.assertIn("--vfs-write-back 0s", cmd)

    def test_service_stop_explains_actual_queued_unicode_names(self):
        name = "test/Bomfunk MC's – In Stereo [2000]/03 – Привет.flac"
        idle = {"diskCache": {
            "uploadsQueued": 14,
            "uploadsInProgress": 0,
            "erroredFiles": 14,
        }}
        queue = {"queue": [{"id": 1, "name": name}, {"id": 2, "name": "test/cover.jpg"}]}
        with patch("windows_service._control_request", side_effect=[idle, queue]) as request:
            with self.assertRaises(RuntimeError) as raised:
                ensure_vfs_idle({"port": 12345, "password": "never_print_me"})
        text = str(raised.exception)
        self.assertIn("14", text)
        self.assertIn(name, text)
        self.assertIn("test/cover.jpg", text)
        self.assertNotIn("never_print_me", text)
        self.assertIn("Не удаляйте кэш", text)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args.args[-1], "vfs/queue")

    def test_error_message_truncates_large_queues_without_losing_count(self):
        idle = {"diskCache": {
            "uploadsQueued": 200,
            "uploadsInProgress": 0,
            "erroredFiles": 200,
        }}
        queue = {"queue": [{"id": n, "name": "test/" + str(n) + ".flac"} for n in range(200)]}
        with patch("windows_service._control_request", side_effect=[idle, queue]):
            with self.assertRaises(RuntimeError) as raised:
                ensure_vfs_idle({"port": 12345, "password": "x"})
        message = str(raised.exception)
        self.assertIn("ещё 195", message)
        self.assertLess(len(message), 1600)

    def test_failure_to_get_queue_never_allows_stop(self):
        stats = {"diskCache": {
            "uploadsQueued": 14,
            "uploadsInProgress": 0,
            "erroredFiles": 0,
        }}
        with patch("windows_service._control_request", side_effect=[
            stats, TimeoutError("RC offline"),
        ]):
            with self.assertRaisesRegex(RuntimeError, "Нельзя остановить службу"):
                ensure_vfs_idle({"port": 12, "password": "secret"})

    def test_service_rc_response_decodes_utf8_without_mojibake(self):
        queue = {"queue": [{"name": "test/Привет – музыка/песня.flac", "id": 123}]}
        stream = io.BytesIO(json.dumps(queue, ensure_ascii=False).encode("utf-8"))
        response = Mock()
        response.__enter__ = Mock(return_value=stream)
        response.__exit__ = Mock(return_value=False)
        with patch("windows_service.urlopen", return_value=response):
            decoded = _control_request(12345, "secret", "vfs/queue")
        self.assertEqual(decoded, queue)
        self.assertNotIn("â", decoded["queue"][0]["name"])


if __name__ == "__main__":
    unittest.main()
