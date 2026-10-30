"""Boundary tests for profile validation, secret persistence and rclone invocation."""
import json
from dataclasses import asdict
import tempfile
import unittest
from unittest import mock
from pathlib import Path

import dependencies
import mounts
import profiles
from mounts import mount_command, remote_environment
from profiles import ProfileStore, new_profile


def sample(**changes):
    data = dict(name="QNAP NAS", endpoint="http://nas.local:8010", bucket="my-backup",
                access_key="PRIVATE_ACCESS_123", secret_key="PRIVATE_SECRET_123",
                drive="S:", path_style=True, region="us-east-1")
    data.update(changes)
    return new_profile(**data)


class DependencyTests(unittest.TestCase):
    def test_portable_winfsp_installer_is_resolved_beside_exe(self):
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td)
            installer = directory / dependencies.WINFSP_INSTALLER
            installer.write_bytes(b"msi-test-fixture")
            with mock.patch.object(dependencies.sys, "frozen", True, create=True):
                with mock.patch.object(dependencies.sys, "executable", str(directory / "S3Drive.exe")):
                    self.assertTrue(dependencies.winfsp_installer().samefile(installer))


class ProfileTests(unittest.TestCase):
    @staticmethod
    def make_store(path):
        return ProfileStore(
            path,
            encrypt=lambda value: "protected:" + value[::-1],
            decrypt=lambda value: value.removeprefix("protected:")[::-1],
        )

    def test_qnap_http_endpoint_with_path_style_is_valid(self):
        self.assertEqual(sample().endpoint, "http://nas.local:8010")

    def test_empty_bucket_mounts_s3_account_root(self):
        profile = sample(bucket="")
        profile.validate()
        self.assertEqual(
            mount_command(profile, "rclone.exe", Path("cache"), 12543)[2],
            "s3drive_mount:",
        )

    def test_blank_bucket_with_spaces_is_not_accepted(self):
        with self.assertRaises(ValueError):
            sample(bucket="   ")

    def test_specific_bucket_is_still_supported(self):
        profile = sample(bucket="my-backup")
        self.assertEqual(
            mount_command(profile, "rclone.exe", Path("cache"), 12543)[2],
            "s3drive_mount:my-backup",
        )

    def test_root_profile_survives_config_round_trip(self):
        with tempfile.TemporaryDirectory() as td:
            store = ProfileStore(
                Path(td) / "config.json",
                encrypt=lambda value: "protected:" + value[::-1],
                decrypt=lambda value: value.removeprefix("protected:")[::-1],
            )
            profile = sample(bucket="")
            store.save([profile])
            self.assertEqual(store.load()[0].bucket, "")

    def test_invalid_name_control_characters_rejected_before_service_install(self):
        with self.assertRaises(ValueError):
            sample(name="Disk\nother")

    def test_long_name_is_rejected_before_windows_mount(self):
        with self.assertRaises(ValueError):
            sample(name="N" * 129)

    def test_invalid_endpoint_cannot_embed_credentials(self):
        with self.assertRaises(ValueError):
            sample(endpoint="http://user:password@nas.local:8010")

    def test_bucket_cannot_escape_rclone_remote_path(self):
        with self.assertRaises(ValueError):
            sample(bucket="bucket/../../secret")

    def test_invalid_profile_identifier_cannot_escape_cache_or_logs(self):
        from profiles import Profile
        invalid = ("../outside", "../../secret", "profile-1", "", "abc\\other", 42,
                   "urn:uuid:11111111-2222-4333-8444-555555555555",
                   "{11111111-2222-4333-8444-555555555555}")
        for bad_id in invalid:
            with self.subTest(id=bad_id):
                values = vars(sample()).copy()
                values["id"] = bad_id
                with self.assertRaisesRegex(ValueError, "идентификатор"):
                    Profile(**values).validate()

    def test_duplicate_profile_ids_cannot_be_loaded(self):
        p = sample()
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config.json"
            store = self.make_store(path)
            doc = []
            record = asdict(p)
            record["access_key"] = "protected:" + record["access_key"][::-1]
            record["secret_key"] = "protected:" + record["secret_key"][::-1]
            doc.append(record)
            doc.append({**record, "drive": "T:"})
            path.write_text(json.dumps({"profiles": doc}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "дубликат|Дубликат"):
                store.load()
            self.assertTrue(path.is_file())

    def test_duplicate_ids_cannot_be_saved(self):
        p = sample()
        from dataclasses import replace
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config.json"
            store = self.make_store(path)
            store.save([p])
            before = path.read_bytes()
            with self.assertRaisesRegex(ValueError, "Дубликат"):
                store.save([p, replace(p, drive="T:")])
            self.assertEqual(path.read_bytes(), before)

    def test_invalid_drive_is_rejected(self):
        with self.assertRaises(ValueError):
            sample(drive="S:\\Windows")


class MountTests(unittest.TestCase):
    def test_rclone_arguments_do_not_expose_s3_keys(self):
        p = sample()
        args = mount_command(p, "rclone.exe", Path("cache"), 12543)
        self.assertEqual(args[1:4], ["mount", "s3drive_mount:my-backup", "S:"])
        self.assertIn("--vfs-cache-mode", args)
        self.assertEqual(args.count("--dir-cache-time"), 1)
        self.assertEqual(args[args.index("--dir-cache-time") + 1], "0s")
        self.assertNotIn(p.secret_key, " ".join(args))
        self.assertNotIn(p.access_key, " ".join(args))

    def test_fresh_directory_listing_applies_to_account_root(self):
        p = sample(bucket="")
        args = mount_command(p, "rclone.exe", Path("cache"), 12543)
        self.assertEqual(args[2], "s3drive_mount:")
        self.assertEqual(args[args.index("--dir-cache-time") + 1], "0s")

    def test_volume_label_uses_exact_profile_name(self):
        profile = sample(name="Личный сетевой диск")
        args = mount_command(profile, "rclone.exe", Path("cache"), 12543)
        self.assertIn("--option", args)
        label_index = args.index("--option")
        self.assertEqual(args[label_index + 1], "volname=Личный сетевой диск")
        self.assertNotIn("--volname", args)
        self.assertNotIn("S3Drive - ", args[label_index + 1])
        self.assertGreater(len(args[label_index + 1].encode("utf-8")), 32)

    def test_rclone_env_config_for_s3_qnap(self):
        env = remote_environment(sample())
        self.assertEqual(env["RCLONE_CONFIG_S3DRIVE_MOUNT_FORCE_PATH_STYLE"], "true")
        self.assertEqual(env["RCLONE_CONFIG_S3DRIVE_MOUNT_ENDPOINT"], "http://nas.local:8010")
        self.assertEqual(env["RCLONE_CONFIG_S3DRIVE_MOUNT_TYPE"], "s3")

    def test_portable_rclone_is_found_beside_built_executable(self):
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td)
            rclone = directory / "rclone.exe"
            rclone.write_bytes(b"rclone-placeholder")
            with mock.patch.object(mounts.sys, "frozen", True, create=True):
                with mock.patch.object(mounts.sys, "executable", str(directory / "S3Drive.exe")):
                    self.assertTrue(Path(mounts.find_rclone()).samefile(rclone))

    @staticmethod
    def make_store(path, legacy_path=None):
        return ProfileStore(
            path, encrypt=lambda x: "protected:" + x[::-1],
            decrypt=lambda x: x.removeprefix("protected:")[::-1],
            legacy_path=legacy_path,
        )

    def test_config_is_encrypted_not_plaintext(self):
        p = sample(name="Мой QNAP")
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config.json"
            store = self.make_store(path)
            store.save([p])
            raw = path.read_text(encoding="utf-8")
            self.assertNotIn(p.secret_key, raw)
            self.assertNotIn(p.access_key, raw)
            self.assertEqual(json.loads(raw)["profiles"][0]["name"], "Мой QNAP")
            self.assertEqual(store.load(), [p])

    def test_first_launch_creates_empty_portable_config(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config.json"
            store = self.make_store(path)
            self.assertEqual(store.load(), [])
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"profiles": []})

    def test_config_file_is_beside_portable_executable(self):
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td)
            with mock.patch.object(profiles.sys, "frozen", True, create=True):
                with mock.patch.object(profiles.sys, "executable", str(directory / "S3Drive.exe")):
                    self.assertEqual(profiles.config_path().parent, directory.resolve())
                    self.assertEqual(profiles.config_path().name, "config.json")

    def test_migrates_legacy_profiles_without_deleting_source(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            record = asdict(sample(name="Мигрированный S3"))
            record["access_key"] = "protected:" + record["access_key"][::-1]
            record["secret_key"] = "protected:" + record["secret_key"][::-1]
            (root / "profiles.json").write_text(
                json.dumps([record], ensure_ascii=False), encoding="utf-8"
            )
            portable = self.make_store(root / "config.json", root / "profiles.json")
            self.assertEqual(portable.load()[0].name, "Мигрированный S3")
            self.assertTrue((root / "profiles.json").exists())
            self.assertEqual(json.loads((root / "config.json").read_text(encoding="utf-8"))["profiles"][0]["name"], "Мигрированный S3")

    def test_existing_config_has_priority_over_legacy(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            legacy = root / "profiles.json"
            legacy.write_text("invalid JSON", encoding="utf-8")
            portable = self.make_store(root / "config.json", legacy)
            portable.save([sample(name="Актуальный S3")])
            self.assertEqual(portable.load()[0].name, "Актуальный S3")

    def test_bad_config_is_not_silently_overwritten(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config.json"
            path.write_text('{"profiles": "corrupted"}', encoding="utf-8")
            with self.assertRaises(ValueError):
                self.make_store(path).load()
            self.assertEqual(path.read_text(encoding="utf-8"), '{"profiles": "corrupted"}')


if __name__ == "__main__":
    unittest.main()
