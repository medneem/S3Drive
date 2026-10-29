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
ERROR_ALREADY_EXISTS = 183


def _windows_kernel32():
    if os.name != "nt":
        raise OSError("Ограничение запуска S3Drive поддерживается только Windows.")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
    kernel32.CreateMutexW.restype = wintypes.HANDLE
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

    def close(self):
        if self._handle is not None:
            handle, self._handle = self._handle, None
            self._kernel32.CloseHandle(handle)
        self.acquired = False

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
        return False
