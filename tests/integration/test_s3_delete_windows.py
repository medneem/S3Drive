"""Windows/WinFsp + local Moto S3: deletion through the actual rclone mount.

Run ONLY in a disposable Windows CI environment. No production endpoints or
credentials are read. Tests compare mounted-drive deletion with direct S3 API
state and a fresh remount; a vanishing Explorer directory is NOT success.
"""
from __future__ import annotations

import contextlib
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from urllib.request import urlopen

import boto3
from botocore.config import Config

from mounts import mount_command, remote_environment
from profiles import Profile


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class WindowsS3DeletionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.name != "nt":
            raise unittest.SkipTest("Real WinFsp mount requires Windows")
        cls.rclone = Path("tools/rclone.exe").resolve(strict=True)
        cls.root = tempfile.TemporaryDirectory(prefix="s3drive-delete-ci-")
        cls.folder = Path(cls.root.name)
        cls.port = free_port()
        cls.endpoint = f"http://127.0.0.1:{cls.port}"
        cls.service_log = (cls.folder / "moto.log").open("wb")
        cls.moto = subprocess.Popen(
            [sys.executable, "-m", "moto.server", "-H", "127.0.0.1",
             "-p", str(cls.port)],
            stdin=subprocess.DEVNULL, stdout=cls.service_log,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        cls.s3 = boto3.client(
            "s3", endpoint_url=cls.endpoint, region_name="us-east-1",
            aws_access_key_id="test", aws_secret_access_key="test",
            config=Config(s3={"addressing_style": "path"},
                          retries={"max_attempts": 1}),
        )
        for attempt in range(90):
            if cls.moto.poll() is not None:
                raise RuntimeError("Moto server exited before S3 tests")
            try:
                cls.s3.list_buckets()
                break
            except Exception:
                time.sleep(0.5)
        else:
            raise RuntimeError("Moto S3 did not become ready within 45s")
        cls.drive = next(
            (letter + ":" for letter in "RXYQZ"
             if not Path(letter + ":\\").exists()),
            None,
        )
        if cls.drive is None:
            raise RuntimeError("No free drive letter for WinFsp test")

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "moto"):
            cls.moto.terminate()
            try:
                cls.moto.wait(timeout=10)
            except subprocess.TimeoutExpired:
                cls.moto.kill()
                cls.moto.wait(timeout=10)
        if hasattr(cls, "service_log"):
            cls.service_log.close()
        if hasattr(cls, "root"):
            cls.root.cleanup()

    def setUp(self):
        # A unique, disposable bucket per test. Never use external endpoints.
        self.bucket = "s3drive-e2e-" + uuid.uuid4().hex[:12]
        self.s3.create_bucket(Bucket=self.bucket)

    def seed(self, *keys):
        for key in keys:
            self.s3.put_object(Bucket=self.bucket, Key=key, Body=b"test-content")

    def remaining(self, prefix):
        paginator = self.s3.get_paginator("list_objects_v2")
        return sorted(
            obj["Key"]
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix)
            for obj in page.get("Contents", [])
        )

    @contextlib.contextmanager
    def mounted(self, *, bucket, dir_cache_time=None):
        """Spawn the SAME rclone mount args used by the S3Drive GUI."""
        profile = Profile(
            id=str(uuid.uuid4()), name="S3Drive CI", endpoint=self.endpoint,
            bucket=bucket, access_key="test", secret_key="test",
            drive=self.drive, path_style=True,
        )
        cache = self.folder / ("cache-" + uuid.uuid4().hex)
        cache.mkdir()
        port = free_port()
        log_path = self.folder / ("rclone-" + uuid.uuid4().hex + ".log")
        # Use the production command (which refreshes S3 directory listings).
        # Optional override is only for investigating a known-bad rclone mode.
        cmd = mount_command(profile, str(self.rclone), cache, port) + [
            "--log-level", "DEBUG",
            "--log-file", str(log_path),
            "--rc-no-auth",
        ]
        if dir_cache_time is not None:
            cmd.extend(["--dir-cache-time", dir_cache_time])
        env = dict(os.environ)
        env.update(remote_environment(profile))
        # rclone must NOT fall back to user config or real AWS credentials.
        env["AWS_EC2_METADATA_DISABLED"] = "true"
        with open(os.devnull, "wb") as output:
            proc = subprocess.Popen(
                cmd, env=env, cwd=self.folder, stdout=output,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            root = Path(self.drive + "\\")
            try:
                for _ in range(120):
                    if proc.poll() is not None:
                        break
                    if root.exists():
                        break
                    time.sleep(0.25)
                else:
                    raise RuntimeError("WinFsp mount did not appear after 30 seconds")
                if proc.poll() is not None or not root.exists():
                    raise RuntimeError(
                        f"rclone mount failed (exit={proc.poll()}):\n"
                        + log_path.read_text(encoding="utf-8", errors="replace")[-5500:]
                    )
                yield root
            finally:
                try:
                    with urlopen(f"http://127.0.0.1:{port}/core/quit",
                                 data=b"", timeout=3):
                        pass
                except Exception:
                    pass
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=5)
                for _ in range(60):
                    if not root.exists():
                        break
                    time.sleep(0.1)
                print(f"Mount trace: {log_path}", flush=True)

    def test_00_windows_recognizes_both_mounts_as_network_drives(self):
        """WinFsp must not present S3 as a local fixed disk to Windows Installer."""
        import ctypes

        DRIVE_REMOTE = 4
        for bucket in (self.bucket, ""):
            with self.subTest(bucket=bucket):
                with self.mounted(bucket=bucket):
                    drive_type = ctypes.windll.kernel32.GetDriveTypeW(self.drive + "\\")
                    self.assertEqual(
                        drive_type, DRIVE_REMOTE,
                        "Windows sees S3Drive as local; MSI*.tmp directories may "
                        "be created as S3 buckets",
                    )

    def test_01_delete_nested_tree_in_named_bucket(self):
        """Remove a full nested tree; backend must contain zero matching keys."""
        self.seed("remove/album1/cover.jpg",
                  "remove/album1/tracks/01.flac",
                  "remove/album2/02.flac",
                  "keep/ok.txt")
        with self.mounted(bucket=self.bucket) as root:
            target = root / "remove"
            self.assertTrue(target.is_dir(), str(target))
            shutil.rmtree(target)
            self.assertFalse(target.exists())
        self.assertEqual(self.remaining("remove/"), [],
                         "Explorer removed the folder but S3 retained objects")
        self.assertEqual(self.remaining("keep/"), ["keep/ok.txt"])
        with self.mounted(bucket=self.bucket) as root:
            self.assertFalse((root / "remove").exists(),
                             "Deleted directory reappeared after remount")

    def test_02_delete_nested_tree_from_account_root(self):
        """Empty bucket profile: first folder is the BUCKET, not an S3 prefix."""
        self.seed("remove/deep/1.txt", "remove/deep/2.txt", "keep/file.txt")
        with self.mounted(bucket="") as root:
            target = root / self.bucket / "remove"
            self.assertTrue(target.is_dir(), str(target))
            shutil.rmtree(target)
            self.assertFalse(target.exists())
        self.assertEqual(self.remaining("remove/"), [],
                         "Root mount hid an incompletely deleted S3 tree")
        self.assertEqual(self.remaining("keep/"), ["keep/file.txt"])
        with self.mounted(bucket="") as root:
            self.assertFalse((root / self.bucket / "remove").exists(),
                             "Root-mount directory reappeared after remount")

    def test_03_external_change_inside_cached_directory(self):
        """Regression: default S3Drive mount deletes externally inserted files.

        Reproduced RED on production defaults before the fix: rclone reports
        success while stale/album/late.txt survives and returns after remount.
        GREEN requires server-side disappearance, not just Windows' view.
        """
        self.seed("stale/album/known.txt", "keep/ok.txt")
        outcome = None
        with self.mounted(bucket="") as root:
            directory = root / self.bucket / "stale"
            self.assertTrue(directory.is_dir())
            self.assertIn("album", os.listdir(directory))
            self.assertIn("known.txt", os.listdir(directory / "album"))
            # External writer, e.g. Cyberduck. rclone VFS cache stays warm.
            self.s3.put_object(Bucket=self.bucket,
                               Key="stale/album/late.txt", Body=b"late")
            try:
                shutil.rmtree(directory)
                outcome = "success"
            except OSError as exc:
                outcome = f"blocked: {type(exc).__name__} {exc}"
            print(f"stale-cache delete outcome: {outcome}", flush=True)
        leftovers = self.remaining("stale/")
        print(f"stale-cache backend survivors: {leftovers}", flush=True)
        with self.mounted(bucket="") as root:
            reappeared = (root / self.bucket / "stale").exists()
            print(f"stale-cache reappeared after remount: {reappeared}",
                  flush=True)
        if outcome == "success":
            self.assertEqual(
                leftovers, [],
                "REPRODUCED: Windows/rclone reports deletion success but "
                f"S3 still has {leftovers}; folder reappeared={reappeared}"
            )
            self.assertFalse(reappeared)
        else:
            self.assertTrue(leftovers,
                            "The delete failed but test has no surviving data")

    def test_04_external_change_in_named_bucket_default_mount(self):
        """The same race must be fixed for a named-bucket connection."""
        self.seed("fresh/album/known.txt", "keep/ok.txt")
        with self.mounted(bucket=self.bucket) as root:
            directory = root / "fresh"
            self.assertIn("album", os.listdir(directory))
            self.assertIn("known.txt", os.listdir(directory / "album"))
            self.s3.put_object(Bucket=self.bucket,
                               Key="fresh/album/late.txt", Body=b"late")
            shutil.rmtree(directory)
            self.assertFalse(directory.exists())
        survivors = self.remaining("fresh/")
        print(f"production mount named-bucket survivors: {survivors}", flush=True)
        self.assertEqual(survivors, [],
                         "Zero directory-cache did not eliminate stale removal")
        with self.mounted(bucket=self.bucket) as root:
            self.assertFalse((root / "fresh").exists(),
                             "Folder reappeared with production mount settings")

    def test_05_delete_external_directory_markers_in_named_bucket(self):
        """Cyberduck-style zero-byte directory objects must vanish too."""
        self.seed("marked/album/tracks/01.flac", "keep/ok.txt")
        for key in ("marked/", "marked/album/", "marked/album/tracks/"):
            self.s3.put_object(Bucket=self.bucket, Key=key, Body=b"")
        with self.mounted(bucket=self.bucket) as root:
            target = root / "marked"
            self.assertTrue((target / "album" / "tracks" / "01.flac").is_file())
            shutil.rmtree(target)
            self.assertFalse(target.exists())
        self.assertEqual(self.remaining("marked/"), [],
                         "S3 directory-marker keys remained after Explorer deletion")
        self.assertEqual(self.remaining("keep/"), ["keep/ok.txt"])
        with self.mounted(bucket=self.bucket) as root:
            self.assertFalse((root / "marked").exists(),
                             "S3 directory marker brought folder back after remount")

    def test_06_delete_empty_marked_directories_from_account_root(self):
        """Empty directories from another S3 client must be removable."""
        self.seed("keep/ok.txt")
        for key in ("empty/", "empty/nested/", "empty/nested/deeper/"):
            self.s3.put_object(Bucket=self.bucket, Key=key, Body=b"")
        with self.mounted(bucket="") as root:
            target = root / self.bucket / "empty"
            self.assertTrue((target / "nested" / "deeper").is_dir())
            shutil.rmtree(target)
            self.assertFalse(target.exists())
        self.assertEqual(self.remaining("empty/"), [],
                         "Empty Cyberduck-style directory markers remain in S3")
        self.assertEqual(self.remaining("keep/"), ["keep/ok.txt"])
        with self.mounted(bucket="") as root:
            self.assertFalse((root / self.bucket / "empty").exists(),
                             "Empty S3 folders reappeared after remount")


    def test_07_delete_entire_disposable_bucket_from_account_root(self):
        """Deleting a top-level Explorer folder must remove the S3 bucket too.

        A bucket is not just a folder prefix. Validate with direct ListBuckets,
        then remount; visually disappearing from Explorer is insufficient.
        """
        self.seed("album/tracks/01.flac", "album/cover.jpg")
        for key in ("album/", "album/tracks/"):
            self.s3.put_object(Bucket=self.bucket, Key=key, Body=b"")
        with self.mounted(bucket="") as root:
            target = root / self.bucket
            self.assertTrue(target.is_dir())
            shutil.rmtree(target)
            visible_after_delete = target.exists()
        buckets = {item["Name"] for item in self.s3.list_buckets()["Buckets"]}
        print("bucket delete: visible immediately =", visible_after_delete,
              "present in S3 API =", self.bucket in buckets, flush=True)
        if self.bucket in buckets:
            print("bucket delete survivors:", self.remaining(""), flush=True)
            logs = list(self.folder.glob("rclone-*.log"))
            if logs:
                recent = max(logs, key=lambda item: item.stat().st_mtime)
                events = [line for line in recent.read_text(encoding="utf-8", errors="replace").splitlines()
                          if any(word in line.lower() for word in
                                 ("rmdir", "remove", "delete", "bucket", "marker", "error"))]
                print("bucket delete rclone debug:", "\\n".join(events[-55:]), flush=True)
        self.assertNotIn(self.bucket, buckets,
                         "Explorer removed the bucket directory but S3 retained it")
        with self.mounted(bucket="") as root:
            self.assertFalse((root / self.bucket).exists(),
                             "Deleted S3 bucket reappeared after remount")
        self.assertFalse(visible_after_delete, "Bucket stayed visible before remount")


    def test_08_unicode_names_upload_immediately_and_delete_fully(self):
        """UTF-8/en dash are valid S3 keys, not a reason to strand VFS writes.

        Exercise the *actual* mounted WinFsp drive, confirm that a closed
        file reaches S3, and remove its parent directory. The S3 API must
        show no remaining objects, including directory markers.
        """
        album = "Bomfunk MC's – In Stereo [2000] – Музыка"
        track = "03 – Привет 世界.flac"
        prefix = f"test/{album}/"
        key = prefix + track
        with self.mounted(bucket="") as root:
            directory = root / self.bucket / "test" / album
            directory.mkdir(parents=True)
            (directory / track).write_bytes(b"unicode-file-content")
            for _ in range(120):
                if key in self.remaining(prefix):
                    break
                time.sleep(0.1)
            else:
                self.fail("Unicode filename was not written back to S3 within 12 seconds")
            shutil.rmtree(root / self.bucket / "test")
            self.assertFalse((root / self.bucket / "test").exists())
        self.assertEqual(
            self.remaining("test/"), [],
            "Unicode file or directory markers survived recursive deletion",
        )
        with self.mounted(bucket="") as root:
            self.assertFalse((root / self.bucket / "test").exists())


    def test_085_prefix_with_same_named_file_mounts_directory(self):
        """S3 may contain both an object named prefix and keys under prefix/."""
        self.seed("medneem", "medneem/visible.txt", "other/hidden.txt")
        with self.mounted(bucket=f"{self.bucket}/medneem") as root:
            self.assertEqual((root / "visible.txt").read_bytes(), b"test-content")
            self.assertFalse((root / "other").exists())
        self.assertEqual(self.remaining("medneem"), ["medneem", "medneem/visible.txt"])

    def test_086_write_file_and_directory_into_preexisting_prefix(self):
        """Windows writes inside a pre-existing bucket without re-creating it."""
        self.seed("medneem/existing.txt")
        with self.mounted(bucket=f"{self.bucket}/medneem") as root:
            self.assertEqual((root / "existing.txt").read_bytes(), b"test-content")
            (root / "sada.grc").write_bytes(b"small user file")
            for _ in range(150):
                if "medneem/sada.grc" in self.remaining("medneem/"):
                    break
                time.sleep(0.1)
            else:
                self.fail("File visible in WinFsp VFS was never uploaded to S3")

            directory = root / "Benny Benassi - Hypnotica"
            directory.mkdir()
            for _ in range(150):
                if "medneem/Benny Benassi - Hypnotica/" in self.remaining("medneem/"):
                    break
                time.sleep(0.1)
            else:
                self.fail("New Windows folder had no S3 directory marker")
        self.assertIn("medneem/sada.grc", self.remaining("medneem/"))
        self.assertIn("medneem/Benny Benassi - Hypnotica/", self.remaining("medneem/"))

    def test_087_windows_receives_directory_delete_notification(self):
        """Probe WinFsp -> Win32 notifications, independent of Explorer's UI.

        If Windows cannot observe this event, shortening the S3 directory
        cache timeout will not make the Explorer window refresh automatically.
        """
        import win32con
        import win32event
        import win32file

        self.seed("notify-me/child.txt", "keep/unchanged.txt")
        with self.mounted(bucket=self.bucket) as root:
            target = root / "notify-me"
            self.assertTrue(target.is_dir())
            handle = win32file.FindFirstChangeNotification(
                str(root), False, win32con.FILE_NOTIFY_CHANGE_DIR_NAME,
            )
            try:
                shutil.rmtree(target)
                observed = win32event.WaitForSingleObject(handle, 5000)
            finally:
                win32file.FindCloseChangeNotification(handle)
            diagnostics = []
            for logfile in sorted(self.folder.glob("rclone-*.log")):
                lines = logfile.read_text(encoding="utf-8", errors="replace").splitlines()
                diagnostics.extend(
                    line for line in lines if "Experimental WinFsp Notify" in line
                )
            self.assertEqual(
                observed, win32event.WAIT_OBJECT_0,
                "A successful directory deletion on the WinFsp mount did not "
                "produce a Win32 directory-change notification within 5s. "
                "Patched rclone diagnostics: " + repr(diagnostics[-10:]),
            )
            self.assertFalse(target.exists())
        self.assertEqual(self.remaining("notify-me/"), [])

    def test_088_read_directory_changes_windows_notification(self):
        """ReadDirectoryChangesW gives a second independent Win32 signal probe."""
        import queue
        import threading
        import win32con
        import win32file

        self.seed("notify-via-read/child.txt")
        with self.mounted(bucket=self.bucket) as root:
            directory = root / "notify-via-read"
            self.assertTrue(directory.is_dir())
            handle = win32file.CreateFile(
                str(root),
                0x0001,  # FILE_LIST_DIRECTORY
                (win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE |
                 win32con.FILE_SHARE_DELETE),
                None, win32con.OPEN_EXISTING,
                win32con.FILE_FLAG_BACKUP_SEMANTICS, None,
            )
            results = queue.Queue()
            started = threading.Event()

            def reader():
                started.set()
                try:
                    changes = win32file.ReadDirectoryChangesW(
                        handle, 32768, False, win32con.FILE_NOTIFY_CHANGE_DIR_NAME,
                    )
                    results.put(changes)
                except Exception as exc:
                    results.put(exc)

            thread = threading.Thread(target=reader, daemon=True)
            try:
                thread.start()
                self.assertTrue(started.wait(2))
                time.sleep(0.25)  # let the kernel register the watcher
                shutil.rmtree(directory)
                try:
                    changes = results.get(timeout=5)
                except queue.Empty:
                    self.fail("ReadDirectoryChangesW returned no event within 5s")
                if isinstance(changes, BaseException):
                    self.fail(f"ReadDirectoryChangesW failed: {changes}")
                self.assertTrue(
                    any(
                        action == 2 and name.lower() == "notify-via-read"
                        for action, name in changes
                    ),
                    f"ReadDirectoryChangesW received unrelated events: {changes}",
                )
            finally:
                try:
                    win32file.CancelIoEx(handle, None)
                except (OSError, AttributeError):
                    pass
                handle.Close()
                thread.join(timeout=1)
        self.assertEqual(self.remaining("notify-via-read/"), [])

    def test_09_real_windows_service_stops_via_authenticated_broker(self):
        """Real SCM + WinFsp rclone + VFS safety check + authenticated named pipe.

        All resources are disposable on the hosted Windows CI runner. The
        broker runs in a Python thread under the elevated CI account, while
        rclone really runs as a LocalSystem Windows service. No production
        credentials, profiles or services are used.
        """
        import base64
        import hashlib
        import json
        import threading
        from unittest.mock import patch

        from service_control import (
            config_fingerprint, current_user_sid, send_control, serve, wake_server,
        )
        from windows_service import (
            SERVICE_RUNNING, SERVICE_STOPPED, _control_request, _sc,
            config_text, service_command, service_name, service_status,
        )

        # Exercise a genuine nested bucket prefix, not only bucket root:
        # this is the same mount shape as users/medneem in the bug report.
        self.seed("medneem/visible.txt", "other/hidden.txt")
        profile = Profile(
            id=str(uuid.uuid4()), name="S3Drive CI stop test",
            endpoint=self.endpoint, bucket=f"/{self.bucket}/medneem/",
            access_key="test", secret_key="test",
            drive=self.drive, service_mode=True,
        )
        root = self.folder / ("service-" + uuid.uuid4().hex[:12])
        root.mkdir()
        cache = root / "cache"
        cache.mkdir()
        config = root / "rclone.conf"
        config.write_text(config_text(profile), encoding="utf-8")
        logfile = root / "rclone.jsonl"
        secret = "s3drive-ci-only-" + uuid.uuid4().hex
        password_file = root / "rc.htpasswd"
        password_file.write_text(
            "s3drive:{SHA}" + base64.b64encode(
                hashlib.sha1(secret.encode("utf-8")).digest()
            ).decode("ascii") + "\n",
            encoding="utf-8",
        )
        port = free_port()
        (root / "control.json").write_text(
            json.dumps({"port": port, "password": secret}), encoding="utf-8",
        )
        command = service_command(
            profile, self.rclone, config, cache, logfile, password_file, port,
        )
        service = service_name(profile.id)
        worker_errors = []
        event = threading.Event()
        broker = None
        created = False
        stopped_by_broker = False
        try:
            _sc("create", service, "binPath=", command, "start=", "auto",
                "obj=", "LocalSystem", "DisplayName=", "S3Drive CI " + profile.drive)
            created = True
            _sc("start", service)

            # SERVICE_RUNNING may precede rclone's authenticated RC server.
            # Confirm the actual VFS is reachable and reports a safe cache.
            for _ in range(120):
                if service_status(profile.id) == SERVICE_RUNNING:
                    try:
                        stats = _control_request(port, secret, "vfs/stats")
                        if "diskCache" in stats:
                            break
                    except (OSError, ValueError, KeyError):
                        pass
                time.sleep(0.25)
            else:
                detail = logfile.read_text("utf-8", errors="replace")[-4000:] if logfile.exists() else ""
                self.fail("Windows service never exposed usable VFS stats:\n" + detail)

            # The service is not healthy solely because SCM reports RUNNING.
            # The mounted drive must actually serve objects under the prefix.
            mounted_root = Path(self.drive + "\\\\")
            for _ in range(80):
                try:
                    if (mounted_root / "visible.txt").read_bytes() == b"test-content":
                        break
                except OSError:
                    pass
                time.sleep(0.25)
            else:
                self.fail("SCM reports RUNNING, but the nested S3 prefix is unreadable")
            self.assertFalse((mounted_root / "other").exists(),
                             "Mount escaped its configured S3 prefix")

            def broker_loop():
                try:
                    serve(event)
                except Exception as exc:
                    worker_errors.append(exc)

            owner = current_user_sid()
            enrollment = {
                "owner_sid": owner,
                "fingerprint": config_fingerprint(profile),
                "drive": profile.drive,
            }
            # Only the installed owner may control this service, as in the
            # real broker. service_dir points to this disposable test fixture.
            with patch("service_control.load_enrollment", return_value=enrollment), \
                 patch("windows_service.service_dir", return_value=root):
                broker = threading.Thread(target=broker_loop, daemon=True)
                broker.start()
                send_control("stop", profile)
                for _ in range(120):
                    if service_status(profile.id) == SERVICE_STOPPED:
                        stopped_by_broker = True
                        break
                    time.sleep(0.25)
                self.assertTrue(stopped_by_broker, "SCM service remains running after broker stop")
                self.assertFalse(worker_errors, f"Broker thread failed: {worker_errors}")
        finally:
            event.set()
            if broker is not None:
                wake_server()
                broker.join(timeout=5)
            if created:
                # This is a disposable no-writes test service. On test failure,
                # clean up SCM resources rather than leaving them on the runner.
                try:
                    if service_status(profile.id) == SERVICE_RUNNING:
                        _sc("stop", service)
                        for _ in range(100):
                            if service_status(profile.id) == SERVICE_STOPPED:
                                break
                            time.sleep(0.2)
                finally:
                    _sc("delete", service)
            print("Real SCM broker stop succeeded:", stopped_by_broker, flush=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
