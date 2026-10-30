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
    def mounted(self, *, bucket):
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
        cmd = mount_command(profile, str(self.rclone), cache, port) + [
            "--dir-cache-time", "1h",
            "--poll-interval", "0",
            "--log-level", "DEBUG",
            "--log-file", str(log_path),
            "--rc-no-auth",
        ]
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
        """Repro of stale VFS listing: new S3 object inserted after directory read.

        A successful shutil.rmtree followed by remaining keys is a DATA
        CONSISTENCY BUG. Do not accept a disappearing local folder as deletion.
        A clean failure (ENOTEMPTY) is safer than false success.
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
