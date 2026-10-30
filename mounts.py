"""rclone + WinFsp mounting backend. Secrets travel in the child environment, not CLI args."""
from __future__ import annotations

import base64
import json
import os
import secrets
import shutil
import socket
import sys
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.request import Request, urlopen

from profiles import Profile, app_data_dir
from background import BackgroundRecord, BackgroundRegistry
from cache_ops import ensure_cache_idle, erase_cache_directory

REMOTE = "s3drive_mount"


def remote_environment(profile: Profile) -> dict[str, str]:
    profile.validate()
    prefix = f"RCLONE_CONFIG_{REMOTE.upper()}_"
    return {
        prefix + "TYPE": "s3",
        prefix + "PROVIDER": "Other",
        prefix + "ENV_AUTH": "false",
        prefix + "ACCESS_KEY_ID": profile.access_key,
        prefix + "SECRET_ACCESS_KEY": profile.secret_key,
        prefix + "ENDPOINT": profile.endpoint.rstrip("/"),
        prefix + "REGION": profile.region,
        prefix + "FORCE_PATH_STYLE": str(profile.path_style).lower(),
    }


def mount_command(profile: Profile, executable: str, cache_dir: Path, port: int) -> list[str]:
    profile.validate()
    if not 1 <= port <= 65535:
        raise ValueError("Некорректный порт управления.")
    return [
        executable, "mount", f"{REMOTE}:{profile.bucket}", profile.drive,
        "--vfs-cache-mode", "writes",
        "--cache-dir", str(cache_dir),
        # rclone v1.75.2 SetVolumeName uses len(vol) > 32, i.e. UTF-8 BYTES,
        # and slices the name before WinFsp receives it. Passing the
        # native FUSE option bypasses that lossy rclone function.
        "--option", "volname=" + profile.name,
        "--rc", "--rc-addr", f"127.0.0.1:{port}",
        "--log-level", "INFO",
    ]


def find_rclone() -> str:
    app_dir = (Path(sys.executable).resolve().parent if getattr(sys, "frozen", False)
               else Path(__file__).resolve().parent)
    alongside = app_dir / "rclone.exe"
    if alongside.is_file():
        return str(alongside)
    resolved = shutil.which("rclone.exe") or shutil.which("rclone")
    if resolved:
        return resolved
    raise FileNotFoundError("Не найден rclone.exe. Установите rclone или положите его рядом с S3Drive.exe.")


def free_local_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@dataclass
class Session:
    profile: Profile
    process: subprocess.Popen | None
    port: int
    password: str
    pid: int
    keep_running: bool
    log_file: object | None = None


class MountManager:
    def __init__(self, registry: BackgroundRegistry | None = None):
        self.sessions: dict[str, Session] = {}
        self.registry = registry if registry is not None else BackgroundRegistry()

    @staticmethod
    def _rc(session: Session, method: str, timeout: float = 3) -> dict:
        # IMPORTANT: the rclone RC API has full user-level command execution
        # privileges. Never bind it to a non-loopback interface.
        token = base64.b64encode(f"s3drive:{session.password}".encode()).decode()
        request = Request(
            f"http://127.0.0.1:{session.port}/{method}",
            data=b"{}", method="POST",
            headers={"Authorization": "Basic " + token, "Content-Type": "application/json"},
        )
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def _rc_pid(self, session: Session) -> int | None:
        try:
            response = self._rc(session, "core/pid", timeout=0.75)
            return int(response["pid"])
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def recover(self, profiles: list[Profile]) -> None:
        """Adopt live mounts from an earlier S3Drive window (same Windows user).

        We use authenticated core/pid to avoid mistaking an unrelated process
        which happens to reuse the local port for one of ours.
        """
        configured = {profile.id: profile for profile in profiles}
        for record in self.registry.load().values():
            profile = configured.get(record.profile_id)
            if profile is None or profile.drive != record.drive:
                continue
            session = Session(
                profile=profile, process=None, port=record.port,
                password=record.password, pid=record.pid,
                keep_running=record.keep_running,
            )
            if self._rc_pid(session) == record.pid:
                self.sessions[profile.id] = session
            elif not Path(profile.drive + "\\").exists():
                # Stale record after an OS reboot / terminated rclone.
                self.registry.delete(profile.id)

    def _forget(self, profile_id: str) -> None:
        session = self.sessions.pop(profile_id, None)
        if session and session.log_file:
            session.log_file.close()
        self.registry.delete(profile_id)

    def is_mounted(self, profile_id: str) -> bool:
        session = self.sessions.get(profile_id)
        if session is None:
            return False
        if session.process is not None:
            running = session.process.poll() is None
        else:
            running = self._rc_pid(session) == session.pid
        if not running:
            self._forget(profile_id)
        return running

    def keep_running(self, profile_id: str) -> bool:
        return self.is_mounted(profile_id) and self.sessions[profile_id].keep_running

    def set_keep_running(self, profile_id: str, enabled: bool) -> None:
        if not self.is_mounted(profile_id):
            return
        session = self.sessions[profile_id]
        self.registry.put(BackgroundRecord(
            profile_id, session.profile.drive, session.port, session.pid,
            session.password, bool(enabled),
        ))
        session.keep_running = bool(enabled)

    def start(self, profile: Profile) -> None:
        profile.validate()
        if self.is_mounted(profile.id):
            raise ValueError("Это подключение уже запущено.")
        if any(self.is_mounted(p) and s.profile.drive == profile.drive
               for p, s in list(self.sessions.items())):
            raise ValueError("Буква диска уже используется другим подключением S3Drive.")
        if os.name != "nt":
            raise OSError("Монтирование поддерживается только в Windows.")
        if Path(profile.drive + "\\").exists():
            raise ValueError(f"Диск {profile.drive} уже занят.")
        executable = find_rclone()
        cache = app_data_dir() / "cache" / profile.id
        cache.mkdir(parents=True, exist_ok=True)
        logs = app_data_dir() / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        port = free_local_port()
        password = secrets.token_urlsafe(32)
        env = os.environ.copy()
        env.update(remote_environment(profile))
        env.update({"RCLONE_RC_USER": "s3drive", "RCLONE_RC_PASS": password})
        stream = (logs / f"{profile.id}.log").open("ab")
        try:
            # Detached from the UI's console/job, still in the INTERACTIVE
            # user's Windows session (not Session 0 / LocalSystem).
            flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
            process = subprocess.Popen(
                mount_command(profile, executable, cache, port),
                stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
                env=env, creationflags=flags,
            )
        except Exception:
            stream.close()
            raise
        session = Session(profile, process, port, password, process.pid,
                          profile.keep_running, stream)
        self.sessions[profile.id] = session
        try:
            self.registry.put(BackgroundRecord(
                profile.id, profile.drive, port, process.pid, password, profile.keep_running
            ))
        except Exception:
            # A mount must not become impossible to recover simply because
            # writing its control metadata failed.
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            self.sessions.pop(profile.id, None)
            stream.close()
            raise

    def clear_cache(self, profile_id: str) -> bool:
        """Safely disconnect this live user mount before deleting VFS cache.

        Refuse to delete offline/unknown caches: a crashed mount may still
        have unsent data not represented in any live rclone statistics.
        """
        import uuid
        uuid.UUID(profile_id)
        if not self.is_mounted(profile_id):
            raise RuntimeError(
                "Для безопасной очистки подключите диск и дождитесь окончания передач. "
                "Кэш остановленного или аварийно завершённого процесса не удаляется."
            )
        session = self.sessions[profile_id]
        ensure_cache_idle(self._rc(session, "vfs/stats"))
        self.stop(profile_id)
        cache_root = app_data_dir() / "cache"
        return erase_cache_directory(cache_root / profile_id, parent=cache_root)

    def stop(self, profile_id: str) -> None:
        if not self.is_mounted(profile_id):
            return
        session = self.sessions[profile_id]
        # Fail closed when uploads are pending; an unsafe quit can strand data
        # in the VFS cache that has not reached the S3 backend.
        try:
            stats = self._rc(session, "vfs/stats")
            cache = stats["diskCache"]
            queued = int(cache["uploadsQueued"])
            active = int(cache["uploadsInProgress"])
            errors = int(cache["erroredFiles"])
        except Exception as exc:
            raise RuntimeError(
                "Не удалось проверить очередь записи rclone; диск не отключён. "
                "Проверьте журнал и повторите попытку."
            ) from exc
        if queued or active or errors:
            raise RuntimeError(
                f"Диск не отключён: ожидают загрузки {queued}, передаются {active}, "
                f"файлов с ошибками {errors}. Дождитесь завершения записи и проверьте ошибки."
            )
        try:
            self._rc(session, "core/quit", timeout=5)
        except Exception:
            # rclone may terminate its RC server before the response arrives.
            pass
        if session.process is not None:
            try:
                session.process.wait(timeout=15)
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError("rclone не завершился. Диск оставлен подключённым.") from exc
        else:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and self._rc_pid(session) == session.pid:
                time.sleep(0.2)
            if self._rc_pid(session) == session.pid:
                raise RuntimeError("Фоновый rclone не завершился. Диск оставлен подключённым.")
        self._forget(profile_id)
