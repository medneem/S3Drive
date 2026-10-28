"""Persistent rclone control registry, encrypted with the current Windows user DPAPI.

This is not a Windows Service. The registry lets a later S3Drive UI reconnect
to a detached rclone process started in the user's interactive desktop session.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from profiles import app_data_dir, dpapi_decrypt, dpapi_encrypt


@dataclass(frozen=True)
class BackgroundRecord:
    profile_id: str
    drive: str
    port: int
    pid: int
    password: str
    keep_running: bool

    def validate(self) -> None:
        if not self.profile_id or len(self.profile_id) > 100:
            raise ValueError("Неверный идентификатор подключения.")
        if len(self.drive) != 2 or self.drive[1] != ":" or not self.drive[0].isupper():
            raise ValueError("Неверная буква диска в реестре сессий.")
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError("Неверный порт фонового процесса.")
        if type(self.pid) is not int or self.pid < 1:
            raise ValueError("Неверный PID фонового процесса.")
        if not self.password or len(self.password) > 512:
            raise ValueError("Неверный пароль фонового процесса.")
        if type(self.keep_running) is not bool:
            raise ValueError("Неверный флаг фонового процесса.")


class BackgroundRegistry:
    def __init__(
        self, path: Path | None = None, *,
        encrypt: Callable[[str], str] | None = None,
        decrypt: Callable[[str], str] | None = None,
    ):
        self.path = path if path is not None else app_data_dir() / "background.json"
        self.encrypt = encrypt or dpapi_encrypt
        self.decrypt = decrypt or dpapi_decrypt

    def load(self) -> dict[str, BackgroundRecord]:
        if not self.path.is_file():
            return {}
        doc = json.loads(self.path.read_text(encoding="utf-8-sig"))
        if not isinstance(doc, dict) or not isinstance(doc.get("sessions"), list):
            raise ValueError("background.json повреждён: ожидается массив sessions.")
        records: dict[str, BackgroundRecord] = {}
        for raw in doc["sessions"]:
            if not isinstance(raw, dict):
                raise ValueError("Неверная запись background.json.")
            record = BackgroundRecord(
                profile_id=raw["profile_id"],
                drive=raw["drive"],
                port=raw["port"],
                pid=raw["pid"],
                password=self.decrypt(raw["password"]),
                keep_running=raw["keep_running"],
            )
            record.validate()
            if record.profile_id in records:
                raise ValueError("Дубликат подключения в background.json.")
            records[record.profile_id] = record
        return records

    def _write(self, records: dict[str, BackgroundRecord]) -> None:
        data = []
        for record in records.values():
            record.validate()
            saved = asdict(record)
            saved["password"] = self.encrypt(record.password)
            data.append(saved)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps({"sessions": data}, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
            tmp.replace(self.path)
        finally:
            tmp.unlink(missing_ok=True)

    def put(self, record: BackgroundRecord) -> None:
        records = self.load()
        records[record.profile_id] = record
        self._write(records)

    def delete(self, profile_id: str) -> None:
        records = self.load()
        if profile_id in records:
            del records[profile_id]
            self._write(records)


def sessions_to_stop(sessions) -> list[str]:
    """A window may close without stopping the opted-in background mounts."""
    return [session.profile.id for session in sessions if not session.keep_running]
