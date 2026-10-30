"""Статистика сетевых передач S3 (в обе стороны) для отдельного окна прогресса.

Пользовательские диски: авторизованный RC + очередь VFS.
Службы LocalSystem: только JSON-журнал; секреты RC не выдаются GUI.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

_DIRECTIONS = ("upload", "download", "queued", "unknown")


@dataclass(frozen=True)
class TransferItem:
    name: str
    direction: str
    percent: int = 0
    speed: float = 0.0
    bytes_done: int = 0
    size: int = 0
    eta: float | None = None

    def __post_init__(self):
        if self.direction not in _DIRECTIONS:
            raise ValueError("Недопустимое направление передачи.")


@dataclass(frozen=True)
class TransferSnapshot:
    queued: int
    uploading: int
    errors: int
    cache_bytes: int
    current_file: str = ""
    percent: int = 0
    speed: float = 0.0
    downloading: int = 0
    unknown: int = 0
    transfers: tuple[TransferItem, ...] = ()

    @property
    def has_activity(self) -> bool:
        return bool(self.queued or self.uploading or self.downloading or self.unknown
                    or self.transfers)


def _name(value) -> str:
    return str(value or "").replace("\\", "/").removeprefix("./").strip("/").casefold()


def _transfer(item: dict, direction: str) -> TransferItem:
    if not isinstance(item, dict):
        raise ValueError("Некорректные данные rclone.")
    done = max(0, int(item.get("bytes") or 0))
    size = max(0, int(item.get("size") or 0))
    percent = item.get("percentage")
    if percent is None:
        percent = 100 * done / size if size else 0
    eta = item.get("eta")
    return TransferItem(
        name=str(item.get("name") or ""),
        direction=direction,
        percent=max(0, min(100, int(percent))),
        speed=max(0.0, float(item.get("speedAvg") or item.get("speed") or 0)),
        bytes_done=done,
        size=size,
        eta=max(0.0, float(eta)) if eta is not None else None,
    )


def _snapshot(entries: list[TransferItem], *, queued: int, uploading: int,
              errors: int, cache_bytes: int) -> TransferSnapshot:
    first = next((x for x in entries if x.direction != "queued"),
                 entries[0] if entries else None)
    return TransferSnapshot(
        queued=max(0, queued), uploading=max(0, uploading),
        downloading=sum(x.direction == "download" for x in entries),
        unknown=sum(x.direction == "unknown" for x in entries),
        errors=max(0, errors), cache_bytes=max(0, cache_bytes),
        current_file=first.name if first else "",
        percent=first.percent if first else 0,
        speed=first.speed if first else 0,
        transfers=tuple(entries),
    )


def parse_snapshot(vfs: dict, queue: dict, core: dict) -> TransferSnapshot:
    """RC: очередь VFS подтверждает outgoing, остальные активные чтения — incoming."""
    disk = vfs["diskCache"]
    items = queue.get("queue", [])
    active = core.get("transferring") or []
    if not isinstance(items, list) or not isinstance(active, list):
        raise ValueError("Некорректный ответ rclone о передачах.")
    for item in items + active:
        if not isinstance(item, dict):
            raise ValueError("Некорректный элемент очереди rclone.")
    by_name = {_name(i.get("name")): i for i in items}
    active_names = {_name(item.get("name")) for item in active}
    uploads_in_progress = int(disk["uploadsInProgress"])
    identified_uploads = sum(
        _name(item.get("name")) in by_name for item in active
    )
    unmatched_uploads = max(0, uploads_in_progress - identified_uploads)
    entries = []
    for item in active:
        name = _name(item.get("name"))
        if name in by_name:
            direction = "upload"
        elif unmatched_uploads:
            # Не выдавать исходящую запись с несовпавшим именем за скачивание.
            unmatched_uploads -= 1
            direction = "unknown"
        else:
            direction = "download"
        entries.append(_transfer(item, direction))
    for key, item in by_name.items():
        if key not in active_names:
            entries.append(_transfer(item, "upload" if item.get("uploading") else "queued"))
    return _snapshot(entries,
                     queued=int(disk["uploadsQueued"]),
                     uploading=uploads_in_progress,
                     errors=int(disk["erroredFiles"]),
                     cache_bytes=int(disk.get("bytesUsed") or 0))


def parse_service_stats(stats: dict, upload_names: set[str] | None = None
                        ) -> TransferSnapshot:
    """core/stats не содержит направления. Без доказательств пишем «неизвестно»."""
    active = stats.get("transferring") or []
    if not isinstance(active, list):
        raise ValueError("Некорректная статистика передач.")
    entries = []
    for transfer in active:
        name = _name(transfer.get("name")) if isinstance(transfer, dict) else ""
        if upload_names is None:
            direction = "unknown"
        else:
            direction = "upload" if name in upload_names else "download"
        entries.append(_transfer(transfer, direction))
    return _snapshot(
        entries, queued=0, uploading=sum(i.direction == "upload" for i in entries),
        errors=int(stats.get("errors") or 0), cache_bytes=0,
    )


def _timestamp(record: dict) -> datetime | None:
    try:
        ts = datetime.fromisoformat(str(record["time"]).replace("Z", "+00:00"))
        return ts if ts.tzinfo else None
    except (TypeError, ValueError, KeyError):
        return None


def _upload_history(records: list[dict], reference: datetime) -> set[str]:
    """По INFO-событиям vfs cache отслеживаем ожидающие/активные записи.

    INFO об очереди и завершении присутствуют в rclone 1.75.2. Старые
    события старше 1 часа не используются для классификации скачиваний.
    """
    pending: set[str] = set()
    for record in records:
        when = _timestamp(record)
        if when is None or (reference - when).total_seconds() > 3600:
            continue
        name = _name(record.get("object"))
        msg = str(record.get("msg") or "")
        if not name:
            continue
        if "vfs cache: queuing for upload" in msg:
            pending.add(name)
        elif ("vfs cache: upload succeeded" in msg or
              "vfs cache: upload canceled" in msg):
            pending.discard(name)
    return pending


def latest_service_stats(path: Path, *, now: datetime | None = None) -> TransferSnapshot:
    """Свежий core/stats вместе с контекстом INFO из последних строк JSONL."""
    path = Path(path)
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        raise RuntimeError("Недопустимая ссылка на журнал службы.")
    if not path.is_file():
        raise RuntimeError("Монитор службы ещё не настроен. Переподключите службу.")
    try:
        with path.open("rb") as stream:
            stream.seek(0, 2)
            length = stream.tell()
            stream.seek(max(0, length - 1048576))
            raw = stream.read(1048576)
    except PermissionError as exc:
        raise RuntimeError("Нет доступа к статистике службы. Переподключите её.") from exc
    if length > 1048576:
        raw = raw.partition(b"\n")[2]
    now = now or datetime.now(timezone.utc)
    records = []
    for line in raw.splitlines():
        try:
            record = json.loads(line)
            if isinstance(record, dict):
                records.append(record)
        except (ValueError, UnicodeDecodeError):
            continue
    for i in range(len(records)-1, -1, -1):
        record = records[i]
        if not isinstance(record.get("stats"), dict):
            continue
        when = _timestamp(record)
        if when is None:
            continue
        elapsed = (now - when).total_seconds()
        if elapsed < -5 or elapsed > 15:
            raise RuntimeError("Статистика службы устарела; новых передач не обнаружено.")
        pending = _upload_history(records[:i+1], when)
        return parse_service_stats(record["stats"], pending)
    raise RuntimeError(
        "Нет свежей статистики службы. Остановите службу и снова подключите диск "
        "через новую версию S3Drive."
    )


def read_service_snapshot(profile_id: str) -> TransferSnapshot:
    from windows_service import service_dir
    return latest_service_stats(service_dir(profile_id) / "rclone.log")


def read_user_snapshot(manager, profile_id: str) -> TransferSnapshot:
    session = manager.sessions[profile_id]
    return parse_snapshot(
        manager._rc(session, "vfs/stats", timeout=1.5),
        manager._rc(session, "vfs/queue", timeout=1.5),
        manager._rc(session, "core/stats", timeout=1.5),
    )
