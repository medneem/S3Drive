"""Protected S3DriveControl Windows service host. Runs as LocalSystem.

Only the tiny local named-pipe broker, never the main GUI or S3 secrets store,
handles privileged SCM calls in ordinary day-to-day use.
"""
from __future__ import annotations

import sys
import threading

import servicemanager
import win32service
import win32serviceutil

from service_control import CONTROL_SERVICE, parse_request, serve, wake_server


class S3DriveControlService(win32serviceutil.ServiceFramework):
    _svc_name_ = CONTROL_SERVICE
    _svc_display_name_ = "S3Drive - Safe service controller"
    _svc_description_ = "Authorized local start/stop of S3Drive mounts with rclone VFS safety checks"

    def __init__(self, args):
        super().__init__(args)
        self.stopping = threading.Event()

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        self.stopping.set()
        wake_server()

    def SvcDoRun(self):
        try:
            serve(self.stopping)
        except Exception as exc:
            try:
                servicemanager.LogErrorMsg("S3DriveControl stopped: " + str(exc)[:400])
            finally:
                raise


def main() -> int:
    if len(sys.argv) == 2 and sys.argv[1] == "--self-test":
        # No UAC, filesystem changes, service creation or S3 credentials.
        assert parse_request({
            "action": "stop", "profile_id": "12345678-1234-1234-1234-123456789abc"
        })[0] == "stop"
        return 0
    if len(sys.argv) != 1:
        raise ValueError("S3DriveControl.exe must be started by Windows SCM.")
    servicemanager.PrepareToHostSingle(S3DriveControlService)
    servicemanager.StartServiceCtrlDispatcher()
    return 0


if __name__ == "__main__":
    sys.exit(main())
