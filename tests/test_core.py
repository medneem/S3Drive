"""Boundary tests for profile validation, secret persistence and rclone invocation."""
import json
import tempfile
import unittest
from pathlib import Path

from mounts import mount_command, remote_environment
from profiles import ProfileStore, new_profile


def sample(**changes):
    data = dict(name="QNAP NAS", endpoint="http://srv-nas-01:8010", bucket="SRV-GNU-GSP-DB",
                access_key="PRIVATE_ACCESS_123", secret_key="PRIVATE_SECRET_123",
                drive="S:", path_style=True, region="us-east-1")
    data.update(changes)
    return new_profile(**data)


class ProfileTests(unittest.TestCase):
    def test_qnap_http_endpoint_with_path_style_is_valid(self):
        self.assertEqual(sample().endpoint, "http://srv-nas-01:8010")

    def test_invalid_endpoint_cannot_embed_credentials(self):
        with self.assertRaises(ValueError):
            sample(endpoint="http://user:password@srv-nas-01:8010")

    def test_bucket_cannot_escape_rclone_remote_path(self):
        with self.assertRaises(ValueError):
            sample(bucket="bucket/../../secret")

    def test_invalid_drive_is_rejected(self):
        with self.assertRaises(ValueError):
            sample(drive="S:\\Windows")


class MountTests(unittest.TestCase):
    def test_rclone_arguments_do_not_expose_s3_keys(self):
        p = sample()
        args = mount_command(p, "rclone.exe", Path("cache"), 12543)
        self.assertEqual(args[1:4], ["mount", "s3drive_mount:SRV-GNU-GSP-DB", "S:"])
        self.assertIn("--vfs-cache-mode", args)
        self.assertNotIn(p.secret_key, " ".join(args))
        self.assertNotIn(p.access_key, " ".join(args))

    def test_rclone_env_config_for_s3_qnap(self):
        env = remote_environment(sample())
        self.assertEqual(env["RCLONE_CONFIG_S3DRIVE_MOUNT_FORCE_PATH_STYLE"], "true")
        self.assertEqual(env["RCLONE_CONFIG_S3DRIVE_MOUNT_ENDPOINT"], "http://srv-nas-01:8010")
        self.assertEqual(env["RCLONE_CONFIG_S3DRIVE_MOUNT_TYPE"], "s3")

    def test_config_is_encrypted_not_plaintext(self):
        p = sample()
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "profiles.json"
            store = ProfileStore(path, encrypt=lambda x: "protected:" + x[::-1],
                                 decrypt=lambda x: x.removeprefix("protected:")[::-1])
            store.save([p])
            raw = path.read_text(encoding="utf-8")
            self.assertNotIn(p.secret_key, raw)
            self.assertNotIn(p.access_key, raw)
            self.assertEqual(json.loads(raw)[0]["name"], p.name)
            self.assertEqual(store.load(), [p])


if __name__ == "__main__":
    unittest.main()
