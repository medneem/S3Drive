"""rclone + WinFsp mounting backend. Secrets travel in the child environment, not CLI args."""
from __future__ import annotations

import base64
import os
import secrets
import shutil
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.request import Request, urlopen

from profiles import Profile, app_data_dir

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
        "--volname", f"S3Drive - {profile.name}",
        "--rc", "--rc-addr", f"127.0.0.1:{port}",
        "--log-level", "INFO",
    ]


def find_rclone() -> str:
    alongside = Path(__file__).resolve().parent / "rclone.exe"
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
    process: subprocess.Popen
    port: int
    password: str
    log_file: object


class MountManager:
    def __init__(self):
        self.sessions: dict[str, Session] = {}

    def is_mounted(self, profile_id: str) -> bool:
        session = self.sessions.get(profile_id)
        if session is None:
            return False
        if session.process.poll() is not None:
            session.log_file.close()
            del self.sessions[profile_id]
            return False
        return True

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
            process = subprocess.Popen(
                mount_command(profile, executable, cache, port),
                stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
                env=env, creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except Exception:
            stream.close()
            raise
        self.sessions[profile.id] = Session(profile, process, port, password, stream)

    def stop(self, profile_id: str) -> None:
        if not self.is_mounted(profile_id):
            return
        session = self.sessions[profile_id]
        token = base64.b64encode(f"s3drive:{session.password}".encode()).decode()
        request = Request(
            f"http://127.0.0.1:{session.port}/core/quit",
            data=b"{}", method="POST",
            headers={"Authorization": "Basic " + token, "Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=5) as response:
                response.read()
        except Exception:
            # core/quit may close the connection before replying.
            pass
        try:
            session.process.wait(timeout=15)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("rclone не завершился. Диск оставлен подключённым во избежание потери данных.") from exc
        finally:
            self.is_mounted(profile_id)
