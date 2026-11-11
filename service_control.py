"""LocalSystem SCM broker with SID-bound, fail-closed rclone operations.

Pipe clients never receive rclone RC credentials. The broker accepts only
start/stop commands for an already installed, protected S3Drive profile.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from pathlib import Path

from windows_service import (
    SERVICE_RUNNING, SERVICE_STOPPED, _sc, check_drive_free,
    config_text, protect_file, service_dir, service_name, service_status,
    stop_service,
)

CONTROL_SERVICE = "S3DriveControl"
PIPE_NAME = r"\\.\pipe\S3DriveControl-v1"
MAX_MESSAGE = 4096
_SID = re.compile(r"S-1-(?:[0-9]+-){1,15}[0-9]+\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_DRIVE = re.compile(r"[A-Z]:\Z")


class ControlUnavailable(ConnectionError):
    """The protected control service is absent or not accepting connections."""


class ReconfigurationRequired(RuntimeError):
    """The installed service settings differ from the portable profile."""


class EnrollmentRequired(PermissionError):
    """The existing service needs one-time UAC authorization to use the broker."""


def validate_owner_sid(value: str) -> str:
    if not isinstance(value, str) or len(value) > 180 or not _SID.fullmatch(value):
        raise ValueError("Некорректный SID владельца службы.")
    if value in ("S-1-5-18", "S-1-5-19", "S-1-5-20"):
        raise ValueError("Системный SID нельзя назначать владельцем диска.")
    return value


def current_user_sid() -> str:
    """Use the interactive GUI process token, not the elevated helper's token."""
    import win32api
    import win32con
    import win32security

    token = win32security.OpenProcessToken(
        win32api.GetCurrentProcess(), win32con.TOKEN_QUERY
    )
    try:
        sid, _ = win32security.GetTokenInformation(token, win32security.TokenUser)
        return validate_owner_sid(win32security.ConvertSidToStringSid(sid))
    finally:
        token.Close()


def config_fingerprint(profile) -> str:
    return hashlib.sha256(config_text(profile).encode("utf-8")).hexdigest()


def enroll_profile(profile_id: str, owner_sid: str, *, drive: str) -> None:
    """Executed only after explicit UAC consent, in the protected service folder.

    Enroll the configuration actually installed, not an arbitrary GUI value.
    The owner receives no write privileges on this folder or RC password.
    """
    service_name(profile_id)
    validate_owner_sid(owner_sid)
    if not _DRIVE.fullmatch(drive):
        raise ValueError("Некорректная буква диска.")
    root = service_dir(profile_id)
    config = root / "rclone.conf"
    if config.is_symlink() or not config.is_file():
        raise FileNotFoundError("Защищённая конфигурация службы отсутствует.")
    # Text mode normalizes Windows CRLF to LF, matching config_text(profile).
    fingerprint = hashlib.sha256(config.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
    payload = {"owner_sid": owner_sid, "fingerprint": fingerprint, "drive": drive}
    temp = root / "owner.json.tmp"
    target = root / "owner.json"
    if target.is_symlink() or temp.is_symlink():
        raise RuntimeError("Недопустимая ссылка в каталоге службы.")
    try:
        temp.write_text(json.dumps(payload), encoding="utf-8")
        protect_file(temp)
        os.replace(temp, target)
        protect_file(target)
    finally:
        temp.unlink(missing_ok=True)


def load_enrollment(profile_id: str) -> dict:
    service_name(profile_id)
    path = service_dir(profile_id) / "owner.json"
    if path.is_symlink() or not path.is_file():
        raise EnrollmentRequired("Служба ещё не зарегистрирована для управления без UAC.")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or set(value) != {"owner_sid", "fingerprint", "drive"}:
        raise PermissionError("Нарушен формат регистрации службы.")
    validate_owner_sid(value["owner_sid"])
    if not isinstance(value["fingerprint"], str) or not _SHA.fullmatch(value["fingerprint"]):
        raise PermissionError("Повреждена регистрация службы.")
    if not isinstance(value["drive"], str) or not _DRIVE.fullmatch(value["drive"]):
        raise PermissionError("Повреждена буква диска.")
    return value


def parse_request(payload: dict) -> tuple[str, str, str | None]:
    if not isinstance(payload, dict):
        raise ValueError("Некорректный запрос управления службой.")
    action = payload.get("action")
    if action not in ("start", "stop"):
        raise ValueError("Операция запрещена: доступно только start/stop.")
    fields = {"action", "profile_id", "fingerprint"} if action == "start" else {
        "action", "profile_id"
    }
    if set(payload) != fields:
        raise ValueError("Некорректные параметры операции.")
    profile_id = payload["profile_id"]
    if not isinstance(profile_id, str):
        raise ValueError("Некорректный идентификатор профиля.")
    try:
        normalized = str(uuid.UUID(profile_id))
    except (ValueError, AttributeError) as exc:
        raise ValueError("Некорректный идентификатор профиля.") from exc
    if normalized != profile_id:
        raise ValueError("UUID профиля должен быть каноническим.")
    fingerprint = payload.get("fingerprint")
    if action == "start" and (
        not isinstance(fingerprint, str) or _SHA.fullmatch(fingerprint) is None
    ):
        raise ValueError("Некорректная контрольная сумма настроек.")
    return action, profile_id, fingerprint


def handle_request(payload: dict, client_sid: str) -> None:
    """This is the *only* privileged pipe dispatch path."""
    action, profile_id, fingerprint = parse_request(payload)
    # Verify identity BEFORE reading any service state or making SCM changes.
    registered = load_enrollment(profile_id)
    if validate_owner_sid(client_sid) != registered["owner_sid"]:
        raise PermissionError("Управлять этой службой может только владелец профиля.")
    state = service_status(profile_id)
    if state is None:
        raise RuntimeError("Служба не установлена.")
    if action == "start":
        if fingerprint != registered["fingerprint"]:
            raise ReconfigurationRequired(
                "Изменились настройки диска: требуется обновить службу с UAC."
            )
        if state == SERVICE_RUNNING:
            return
        if state != SERVICE_STOPPED:
            raise RuntimeError("Служба меняет состояние.")
        check_drive_free(registered["drive"])
        _sc("start", service_name(profile_id))
    else:
        # stop_service must read protected RC credentials and verify pending
        # uploads immediately before making the SCM stop request.
        stop_service(profile_id)


def _client_sid(pipe) -> str:
    import win32api
    import win32con
    import win32security

    # Never process a request if impersonation fails: LocalSystem must not
    # accidentally treat an unauthenticated caller as itself.
    win32security.ImpersonateNamedPipeClient(pipe)
    try:
        token = win32security.OpenThreadToken(
            win32api.GetCurrentThread(), win32con.TOKEN_QUERY, True
        )
        try:
            sid, _ = win32security.GetTokenInformation(
                token, win32security.TokenUser
            )
            return validate_owner_sid(win32security.ConvertSidToStringSid(sid))
        finally:
            token.Close()
    finally:
        win32security.RevertToSelf()


def _pipe_security_attributes():
    import win32security

    system = win32security.CreateWellKnownSid(win32security.WinLocalSystemSid, None)
    admins = win32security.CreateWellKnownSid(
        win32security.WinBuiltinAdministratorsSid, None
    )
    authenticated = win32security.CreateWellKnownSid(
        win32security.WinAuthenticatedUserSid, None
    )
    acl = win32security.ACL()
    for sid in (system, admins):
        acl.AddAccessAllowedAce(win32security.ACL_REVISION, 0x001F01FF, sid)
    # FILE_READ_DATA | FILE_WRITE_DATA, but NOT FILE_CREATE_PIPE_INSTANCE.
    acl.AddAccessAllowedAce(win32security.ACL_REVISION, 0x00000003, authenticated)
    descriptor = win32security.SECURITY_DESCRIPTOR()
    descriptor.SetSecurityDescriptorDacl(True, acl, False)
    attrs = win32security.SECURITY_ATTRIBUTES()
    attrs.SECURITY_DESCRIPTOR = descriptor
    return attrs


def _make_pipe():
    import win32pipe

    return win32pipe.CreateNamedPipe(
        PIPE_NAME,
        win32pipe.PIPE_ACCESS_DUPLEX | 0x00080000,  # FIRST_PIPE_INSTANCE
        win32pipe.PIPE_TYPE_MESSAGE | win32pipe.PIPE_READMODE_MESSAGE
        | win32pipe.PIPE_WAIT | 0x00000008,  # PIPE_REJECT_REMOTE_CLIENTS
        1, MAX_MESSAGE, MAX_MESSAGE, 0, _pipe_security_attributes(),
    )


def serve(stop_event) -> None:
    """Keep a single pipe instance alive until Windows SCM stops the broker."""
    import pywintypes
    import win32file
    import win32pipe

    pipe = _make_pipe()
    try:
        while not stop_event.is_set():
            try:
                win32pipe.ConnectNamedPipe(pipe, None)
            except pywintypes.error as exc:
                if exc.winerror != 535:  # ERROR_PIPE_CONNECTED
                    raise
            try:
                if stop_event.is_set():
                    break
                status, data = win32file.ReadFile(pipe, MAX_MESSAGE)
                if status != 0 or len(data) > MAX_MESSAGE:
                    raise ValueError("Слишком большой запрос управления.")
                request = json.loads(data.decode("utf-8"))
                sid = _client_sid(pipe)
                handle_request(request, sid)
                reply = {"ok": True}
            except Exception as exc:
                if isinstance(exc, ReconfigurationRequired):
                    reply = {"ok": False, "code": "RECONFIGURE", "error": str(exc)}
                elif isinstance(exc, EnrollmentRequired):
                    reply = {"ok": False, "code": "ENROLL", "error": str(exc)}
                elif isinstance(exc, (ValueError, PermissionError, RuntimeError,
                                      OSError, KeyError, TypeError)):
                    reply = {"ok": False, "code": "ERROR", "error": str(exc)[:500]}
                else:
                    reply = {"ok": False, "code": "ERROR",
                             "error": "Внутренняя ошибка управления службой."}
            try:
                win32file.WriteFile(pipe, json.dumps(reply, ensure_ascii=False).encode("utf-8"))
                win32file.FlushFileBuffers(pipe)
            except (OSError, pywintypes.error):
                pass
            finally:
                win32pipe.DisconnectNamedPipe(pipe)
    finally:
        win32file.CloseHandle(pipe)


def send_control(action: str, profile) -> None:
    """Unprivileged GUI interface. No RC password or secret crosses this pipe."""
    import pywintypes
    import win32con
    import win32file
    import win32pipe

    payload = {"action": action, "profile_id": profile.id}
    if action == "start":
        payload["fingerprint"] = config_fingerprint(profile)
    parse_request(payload)
    data = json.dumps(payload).encode("utf-8")
    try:
        win32pipe.WaitNamedPipe(PIPE_NAME, 3000)
        # Minimal read/write access; do not request FILE_CREATE_PIPE_INSTANCE.
        handle = win32file.CreateFile(
            PIPE_NAME, 0x00000003, 0, None, win32con.OPEN_EXISTING,
            0x00120000, None,  # SECURITY_SQOS_PRESENT | SECURITY_IMPERSONATION
        )
    except pywintypes.error as exc:
        if exc.winerror in (2, 53, 121, 231, 233):
            raise ControlUnavailable("Не запущена служба управления S3Drive.") from exc
        raise
    try:
        win32file.WriteFile(handle, data)
        status, reply = win32file.ReadFile(handle, MAX_MESSAGE)
        if status != 0:
            raise RuntimeError("Неполный ответ службы управления.")
    finally:
        win32file.CloseHandle(handle)
    answer = json.loads(reply.decode("utf-8"))
    if not isinstance(answer, dict):
        raise RuntimeError("Некорректный ответ службы управления.")
    if answer.get("ok") is True:
        return
    if answer.get("code") == "RECONFIGURE":
        raise ReconfigurationRequired(str(answer.get("error", "")))
    if answer.get("code") == "ENROLL":
        raise EnrollmentRequired(str(answer.get("error", "")))
    raise RuntimeError(str(answer.get("error", "Ошибка управления службой."))[:500])


def wake_server() -> None:
    """Unblock ConnectNamedPipe on SERVICE_CONTROL_STOP."""
    try:
        import win32con
        import win32file
        import win32pipe
        win32pipe.WaitNamedPipe(PIPE_NAME, 1000)
        handle = win32file.CreateFile(
            PIPE_NAME, 0x00000003, 0, None, win32con.OPEN_EXISTING,
            0x00120000, None,
        )
        win32file.CloseHandle(handle)
    except OSError:
        pass
