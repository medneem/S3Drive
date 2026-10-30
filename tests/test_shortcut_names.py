"""Regression tests for named drive shortcuts, including safe migrations."""
from pathlib import Path
import tempfile
import unittest

from drive_actions import create_desktop_shortcut


class FakeShortcut:
    def __init__(self, shell, path):
        self.shell = shell
        self.path = Path(path)
        original = shell.saved.get(self.path, {})
        self.TargetPath = original.get("target", "")
        self.Description = original.get("description", "")

    def Save(self):
        self.path.write_text("shortcut fixture", encoding="utf-8")
        self.shell.saved[self.path] = {"target": self.TargetPath, "description": self.Description}


class FakeShell:
    def __init__(self, desktop):
        self.desktop = Path(desktop)
        self.saved = {}

    def SpecialFolders(self, key):
        assert key == "Desktop"
        return str(self.desktop)

    def CreateShortcut(self, path):
        return FakeShortcut(self, path)

    def add(self, name, target, description):
        path = self.desktop / name
        shortcut = self.CreateShortcut(path)
        shortcut.TargetPath = target
        shortcut.Description = description
        shortcut.Save()
        return path


class ShortcutNameTests(unittest.TestCase):
    def test_uses_russian_profile_name_and_drive_letter(self):
        with tempfile.TemporaryDirectory() as td:
            shell = FakeShell(td)
            dest, created = create_desktop_shortcut("S:", "Личный сетевой диск", shell=shell)
            self.assertTrue(created)
            self.assertEqual(dest.name, "Личный сетевой диск (S).lnk")
            self.assertEqual(shell.saved[dest]["target"], "S:\\")

    def test_sanitizes_invalid_windows_filename_characters(self):
        with tempfile.TemporaryDirectory() as td:
            dest, created = create_desktop_shortcut("T:", 'QNAP / archive:*?<>"\\|. ', shell=FakeShell(td))
            self.assertTrue(created)
            self.assertEqual(dest.name, "QNAP _ archive________ (T).lnk")

    def test_migrates_legacy_shortcut_if_it_belongs_to_s3drive(self):
        with tempfile.TemporaryDirectory() as td:
            shell = FakeShell(td)
            legacy = shell.add("S3Drive (S).lnk", "S:\\", "Диск S3Drive S:")
            dest, created = create_desktop_shortcut("S:", "QNAP", shell=shell)
            self.assertTrue(created)
            self.assertTrue(dest.exists())
            self.assertFalse(legacy.exists())

    def test_preserves_unrelated_legacy_named_shortcut(self):
        with tempfile.TemporaryDirectory() as td:
            shell = FakeShell(td)
            legacy = shell.add("S3Drive (S).lnk", "C:\\", "Не наш ярлык")
            dest, created = create_desktop_shortcut("S:", "QNAP", shell=shell)
            self.assertTrue(created)
            self.assertTrue(legacy.exists())
            self.assertTrue(dest.exists())

    def test_renames_managed_shortcut_when_profile_name_changes(self):
        with tempfile.TemporaryDirectory() as td:
            shell = FakeShell(td)
            old, _ = create_desktop_shortcut("S:", "Старое имя", shell=shell)
            new, _ = create_desktop_shortcut("S:", "Новое имя", shell=shell)
            self.assertFalse(old.exists())
            self.assertTrue(new.exists())
            self.assertIn("Новое имя", shell.saved[new]["description"])

    def test_does_not_overwrite_a_foreign_shortcut_with_same_name(self):
        with tempfile.TemporaryDirectory() as td:
            shell = FakeShell(td)
            foreign = shell.add("QNAP (S).lnk", "C:\\", "My favorite folder")
            with self.assertRaises(FileExistsError):
                create_desktop_shortcut("S:", "QNAP", shell=shell)
            self.assertTrue(foreign.exists())
            self.assertEqual(shell.saved[foreign]["target"], "C:\\")

    def test_keeps_already_correct_shortcut(self):
        with tempfile.TemporaryDirectory() as td:
            shell = FakeShell(td)
            first, created = create_desktop_shortcut("S:", "QNAP", shell=shell)
            second, created_again = create_desktop_shortcut("S:", "QNAP", shell=shell)
            self.assertTrue(created)
            self.assertFalse(created_again)
            self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
