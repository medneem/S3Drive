"""Automatic release update is opt-in to stable releases and safe for portable installs."""
from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

from auto_update import (
    PAYLOAD_FILES, UpdateInfo, check_latest_release, launch_updater,
    parse_release, stage_update, verify_archive,
)
from update_helper import apply_bundle


def release(version="1.4.0", digest="a" * 64):
    return {
        "tag_name": f"v{version}",
        "draft": False, "prerelease": False,
        "assets": [{
            "name": f"S3Drive-v{version}-windows-x64.zip",
            "size": 123,
            "digest": f"sha256:{digest}",
            "browser_download_url": (
                f"https://github.com/medneem/S3Drive/releases/download/"
                f"v{version}/S3Drive-v{version}-windows-x64.zip"
            ),
        }],
    }


def bundle_bytes(version="1.4.0", additions=None):
    data = {name: ("new:" + name).encode("utf-8") for name in PAYLOAD_FILES}
    data["VERSION.txt"] = (version + "\n").encode()
    data.update(additions or {})
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, body in data.items():
            archive.writestr(name, body)
    return stream.getvalue()


class ReleaseTests(unittest.TestCase):
    def test_official_stable_release_only_if_newer(self):
        self.assertEqual(parse_release(release("1.3.0"), "1.2.2").version, "1.3.0")
        self.assertIsNone(parse_release(release("1.2.2"), "1.2.2"))
        self.assertIsNone(parse_release(release("1.2.1"), "1.2.2"))
        self.assertIsNone(parse_release(release("1.99.0"), "1.2.2-dev.40"))
        self.assertIsNone(parse_release(release("1.99.0"), "invalid"))
        bad = release()
        bad["draft"] = True
        self.assertIsNone(parse_release(bad, "1.2.2"))
        bad = release()
        bad["prerelease"] = True
        self.assertIsNone(parse_release(bad, "1.2.2"))

    def test_release_requires_official_asset_and_sha256(self):
        for value in (None, "sha256:bad", "md5:" + "0" * 32):
            with self.subTest(digest=value):
                data = release()
                data["assets"][0]["digest"] = value
                with self.assertRaises(ValueError):
                    parse_release(data, "1.2.2")
        data = release()
        data["assets"][0]["browser_download_url"] = "https://evil.example/app.zip"
        with self.assertRaises(ValueError):
            parse_release(data, "1.2.2")
        data = release()
        data["assets"][0]["name"] = "S3Drive-v1.4.0-windows-x64.exe"
        with self.assertRaises(ValueError):
            parse_release(data, "1.2.2")

    def test_stable_versions_compared_numerically(self):
        self.assertEqual(parse_release(release("1.10.0"), "1.9.9").version, "1.10.0")
        self.assertIsNone(parse_release(release("1.9.9"), "1.10.0"))

    def test_offline_latest_check_fails_without_interfering_with_app(self):
        opener = Mock(side_effect=OSError("offline"))
        with self.assertRaises(OSError):
            check_latest_release("1.2.2", opener=opener)
        opener.assert_called_once()

    def test_dev_build_does_not_even_contact_github(self):
        opener = Mock()
        self.assertIsNone(check_latest_release("1.0.0-dev.5", opener=opener))
        opener.assert_not_called()


class ArchiveTests(unittest.TestCase):
    def test_staged_archive_checked_with_sha256_and_exact_contents(self):
        payload = bundle_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        data = release(digest=digest)
        data["assets"][0]["size"] = len(payload)
        update = parse_release(data, "1.2.2")
        self.assertIsNotNone(update)
        opener = Mock(return_value=io.BytesIO(payload))
        with tempfile.TemporaryDirectory() as td:
            staged = stage_update(update, opener=opener, parent=Path(td))
            self.assertEqual(staged.archive.read_bytes(), payload)
            verify_archive(staged.archive, digest, "1.4.0")

    def test_checksum_mismatch_discards_download(self):
        payload = bundle_bytes()
        data = release(digest="0" * 64)
        data["assets"][0]["size"] = len(payload)
        update = parse_release(data, "1.2.2")
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):
                stage_update(update, opener=Mock(return_value=io.BytesIO(payload)),
                             parent=Path(td))
            self.assertFalse(list(Path(td).iterdir()))

    def test_archive_rejects_config_and_zip_slip(self):
        for extra in ({"config.json": b"not allowed"}, {"../evil.exe": b"attack"}):
            payload = bundle_bytes(additions=extra)
            with tempfile.TemporaryDirectory() as td:
                path = Path(td) / "archive.zip"
                path.write_bytes(payload)
                with self.assertRaises(ValueError):
                    verify_archive(path, hashlib.sha256(payload).hexdigest(), "1.4.0")

    def test_archive_rejects_wrong_version(self):
        payload = bundle_bytes("1.3.0")
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "archive.zip"
            path.write_bytes(payload)
            with self.assertRaises(ValueError):
                verify_archive(path, hashlib.sha256(payload).hexdigest(), "1.4.0")


class InstallationTests(unittest.TestCase):
    def test_replaces_only_program_files_preserving_config_and_services(self):
        payload = bundle_bytes()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "install"
            root.mkdir()
            (root / "S3Drive.exe").write_bytes(b"old")
            (root / "config.json").write_text('{"secrets":"sealed"}')
            (root / "cache").mkdir()
            (root / "cache" / "queued.bin").write_bytes(b"unsent")
            archive = Path(td) / "archive.zip"
            archive.write_bytes(payload)
            apply_bundle(archive, hashlib.sha256(payload).hexdigest(), "1.4.0", root)
            self.assertEqual((root / "S3Drive.exe").read_bytes(), b"new:S3Drive.exe")
            self.assertEqual((root / "S3DriveUpdater.exe").read_bytes(), b"new:S3DriveUpdater.exe")
            self.assertIn("sealed", (root / "config.json").read_text())
            self.assertEqual((root / "cache" / "queued.bin").read_bytes(), b"unsent")

    def test_rollback_if_one_replacement_fails(self):
        payload = bundle_bytes()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "install"
            root.mkdir()
            for name in PAYLOAD_FILES:
                (root / name).write_bytes(b"old:" + name.encode())
            archive = Path(td) / "archive.zip"
            archive.write_bytes(payload)
            import update_helper
            actual_replace = update_helper.os.replace

            def fail_once(src, dst):
                if Path(dst).name == "S3DriveUpdater.exe" and not failed[0]:
                    failed[0] = True
                    raise OSError("locked file")
                return actual_replace(src, dst)

            failed = [False]
            with patch.object(update_helper.os, "replace", side_effect=fail_once):
                with self.assertRaises(OSError):
                    apply_bundle(archive, hashlib.sha256(payload).hexdigest(),
                                 "1.4.0", root)
            for name in PAYLOAD_FILES:
                self.assertEqual((root / name).read_bytes(),
                                 b"old:" + name.encode(), name)

    def test_launch_helper_does_not_replace_running_executable(self):
        payload = bundle_bytes()
        data = release(digest=hashlib.sha256(payload).hexdigest())
        data["assets"][0]["size"] = len(payload)
        update = parse_release(data, "1.2.2")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "program"
            root.mkdir()
            (root / "S3DriveUpdater.exe").write_bytes(b"updater")
            (root / "S3Drive.exe").write_bytes(b"running")
            staged = stage_update(
                update, opener=Mock(return_value=io.BytesIO(payload)),
                parent=Path(td),
            )
            with patch("auto_update.subprocess.Popen") as spawn:
                launch_updater(staged, root, parent_pid=12345, popen=spawn)
                args = spawn.call_args.args[0]
                self.assertEqual(Path(args[0]).read_bytes(), b"updater")
                self.assertIn("--parent-pid", args)
                self.assertEqual((root / "S3Drive.exe").read_bytes(), b"running")


class PackagingTests(unittest.TestCase):
    def test_distribution_has_standalone_updater(self):
        workflow = (Path(__file__).resolve().parents[1] /
                    ".github" / "workflows" / "build-windows.yml").read_text("utf-8")
        self.assertIn("--onefile --windowed --name S3DriveUpdater", workflow)
        self.assertIn('"S3DriveUpdater.exe"', workflow)
        self.assertIn("dist/S3DriveUpdater.exe", workflow)


if __name__ == "__main__":
    unittest.main()
