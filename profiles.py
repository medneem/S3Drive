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

    def validate(self) -> None:
        if not self.name.strip():
            raise ValueError("Введите название подключения.")
        parsed = urlsplit(self.endpoint)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("Endpoint должен начинаться с http:// или https://.")
        if parsed.username or parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise ValueError("Endpoint должен содержать только схему, адрес и порт.")
        try:
            _ = parsed.port
        except ValueError as exc:
            raise ValueError("Некорректный порт Endpoint.") from exc
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,61}[A-Za-z0-9]", self.bucket):
            raise ValueError("Укажите корректное имя S3-бакета (2–63 символа).")
        if not re.fullmatch(r"[A-Z]:", self.drive):
            raise ValueError("Буква диска должна иметь вид S:.")
        if not self.access_key or not self.secret_key:
            raise ValueError("Укажите Access Key и Secret Key.")
        if not self.region or any(c.isspace() for c in self.region):
            raise ValueError("Некорректный регион S3.")


def config_path() -> Path:
    """Portable config beside S3Drive.exe; project folder in development."""
    root = (Path(sys.executable).resolve().parent if getattr(sys, "frozen", False)
            else Path(__file__).resolve().parent)
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
        self.encrypt = encrypt or dpapi_encrypt
        self.decrypt = decrypt or dpapi_decrypt
        self.legacy_path = (legacy_path if legacy_path is not None else
                            app_data_dir() / "profiles.json" if path is None else None)

    def _deserialize(self, records: list) -> list[Profile]:
        if not isinstance(records, list):
            raise ValueError("Профили должны быть массивом JSON.")
        profiles = []
        for record in records:
            if not isinstance(record, dict):
                raise ValueError("Каждый профиль должен быть JSON-объектом.")
            values = dict(record)
            values["access_key"] = self.decrypt(values["access_key"])
            values["secret_key"] = self.decrypt(values["secret_key"])
            profile = Profile(**values)
            profile.validate()
            profiles.append(profile)
        return profiles

    def load(self) -> list[Profile]:
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict) or not isinstance(data.get("profiles"), list):
                raise ValueError("config.json должен содержать объект с массивом profiles.")
            return self._deserialize(data["profiles"])

        # Upgrade from earlier releases; never delete the old file.
        if self.legacy_path is not None and self.legacy_path.is_file():
            data = json.loads(self.legacy_path.read_text(encoding="utf-8-sig"))
            profiles = self._deserialize(data)
            self.save(profiles)
            return profiles

        self.save([])
        return []

    def save(self, profiles: list[Profile]) -> None:
        records = []
        for profile in profiles:
            profile.validate()
            record = asdict(profile)
            record["access_key"] = self.encrypt(profile.access_key)
            record["secret_key"] = self.encrypt(profile.secret_key)
            records.append(record)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_name(self.path.name + ".tmp")
        try:
            temp.write_text(json.dumps({"profiles": records}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temp.replace(self.path)
        finally:
            temp.unlink(missing_ok=True)


def new_profile(**values) -> Profile:
    profile = Profile(id=str(uuid.uuid4()), **values)
    profile.validate()
    return profile
