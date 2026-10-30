"""Статистика загрузок rclone для отдельного окна прогресса.

Пользовательское монтирование использует авторизованный RC. Служба LocalSystem
публикует статистику только через JSON-журнал с доступом на чтение; RC-пароль,
способный выполнять произвольные команды от SYSTEM, GUI не получает.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class TransferSnapshot:
    queued: int
    uploading: int
    errors: int
    cache_bytes: int
    current_file: str = ""
    percent: int = 0
    speed: float = 0.0


def parse_snapshot(vfs: dict, queue: dict, core: dict) -> TransferSnapshot:
    """Точное состояние VFS для пользовательского монтирования."""
    disk = vfs["diskCache"]
    items = queue.get("queue", [])
    if not isinstance(items, list):
        raise ValueError("Некорректный ответ очереди rclone.")
    active = core.get("transferring") or []
    transfer = active[0] if active else {}
    if not transfer:
        transfer = next((item for item in items if item.get("uploading")), {})
    return TransferSnapshot(
        queued=int(disk["uploadsQueued"]),
        uploading=int(disk["uploadsInProgress"]),
        errors=int(disk["erroredFiles"]),
        cache_bytes=int(disk["bytesUsed"]),
        current_file=str(transfer.get("name") or ""),
        percent=max(0, min(100, int(transfer.get("percentage") or 0))),
        speed=max(0.0, float(transfer.get("speedAvg") or transfer.get("speed") or 0)),
    )


def parse_service_stats(stats: dict) -> TransferSnapshot:
    """Отдельный поток telemetry: агрегаты core/stats, без доступа к RC."""
    active = stats.get("transferring") or []
    if not isinstance(active, list):
        raise ValueError("Некорректная статистика передач.")
    transfer = active[0] if active else {}
    return TransferSnapshot(
        queued=0,  # Очередь VFS не содержится в периодических core/stats.
        uploading=len(active),
        errors=max(0, int(stats.get("errors") or 0)),
        cache_bytes=0,  # Объём кэша недоступен через журнал core/stats.
        current_file=str(transfer.get("name") or ""),
        percent=max(0, min(100, int(transfer.get("percentage") or 0))),
        speed=max(0.0, float(transfer.get("speedAvg") or transfer.get("speed") or 0)),
    )


def latest_service_stats(path: Path, *, now: datetime | None = None) -> TransferSnapshot:
    """Чтение только свежей завершённой записи JSONL, без чужих секретов.

    При несовместимом старом логе не показываем устаревшее состояние загрузок.
    """
    path = Path(path)
    if path.is_symlink():
        raise RuntimeError("Недопустимая ссылка на журнал службы.")
    if not path.is_file():
        raise RuntimeError(
            "Монитор службы ещё не настроен. Остановите службу и снова подключите диск."
        )
    try:
        with path.open("rb") as stream:
            stream.seek(0, 2)
            length = stream.tell()
            stream.seek(max(0, length - 262144))
            raw = stream.read(262144)
    except PermissionError as exc:
        raise RuntimeError(
            "Нет доступа к статистике службы. Остановите службу и снова подключите диск."
        ) from exc
    if length > 262144:
        raw = raw.partition(b"\n")[2]
    now = now or datetime.now(timezone.utc)
    for line in reversed(raw.splitlines()):
        try:
            record = json.loads(line)
            if not isinstance(record, dict) or not isinstance(record.get("stats"), dict):
                continue
            timestamp = datetime.fromisoformat(str(record["time"]).replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                continue
            elapsed = (now - timestamp).total_seconds()
            if elapsed < -5 or elapsed > 15:
                raise RuntimeError("Статистика службы устарела; новых передач не обнаружено.")
            return parse_service_stats(record["stats"])
        except (ValueError, TypeError, KeyError, UnicodeDecodeError):
            continue
    raise RuntimeError(
        "Нет свежей статистики службы. Для включения мониторинга остановите "
        "службу и снова подключите диск через новую версию S3Drive."
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
