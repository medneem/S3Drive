"""Regression: network mounts must display profile labels, not opaque IDs.

The network-drive mode also prevents MSI temp files in the S3 account root.
"""
import unittest
from pathlib import Path

from mounts import mount_command
from profiles import Profile
from windows_service import service_command
from network_drive import network_volume_name, native_network_volume_prefix


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
    FIRST = "4ef5373c-fc38-4862-afb7-3fc07022af8e"
    SECOND = "640ee20c-560e-4b59-9890-785f5c9d920f"

    def test_explorer_share_has_complete_unicode_profile_name(self):
        prefix = native_network_volume_prefix(self.FIRST, "Личный сетевой диск")
        self.assertEqual(prefix.rsplit("\\", 1)[1], "Личный сетевой диск")
        self.assertTrue(prefix.startswith("\\s3d"))
        self.assertEqual(prefix, native_network_volume_prefix(self.FIRST, "Личный сетевой диск"))

    def test_same_display_names_do_not_collide_between_profiles(self):
        a = native_network_volume_prefix(self.FIRST, "QNAP")
        b = native_network_volume_prefix(self.SECOND, "QNAP")
        self.assertNotEqual(a, b)
        self.assertEqual(a.rsplit("\\", 1)[1], "QNAP")
        self.assertEqual(b.rsplit("\\", 1)[1], "QNAP")

    def test_short_rclone_fallback_never_splits_utf8(self):
        label = network_volume_name(self.FIRST, "Личный сетевой диск")
        self.assertLessEqual(len(label.encode("utf-8")), 32)
        self.assertTrue(label.startswith("\\\\s3d"))
        self.assertIn("Лич", label)

    def test_invalid_unc_characters_are_replaced_only_for_mount_name(self):
        self.assertEqual(
            native_network_volume_prefix(self.FIRST, "Backup / NAS").rsplit("\\", 1)[1],
            "Backup _ NAS",
        )

    def test_user_mounts_use_full_native_network_label(self):
        p = profile(self.FIRST)
        args = mount_command(p, "rclone.exe", Path("cache"), 10001)
        self.assertIn("--network-mode", args)
        self.assertEqual(args[args.index("--volname") + 1], network_volume_name(p.id, p.name))
        self.assertEqual(
            args[args.index("--fuse-flag") + 1],
            "--VolumePrefix=" + native_network_volume_prefix(p.id, p.name),
        )
        self.assertIn("volname=" + p.name, args)
        self.assertIn("--s3-directory-markers", args)

    def test_windows_service_uses_full_native_network_label(self):
        p = profile(self.SECOND)
        cmd = service_command(
            p, Path("C:/rclone.exe"), Path("C:/rclone.conf"),
            Path("C:/cache"), Path("C:/rclone.log"),
            Path("C:/rc.htpasswd"), 10002,
        )
        self.assertIn("--network-mode", cmd)
        self.assertIn("--volname " + network_volume_name(p.id, p.name), cmd)
        self.assertIn(
            '--fuse-flag "--VolumePrefix=' + native_network_volume_prefix(p.id, p.name) + '"',
            cmd,
        )
        self.assertIn('volname=' + p.name, cmd)
        self.assertIn("--s3-directory-markers", cmd)


if __name__ == "__main__":
    unittest.main()
