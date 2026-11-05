"""Regression: Windows must treat S3Drive as a remote drive, not a huge local disk.

Windows Installer picks writable local drives by free space and may create
MSI*.tmp directories in the account root (which become S3 buckets).
"""
import unittest
from pathlib import Path

from mounts import mount_command
from profiles import Profile
from windows_service import service_command
from network_drive import network_volume_name


def profile(ident: str, name: str = "Личный сетевой диск") -> Profile:
    return Profile(
        id=ident,
        name=name,
        endpoint="http://nas.local:8010",
        bucket="",
        access_key="test",
        secret_key="test",
        drive="S:",
    )


class NetworkMountTests(unittest.TestCase):
    def test_unique_stable_unc_shares_for_multiple_profiles(self):
        a = network_volume_name("4ef5373c-fc38-4862-afb7-3fc07022af8e")
        b = network_volume_name("640ee20c-560e-4b59-9890-785f5c9d920f")
        self.assertNotEqual(a, b)
        self.assertTrue(a.startswith("\\\\S3Drive\\"))
        self.assertEqual(a, network_volume_name("4ef5373c-fc38-4862-afb7-3fc07022af8e"))
        # rclone v1.75.2 truncates a --volname longer than 32 bytes on Windows.
        self.assertLessEqual(len(a.encode("utf-8")), 32)

    def test_user_mounts_are_remote_network_drives(self):
        p = profile("4ef5373c-fc38-4862-afb7-3fc07022af8e")
        args = mount_command(p, "rclone.exe", Path("cache"), 10001)
        self.assertIn("--network-mode", args)
        self.assertEqual(args[args.index("--volname") + 1], network_volume_name(p.id))
        self.assertIn("volname=" + p.name, args)
        self.assertIn("--s3-directory-markers", args)

    def test_windows_service_mounts_are_remote_network_drives(self):
        p = profile("640ee20c-560e-4b59-9890-785f5c9d920f")
        cmd = service_command(
            p, Path("C:/rclone.exe"), Path("C:/rclone.conf"),
            Path("C:/cache"), Path("C:/rclone.log"),
            Path("C:/rc.htpasswd"), 10002,
        )
        self.assertIn("--network-mode", cmd)
        self.assertIn("--volname " + network_volume_name(p.id), cmd)
        self.assertIn('volname=' + p.name, cmd)
        self.assertIn("--s3-directory-markers", cmd)


if __name__ == "__main__":
    unittest.main()
