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

    def test_admin_and_updater_have_distinct_windows_version_resources(self):
        for executable in ("S3DriveAdmin", "S3DriveUpdater"):
            with self.subTest(executable=executable):
                info = windows_version_info("1.5.4", executable=executable)
                self.assertIn(f"OriginalFilename', '{executable}.exe'", info)
                self.assertIn(f"InternalName', '{executable}'", info)
                self.assertIn("CompanyName', 'medneem'", info)
                self.assertIn("ProductVersion', '1.5.4'", info)

    def test_ci_pins_known_good_windows_packaging_dependencies(self):
        root = Path(__file__).resolve().parents[1]
        build = (root / "requirements-build.txt").read_text("utf-8")
        requirements = (root / "requirements.txt").read_text("utf-8")
        self.assertIn("pyinstaller==6.22.3", build.lower())
        self.assertIn("pyinstaller-hooks-contrib==2026.9", build.lower())
        self.assertIn("pillow==12.3.0", build.lower())
        self.assertIn("pyside6==6.12.0", requirements.lower())
        self.assertIn("pywin32==312", requirements.lower())

    def test_ci_embeds_and_reports_all_three_exe_versions_and_hashes(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github" /
                    "workflows" / "build-windows.yml").read_text("utf-8")
        self.assertIn("python -m nuitka", workflow)
        self.assertIn("--onefile", workflow)
        self.assertIn("--output-filename=S3DriveAdmin.exe", workflow)
        self.assertIn("S3DriveAdmin.exe --self-test", workflow)
        self.assertNotIn("--name S3DriveAdmin --version-file", workflow)
        self.assertIn("--name S3DriveUpdater --version-file windows_updater_version_info.txt", workflow)
        self.assertIn("Get-FileHash", workflow)
        self.assertIn("GITHUB_STEP_SUMMARY", workflow)

    def test_elevated_helper_compilation_does_not_require_python_on_target(self):
        from service_admin import portable_config_probe
        from unittest.mock import patch
        from types import SimpleNamespace
        import profiles
        with tempfile.TemporaryDirectory() as directory:
            fake = Path(directory) / "S3DriveAdmin.exe"
            with patch.object(profiles.sys, "frozen", False, create=True):
                with patch.object(profiles, "__compiled__",
                                  SimpleNamespace(containing_dir=directory),
                                  create=True):
                    self.assertEqual(profiles.config_path(),
                                     (Path(directory) / "config.json").resolve())
                    self.assertTrue(portable_config_probe(fake))
                with patch.object(profiles, "__compiled__",
                                  SimpleNamespace(containing_dir="/other"),
                                  create=True):
                    self.assertFalse(portable_config_probe(fake))

    def test_ci_builds_only_onefile_windows_archive(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github" /
                    "workflows" / "build-windows.yml").read_text(encoding="utf-8")
        self.assertIn("--onefile --windowed --name S3Drive", workflow)
        self.assertIn("name: S3Drive-windows-x64-${{ github.run_id }}", workflow)
        self.assertIn("path: S3Drive-v*-windows-x64.zip", workflow)
        self.assertIn('gh release create "$tag" "$zip"', workflow)
        self.assertNotIn("--onedir", workflow)
        self.assertNotIn("antivirus diagnostic archive", workflow.lower())
        self.assertNotIn("windows-x64-onedir", workflow)

    def test_stamping_release_updates_app_and_resource_versions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            version = stamp_version("1.0.0", "50", root, "1.0.0")
            self.assertEqual(version, "1.0.0")
            self.assertIn('APP_VERSION = "1.0.0"', (root / "version.py").read_text(encoding="utf-8"))
            self.assertIn("filevers=(1, 0, 0, 0)", (root / "windows_version_info.txt").read_text(encoding="utf-8"))
            self.assertIn("S3DriveAdmin.exe", (root / "windows_admin_version_info.txt").read_text(encoding="utf-8"))
            self.assertIn("S3DriveUpdater.exe", (root / "windows_updater_version_info.txt").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
