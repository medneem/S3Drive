"""Validated S3 connection profiles and Windows DPAPI-protected persistence."""
from __future__ import annotations

import base64
import json
import os
import re
import sys
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit


@dataclass
class Profile:
    id: str
    name: str
    endpoint: str
    bucket: str
    access_key: str
    secret_key: str
    drive: str = "S:"
    path_style: bool = True
    region: str = "us-east-1"
    create_shortcut: bool = False
    open_after_mount: bool = False
    keep_running: bool = False
    service_mode: bool = False

    def validate(self) -> None:
        # Profile ID also forms filesystem paths (cache, logs and service dir).
        # Fail before any path construction or disk I/O, including legacy JSON.
        if not isinstance(self.id, str):
            raise ValueError("Некорректный идентификатор подключения (UUID).")
        try:
            canonical_id = str(uuid.UUID(self.id))
        except (ValueError, AttributeError) as exc:
            raise ValueError("Некорректный идентификатор подключения (UUID).") from exc
        if self.id.lower() != canonical_id:
            raise ValueError("Некорректный идентификатор подключения (UUID).")
        if not self.name.strip():
            raise ValueError("Введите название подключения.")
        if len(self.name) > 128 or any(ord(ch) < 32 for ch in self.name):
            raise ValueError("Название не должно превышать 128 символов или содержать управляющие символы.")
        parsed = urlsplit(self.endpoint)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("Endpoint должен начинаться с http:// или https://.")
        if parsed.username or parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise ValueError("Endpoint должен содержать только схему, адрес и порт.")
        try:
            _ = parsed.port
        except ValueError as exc:
            raise ValueError("Некорректный порт Endpoint.") from exc
        # The first path component is always a real S3 bucket. Remaining
        # components are an optional object-key prefix inside that bucket.
        # Accept leading/trailing slashes copied from S3 browser paths.
        error = "Укажите корректное имя бакета (2–63 символа) или путь bucket/folder; поле можно оставить пустым."
        if not isinstance(self.bucket, str):
            raise ValueError(error)
        bucket_path = self.bucket.strip("/")
        components = bucket_path.split("/") if bucket_path else []
        invalid_prefix = any(
            part in ("", ".", "..")
            or any(ord(ch) < 32 or ch in '\\:*?"<>|' for ch in part)
            for part in components[1:]
        )
        if self.bucket and (
            not components
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,61}[A-Za-z0-9]", components[0])
            or invalid_prefix
        ):
            raise ValueError(error)
        if not re.fullmatch(r"[A-Z]:", self.drive):
            raise ValueError("Буква диска должна иметь вид S:.")
        if not self.access_key or not self.secret_key:
            raise ValueError("Укажите Access Key и Secret Key.")
        if not self.region or any(c.isspace() for c in self.region):
            raise ValueError("Некорректный регион S3.")
        if not isinstance(self.create_shortcut, bool) or not isinstance(self.open_after_mount, bool):
            raise ValueError("Параметры ярлыка и открытия диска должны быть true/false.")
        if type(self.keep_running) is not bool:
            raise ValueError("Фоновый режим должен быть true или false.")
        if type(self.service_mode) is not bool:
            raise ValueError("Режим службы должен быть true или false.")
        if self.service_mode and self.keep_running:
            raise ValueError("Выберите службу Windows или пользовательский фон, но не оба варианта.")


def normalize_profile_bucket_path(bucket: str) -> str:
    """Show and persist a trailing slash for nested S3 directories only.

    Keep the user's optional leading slash and leave account/bucket roots
    unchanged. The rclone command independently normalizes its source.
    """
    path = bucket.strip("/")
    if "/" in path and not bucket.endswith("/"):
        return bucket + "/"
    return bucket


def s3_mount_root(bucket: str) -> str:
    """Canonical rclone root. A nested S3 prefix MUST end with '/'.

    Without the trailing slash rclone may HEAD-test the prefix as a file.
    Restricted S3 keys can return 403 for that test and rclone then fails
    with "is a file not a directory", even though the folder exists.
    A bucket-only root and account root do not require the slash.
    """
    path = bucket.strip("/")
    return path + "/" if "/" in path else path


def config_path() -> Path:
    """Portable config beside S3Drive.exe; project folder in development."""
    if getattr(sys, "frozen", False):
        # PyInstaller onefile GUI and updater.
        root = Path(sys.executable).resolve().parent
    elif "__compiled__" in globals():
        # Nuitka onefile helper: __file__ points to the unpacked payload.
        # containing_dir is the actual S3DriveAdmin.exe installation folder.
        root = Path(__compiled__.containing_dir).resolve()
    else:
        root = Path(__file__).resolve().parent
    return root / "config.json"


def app_data_dir() -> Path:
    root = os.environ.get("LOCALAPPDATA")
    return (Path(root) if root else Path.home() / ".local" / "share") / "S3Drive"


def dpapi_encrypt(value: str) -> str:
    import win32crypt
    blob = win32crypt.CryptProtectData(value.encode("utf-8"), "S3Drive credential", None, None, None, 0)
    return "dpapi:" + base64.b64encode(blob).decode("ascii")


def dpapi_decrypt(value: str) -> str:
    import win32crypt
    if not value.startswith("dpapi:"):
        raise ValueError("Неподдерживаемый формат защищённого ключа.")
    blob = base64.b64decode(value[6:], validate=True)
    return win32crypt.CryptUnprotectData(blob, None, None, None, 0)[1].decode("utf-8")


class ProfileStore:
    def __init__(
        self, path: Path | None = None, encrypt=None, decrypt=None,
        legacy_path: Path | None = None,
    ):
        self.path = path if path is not None else config_path()
        self.language = "en"
        self.check_updates_on_startup = True
        self.encrypt = encrypt or dpapi_encrypt
        self.decrypt = decrypt or dpapi_decrypt
        self.legacy_path = (legacy_path if legacy_path is not None else
                            app_data_dir() / "profiles.json" if path is None else None)

    def _deserialize(self, records: list) -> list[Profile]:
        if not isinstance(records, list):
            raise ValueError("Профили должны быть массивом JSON.")
        profiles = []
        seen_ids: set[str] = set()
        for record in records:
            if not isinstance(record, dict):
                raise ValueError("Каждый профиль должен быть JSON-объектом.")
            values = dict(record)
            values["access_key"] = self.decrypt(values["access_key"])
            values["secret_key"] = self.decrypt(values["secret_key"])
            profile = Profile(**values)
            profile.validate()
            normalized_id = str(uuid.UUID(profile.id))
            if normalized_id in seen_ids:
                raise ValueError("Дубликат идентификатора подключения в config.json.")
            seen_ids.add(normalized_id)
            profiles.append(profile)
        return profiles

    def load(self) -> list[Profile]:
        # Portable config.json is the ONLY source of profiles. Discard the
        # obsolete local-app-data profile store without reading/decrypting it.
        # Never delete config.json itself if both paths refer to the same file.
        if (self.legacy_path is not None
                and self.legacy_path.resolve() != self.path.resolve()):
            self.legacy_path.unlink(missing_ok=True)
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict) or not isinstance(data.get("profiles"), list):
                raise ValueError("config.json должен содержать объект с массивом profiles.")
            language = data.get("language", "en")
            if language not in ("en", "ru"):
                raise ValueError("Unsupported interface language in config.json.")
            check_updates = data.get("check_updates_on_startup", True)
            if type(check_updates) is not bool:
                raise ValueError("check_updates_on_startup must be true or false.")
            profiles = self._deserialize(data["profiles"])
            self.language = language
            self.check_updates_on_startup = check_updates
            return profiles

        # No automatic migration: a new portable folder starts empty.
        self.save([])
        return []

    def save(self, profiles: list[Profile]) -> None:
        if self.language not in ("en", "ru"):
            raise ValueError("Unsupported interface language.")
        if type(self.check_updates_on_startup) is not bool:
            raise ValueError("check_updates_on_startup must be true or false.")
        records = []
        seen_ids: set[str] = set()
        for profile in profiles:
            profile.validate()
            normalized_id = str(uuid.UUID(profile.id))
            if normalized_id in seen_ids:
                raise ValueError("Дубликат идентификатора подключения в config.json.")
            seen_ids.add(normalized_id)
            record = asdict(profile)
            record["access_key"] = self.encrypt(profile.access_key)
            record["secret_key"] = self.encrypt(profile.secret_key)
            records.append(record)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_name(self.path.name + ".tmp")
        try:
            temp.write_text(json.dumps({"language": self.language, "check_updates_on_startup": self.check_updates_on_startup, "profiles": records}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temp.replace(self.path)
        finally:
            temp.unlink(missing_ok=True)


def new_profile(**values) -> Profile:
    profile = Profile(id=str(uuid.uuid4()), **values)
    profile.validate()
    return profile
