"""Versioning contract: CI builds are dev artifacts, releases use explicit SemVer."""

import tempfile
import unittest
from pathlib import Path

from scripts.stamp_version import make_version, stamp_version, windows_version_info


class VersioningTests(unittest.TestCase):
    def test_push_build_uses_development_suffix_not_release_number(self):
        self.assertEqual(make_version("1.0.0", "44"), "1.0.0-dev.44")

    def test_explicit_first_release_is_1_0_0(self):
        self.assertEqual(make_version("1.0.0", "44", "1.0.0"), "1.0.0")

    def test_future_patch_release_is_independent_of_pipeline_number(self):
        self.assertEqual(make_version("1.0.0", "999", "1.0.1"), "1.0.1")

    def test_rejects_invalid_or_older_release_numbers(self):
        for invalid in ("v1.0.0", "1.0", "1.0.0-rc1", "0.9.9", "1.0.0; echo bad"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                make_version("1.0.0", "44", invalid)

    def test_rejects_run_number_that_cannot_fit_windows_version(self):
        with self.assertRaises(ValueError):
            make_version("1.0.0", "65536")

    def test_windows_metadata_handles_development_build(self):
        metadata = windows_version_info("1.0.0-dev.44")
        self.assertIn("filevers=(1, 0, 0, 44)", metadata)
        self.assertIn("ProductVersion', '1.0.0-dev.44'", metadata)

    def test_windows_metadata_handles_stable_release(self):
        metadata = windows_version_info("1.0.0")
        self.assertIn("filevers=(1, 0, 0, 0)", metadata)
        self.assertIn("ProductVersion', '1.0.0'", metadata)

    def test_stamping_release_updates_app_and_resource_versions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            version = stamp_version("1.0.0", "50", root, "1.0.0")
            self.assertEqual(version, "1.0.0")
            self.assertIn('APP_VERSION = "1.0.0"', (root / "version.py").read_text(encoding="utf-8"))
            self.assertIn("filevers=(1, 0, 0, 0)", (root / "windows_version_info.txt").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
