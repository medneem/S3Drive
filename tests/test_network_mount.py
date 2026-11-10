"""Regression: network mounts must display profile labels, not opaque IDs.

The network-drive mode also prevents MSI temp files in the S3 account root.
"""
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch
import sys


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

    def test_registry_override_replaces_unc_suffix_with_profile_name(self):
        from drive_labels import network_label_registry_path
        self.assertEqual(
            network_label_registry_path(self.FIRST, "Личный сетевой диск"),
            (
                "Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\MountPoints2"
                "\\##s3d4ef5373cfc38#Личный сетевой диск"
            ),
        )
        self.assertEqual(
            network_label_registry_path(self.SECOND, "Яндекс"),
            (
                "Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\MountPoints2"
                "\\##s3d640ee20c560e#Яндекс"
            ),
        )

    def test_current_user_network_label_is_applied_only_to_own_unc(self):
        from drive_labels import install_explorer_network_label, network_label_registry_path
        winreg = MagicMock(HKEY_CURRENT_USER=100, KEY_READ=1, KEY_WRITE=2, REG_SZ=1)
        winreg.QueryValueEx.side_effect = FileNotFoundError
        with patch.dict(sys.modules, {"winreg": winreg}):
            with patch("drive_labels.os.name", "nt"):
                with patch("drive_labels.notify_explorer") as notify:
                    changed = install_explorer_network_label(self.FIRST, "Личный сетевой диск")
        self.assertTrue(changed)
        self.assertEqual(
            winreg.CreateKeyEx.call_args.args[1],
            network_label_registry_path(self.FIRST, "Личный сетевой диск"),
        )
        self.assertEqual(winreg.CreateKeyEx.call_args.args[0], winreg.HKEY_CURRENT_USER)
        self.assertEqual(
            winreg.SetValueEx.call_args.args[1:],
            ("_LabelFromReg", 0, winreg.REG_SZ, "Личный сетевой диск"),
        )
        notify.assert_called_once()

    def test_existing_matching_label_is_untouched(self):
        from drive_labels import install_explorer_network_label
        winreg = MagicMock(HKEY_CURRENT_USER=100, KEY_READ=1, KEY_WRITE=2, REG_SZ=1)
        winreg.QueryValueEx.return_value = ("Яндекс", winreg.REG_SZ)
        with patch.dict(sys.modules, {"winreg": winreg}):
            with patch("drive_labels.os.name", "nt"):
                with patch("drive_labels.notify_explorer") as notify:
                    changed = install_explorer_network_label(self.SECOND, "Яндекс")
        self.assertFalse(changed)
        winreg.SetValueEx.assert_not_called()
        notify.assert_not_called()

    def test_gui_repairs_labels_for_running_service_without_remount(self):
        from main import MainWindow
        p = profile(self.FIRST)
        p.service_mode = True
        gui = SimpleNamespace(
            profiles=[p], mounts=Mock(), _applied_network_labels={},
            statusBar=Mock(),
        )
        with patch("main.os.name", "nt"):
            with patch("main.service_status", return_value=4):
                with patch("main.os.path.isdir", return_value=True):
                    with patch("main.install_explorer_network_label") as install:
                        MainWindow.refresh_mounted_drive_labels(gui)
                        MainWindow.refresh_mounted_drive_labels(gui)
        install.assert_called_once_with(p.id, p.name)

    def test_gui_does_not_label_disconnected_or_foreign_network_drives(self):
        from main import MainWindow
        p = profile(self.FIRST)
        p.service_mode = False
        gui = SimpleNamespace(
            profiles=[p], mounts=Mock(), _applied_network_labels={},
            statusBar=Mock(),
        )
        gui.mounts.is_mounted.return_value = False
        with patch("main.os.name", "nt"):
            with patch("main.service_status", return_value=None):
                with patch("main.install_explorer_network_label") as install:
                    MainWindow.refresh_mounted_drive_labels(gui)
        install.assert_not_called()

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
