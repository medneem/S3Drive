"""One S3Drive GUI per interactive Windows session, using a kernel mutex.

The Windows service, admin helper and rclone are separate processes and are
intentionally not subject to this UI-only singleton.
"""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

# Local\ applies to the interactive Windows session, not Session 0 services.
# A kernel mutex is atomic across processes, including parallel launches.
MUTEX_NAME = r"Local\S3Drive.Desktop.GUI"
ACTIVATION_EVENT = r"Local\S3Drive.Desktop.Activate"
ACTIVATION_ACK_EVENT = r"Local\S3Drive.Desktop.ActivateAck"
ERROR_ALREADY_EXISTS = 183
EVENT_MODIFY_STATE = 0x0002
SYNCHRONIZE = 0x00100000
ACTIVATION_TIMEOUT_MS = 2000
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 0x102


def _windows_kernel32():
    if os.name != "nt":
        raise OSError("Ограничение запуска S3Drive поддерживается только Windows.")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CreateEventW.argtypes = (
        ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR
    )
    kernel32.CreateEventW.restype = wintypes.HANDLE
    kernel32.OpenEventW.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
    kernel32.OpenEventW.restype = wintypes.HANDLE
    kernel32.SetEvent.argtypes = (wintypes.HANDLE,)
    kernel32.SetEvent.restype = wintypes.BOOL
    kernel32.ResetEvent.argtypes = (wintypes.HANDLE,)
    kernel32.ResetEvent.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


class SingleInstance:
    """Keep a kernel mutex HANDLE open for the entire GUI application lifetime.

    Owning the mutex itself is unnecessary: the named kernel object exists as
    long as the first process retains its handle. Even an unclean exit releases
    the handle in Windows, so stale lock files cannot occur.
    """

    def __init__(self, *, kernel32=None, get_last_error=None):
        self._kernel32 = kernel32 if kernel32 is not None else _windows_kernel32()
        self._get_last_error = (
            get_last_error if get_last_error is not None else ctypes.get_last_error
        )
        self._handle = None
        self._activation_handle = None
        self._ack_handle = None
        self.acquired = False

    def __enter__(self):
        if self._handle is not None:
            raise RuntimeError("Блокировка S3Drive уже открыта.")
        handle = self._kernel32.CreateMutexW(None, False, MUTEX_NAME)
        if not handle:
            raise OSError(
                self._get_last_error(),
                "Не удалось проверить, запущена ли другая копия S3Drive.",
            )
        self._handle = handle
        self.acquired = self._get_last_error() != ERROR_ALREADY_EXISTS
        if not self.acquired:
            # Never keep the duplicate handle open unnecessarily.
            self.close()
        return self

    def enable_activation(self) -> None:
        """Start receiving focus requests only after the GUI has the mutex."""
        if not self.acquired or self._activation_handle is not None:
            return
        handle = self._kernel32.CreateEventW(None, False, False, ACTIVATION_EVENT)
        if not handle:
            raise OSError(
                self._get_last_error(), "Не удалось создать сигнал активации S3Drive."
            )
        # The acknowledgement is signalled only *after* the GUI has restored
        # its window. Without it a dead or hidden GUI looks like a success.
        ack = self._kernel32.CreateEventW(None, False, False, ACTIVATION_ACK_EVENT)
        if not ack:
            self._kernel32.CloseHandle(handle)
            raise OSError(
                self._get_last_error(), "Не удалось создать подтверждение активации S3Drive."
            )
        self._activation_handle = handle
        self._ack_handle = ack

    def activation_requested(self) -> bool:
        """Consume a focus request without busy-waiting or starting a second UI."""
        if not self.acquired or self._activation_handle is None:
            return False
        return self._kernel32.WaitForSingleObject(self._activation_handle, 0) == WAIT_OBJECT_0

    def acknowledge_activation(self) -> bool:
        """Confirm that the existing GUI processed the restore request."""
        if not self.acquired or self._ack_handle is None:
            return False
        return bool(self._kernel32.SetEvent(self._ack_handle))

    def request_activation(self) -> bool:
        """Wait briefly until the existing GUI actually processes restoration."""
        if self.acquired:
            return False
        handle = self._kernel32.OpenEventW(EVENT_MODIFY_STATE, False, ACTIVATION_EVENT)
        if not handle:
            return False
        try:
            ack = self._kernel32.OpenEventW(
                EVENT_MODIFY_STATE | SYNCHRONIZE, False, ACTIVATION_ACK_EVENT
            )
            if not ack:
                return False
            try:
                # Clear any previous reply; a stale auto-reset event must not
                # be interpreted as a response to this launch.
                if not self._kernel32.ResetEvent(ack):
                    return False
                if not self._kernel32.SetEvent(handle):
                    return False
                return (
                    self._kernel32.WaitForSingleObject(ack, ACTIVATION_TIMEOUT_MS)
                    == WAIT_OBJECT_0
                )
            finally:
                self._kernel32.CloseHandle(ack)
        finally:
            self._kernel32.CloseHandle(handle)

    def close(self):
        if self._ack_handle is not None:
            handle, self._ack_handle = self._ack_handle, None
            self._kernel32.CloseHandle(handle)
        if self._activation_handle is not None:
            handle, self._activation_handle = self._activation_handle, None
            self._kernel32.CloseHandle(handle)
        if self._handle is not None:
            handle, self._handle = self._handle, None
            self._kernel32.CloseHandle(handle)
        self.acquired = False

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
        return False
