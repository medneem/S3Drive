"""Отдельный артефакт onedir для проверки срабатываний Kaspersky."""
import unittest
from pathlib import Path


WORKFLOW = (Path(__file__).resolve().parents[1] / ".github" /
            "workflows" / "build-windows.yml")


class AntivirusDiagnosticTests(unittest.TestCase):
    def test_onedir_is_isolated_from_release_and_regular_artifact(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("Build antivirus diagnostic onedir", source)
        self.assertIn("--onedir --noupx --windowed", source)
        self.assertIn("Upload antivirus diagnostic archive", source)
        self.assertIn("S3Drive-windows-x64-onedir-", source)
        self.assertIn("if: github.event_name != 'workflow_dispatch'", source)
        self.assertIn('Copy-Item "dist/S3Drive/*"', source)
        self.assertIn('Where-Object { $_.Name -ne "S3Drive.exe" }', source)
        self.assertIn('gh release create "$tag" "$zip"', source)

    def test_onedir_requires_runtime_and_preserves_existing_zip(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('Test-Path "dist/S3Drive/_internal"', source)
        self.assertIn('Test-Path "$folder/_internal"', source)
        self.assertIn('name: S3Drive-windows-x64-${{ github.run_id }}', source)
        self.assertIn('path: S3Drive-v*-windows-x64.zip', source)


if __name__ == "__main__":
    unittest.main()
