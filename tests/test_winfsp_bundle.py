"""Regression checks for the SHA-256-pinned WinFsp Windows dependency."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = "winfsp-2.2.26286.msi"
SHA256 = "293FFB9BCC0B625C85079FE85FA77F8FE98170ABA51A02AC9C83D58F6C3B0E2F"


class WinFspBundleTests(unittest.TestCase):
    def test_upstream_beta5_msi_is_hash_pinned(self):
        script = (ROOT / "scripts" / "download_dependencies.ps1").read_text(encoding="utf-8")
        self.assertIn('$winfspTag = "v2.2B5"', script)
        self.assertIn(f'$winfspInstallerName = "{INSTALLER}"', script)
        self.assertIn(f'$winfspExpectedSha256 = "{SHA256}"', script)
        self.assertIn('Download-And-Verify `', script)

    def test_installer_name_matches_app_windows_build_and_e2e(self):
        files = (
            "dependencies.py",
            ".github/workflows/build-windows.yml",
            ".github/workflows/s3-deletion-e2e.yml",
        )
        for filename in files:
            with self.subTest(filename=filename):
                source = (ROOT / filename).read_text(encoding="utf-8")
                self.assertIn(INSTALLER, source)
                self.assertNotIn("winfsp-2.2.26215.msi", source)

    def test_setup_docs_name_current_driver_without_claiming_f5_fix(self):
        for filename in ("README.md", "README.ru.md",
                         "README_SETUP.txt", "README_SETUP_RU.txt",
                         "THIRD_PARTY_NOTICES.md"):
            with self.subTest(filename=filename):
                source = (ROOT / filename).read_text(encoding="utf-8")
                self.assertNotIn("2026 Beta4", source)
                self.assertNotIn("winfsp-2.2.26215.msi", source)
        self.assertIn(
            "WinFsp 2026 Beta5",
            (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
