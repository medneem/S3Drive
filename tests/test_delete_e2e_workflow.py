"""Ensure the standalone S3/WinFsp deletion reproducer stays enabled in CI."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class S3DeletionWorkflowTests(unittest.TestCase):
    def test_isolated_e2e_uses_real_mount_and_s3_api(self):
        workflow = (ROOT / ".github" / "workflows" / "s3-deletion-e2e.yml").read_text("utf-8")
        source = (ROOT / "tests" / "integration" / "test_s3_delete_windows.py").read_text("utf-8")
        self.assertIn("runs-on: windows-latest", workflow)
        self.assertIn("scripts/download_dependencies.ps1", workflow)
        self.assertIn("moto[server]", workflow)
        self.assertIn("msiexec.exe", workflow)
        self.assertIn("for ($pass = 1; $pass -le 3; $pass++)", workflow)
        self.assertIn("shutil.rmtree(target)", source)
        self.assertIn("shutil.rmtree(directory)", source)
        self.assertIn("self.remaining(", source)
        self.assertIn("with self.mounted(bucket=\"\")", source)
        self.assertIn("with self.mounted(bucket=self.bucket)", source)
        self.assertIn("reappeared after remount", source)
        self.assertIn("remote_environment(profile)", source)
        self.assertIn("mount_command(profile,", source)
        self.assertIn("127.0.0.1", source)

    def test_no_integration_dependencies_in_user_package(self):
        requirements = (ROOT / "requirements.txt").read_text("utf-8")
        self.assertNotIn("moto", requirements)
        self.assertNotIn("boto3", requirements)
        release = (ROOT / ".github" / "workflows" / "build-windows.yml").read_text("utf-8")
        self.assertNotIn("s3-deletion-e2e.yml", release)
        self.assertNotIn("test_s3_delete_windows", release)


if __name__ == "__main__":
    unittest.main()
