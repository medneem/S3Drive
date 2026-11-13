# S3Drive

**English** | [Русский](README.ru.md)

[![Windows Build](https://github.com/medneem/S3Drive/actions/workflows/build-windows.yml/badge.svg)](https://github.com/medneem/S3Drive/actions/workflows/build-windows.yml)
[![S3 E2E](https://github.com/medneem/S3Drive/actions/workflows/s3-deletion-e2e.yml/badge.svg)](https://github.com/medneem/S3Drive/actions/workflows/s3-deletion-e2e.yml)
[![Release](https://img.shields.io/github/v/release/medneem/S3Drive?label=release&logo=github&color=2ea44f)](https://github.com/medneem/S3Drive/releases/latest)
[![Downloads](https://img.shields.io/github/downloads/medneem/S3Drive/total?label=downloads&logo=github)](https://github.com/medneem/S3Drive/releases)
[![License](https://img.shields.io/github/license/medneem/S3Drive?label=license&color=informational)](LICENSE)

[![Windows](https://img.shields.io/badge/Windows-10%20%7C%2011-0078D4?logo=windows&logoColor=white)](https://github.com/medneem/S3Drive/releases/latest)
[![Python](https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![PySide6](https://img.shields.io/badge/PySide6-6.12-41CD52?logo=qt&logoColor=white)](https://doc.qt.io/qtforpython-6/)
[![rclone](https://img.shields.io/badge/rclone-1.76.0-3D85C6)](https://rclone.org/)
[![WinFsp](https://img.shields.io/badge/WinFsp-bundled-565E6A)](https://winfsp.dev/)
[![Portable](https://img.shields.io/badge/build-portable-7A46C5)](https://github.com/medneem/S3Drive/releases/latest)
[![Languages](https://img.shields.io/badge/languages-EN%20%7C%20RU-1EAD73)](README.ru.md)

**S3Drive** is an open-source portable app for Windows 10/11 x64 that mounts S3-compatible object storage as a Windows drive using [rclone](https://rclone.org/) and [WinFsp](https://winfsp.dev/). The GUI uses Python 3.13 and PySide6.

Documentation reflects **v1.5.6**. [Latest published release](https://github.com/medneem/S3Drive/releases/latest) · [S3Drive v1.5.6](https://github.com/medneem/S3Drive/releases/tag/v1.5.6).

## Features

- Multiple profiles with a display name, Endpoint, Access Key, Secret Key, region, path-style setting, optional bucket, and Windows drive letter. Works with HTTP(S) S3-compatible providers such as QNAP and MinIO.
- Leave Bucket blank to mount the S3 account root: accessible buckets become folders, provided the credentials allow `ListBuckets` / `s3:ListAllMyBuckets`.
- WinFsp network drives with a unique UNC path and the friendly profile name in Explorer, without exposing the internal network-share identifier in the display name.
- Ordinary interactive-user mounts, background mounts after closing the GUI, or Windows services that start automatically after reboot.
- rclone VFS write caching (`writes`), safe disconnect with upload-queue checks, and guarded cache clearing.
- An S3 **upload** progress window showing queued and active files, transferred bytes, speed, percentage, and remaining time. Windows Explorer provides the normal download UI; S3Drive's own monitor runs only while the GUI is open.
- Optional desktop shortcut and open-drive-after-connect actions.
- English (default) and Russian interface; choose a language in **Settings**.
- S3 credentials encrypted using Windows DPAPI in the portable `config.json`.
- A single GUI instance per Windows user session; a second launch activates the existing window. No tray icon.
- Opt-in installation of verified stable GitHub Releases using an Update button.

## Installation and first use

1. Download the [latest Windows x64 ZIP](https://github.com/medneem/S3Drive/releases/latest) and **extract the entire archive** into a writable folder. Do not launch from inside the ZIP.
2. Run `S3Drive.exe` as a **standard Windows user**, not permanently as administrator.
3. If WinFsp is missing, S3Drive offers to install the bundled `winfsp-2.2.26215.msi`. The MSI requires UAC. Finish installing WinFsp, then launch S3Drive again.
4. Create a profile: enter an S3 URL (`http://...` or `https://...`), access credentials, drive letter, and optionally a bucket name. QNAP S3 commonly needs **Path-style**. Use HTTP only on a trusted network.
5. Optionally select the shortcut, open-after-connect, or Windows service settings; click **Save**, then **Connect**.

The release already includes the SHA-256-verified **rclone 1.76.0** binary and WinFsp installer; do not install a separate rclone executable. See [README_SETUP.txt](README_SETUP.txt) for first-run instructions.

## Mount modes

| Mode | After closing S3Drive | After Windows reboot | Administrator consent |
| --- | --- | --- | --- |
| Ordinary user mount | Disconnects on normal GUI shutdown | No automatic reconnect | Not required |
| Keep drive connected | rclone remains active under the current user | No automatic reconnect | Not required |
| Windows service | Drive remains mounted and visible in Explorer | Service starts automatically | UAC for installation and administrative changes |

**Windows service.** Enable **Run as Windows service**, save the profile, and click **Connect**. On initial setup, the UAC-approved `S3DriveAdmin.exe` installs a per-profile `LocalSystem` service and a protected `S3DriveControl` controller. Once the profile owner is enrolled, normal **Connect** and **Stop service and drive** requests go through an SID-validated local named pipe **without repeated UAC**. Changing service settings, removing a service, and clearing a protected VFS cache still require UAC. Older installations may require one-time enrollment.

**Protected executables and secrets.** Per-profile rclone, the S3 configuration, and the rclone control password live in a restricted `%ProgramData%\S3Drive` directory. Since v1.5.6, installed service rclone executables use immutable names such as `rclone-<hash>.exe`. The previous image is never overwritten if Windows still has it mapped. The controller has a similar side-by-side upgrade mechanism. Updating the desktop app does **not** stop running services, replace their executables, or change their mount arguments; safely stop and reconnect the service in S3Drive to apply changed versions or settings.

**Security boundary.** A service-mounted drive can be accessed by other local Windows users. WinFsp uses `FileSecurity=D:P(A;;FA;;;WD)` (Everyone/full access) for the mounted files, subject to S3 backend permissions. Do not use service mode for buckets that must be isolated from other users of the same PC. Avoid stopping services directly via `services.msc` or `sc.exe`: that bypasses S3Drive's pending-upload checks.

**User background mode.** **Keep drive connected after closing the app** leaves rclone running in the current Windows session. On the next GUI launch, S3Drive can recover control via its authenticated local API; it does not automatically reconnect after a reboot. The control password in `%LOCALAPPDATA%\S3Drive\background.json` is DPAPI-protected.

## Configuration, drive labels, and data safety

`config.json` next to `S3Drive.exe` is the **only** profile store. It contains the language setting (`en` or `ru`), startup update-check setting, and profiles with DPAPI-encrypted S3 credentials. If missing, an empty configuration is created. The obsolete `%LOCALAPPDATA%\S3Drive\profiles.json` is no longer imported and is deleted at startup. Do not publish `config.json`; DPAPI-protected data generally cannot be decrypted by simply moving the file to another Windows installation or account.

Explorer displays the chosen profile name rather than the internal UNC hash. S3Drive also writes the user-visible network label in the user's `Explorer\MountPoints2` settings and passes the volume name through WinFsp mount options. Explorer may cache an older name until its view refreshes. Reconnect an existing Windows service after changing its mount settings.

S3 is **object storage, not NTFS**. Renames can involve copy-and-delete operations; concurrent mutations by other S3 clients are not atomic. When mounting an S3 account root, top-level folders represent **real buckets** and deleting such a folder may delete a bucket if permissions allow.

S3Drive uses `--s3-directory-markers` and `--dir-cache-time 0s` to handle marker objects and avoid stale directory listings. This can increase S3 LIST requests and **cannot guarantee** consistency during concurrent changes on the server.

Interactive and service mounts use `--vfs-cache-mode writes` and `--vfs-write-back 0s`. Before disconnecting, S3Drive checks pending, active, or failed uploads and refuses unsafe shutdown. **Never forcibly stop rclone or delete its VFS cache by hand.** **Clear cache** performs additional safety checks and refuses to clear an offline or crashed mount. Verify important deletions and uploads directly against the S3 server.

Do not use an S3 mount for live databases, active Git working trees, or files concurrently edited by multiple users.

## Updates

Stable releases check GitHub Releases by default when the app starts. In **Settings**, disable **Check for new versions at startup** or use **Check for updates** manually. Development builds with a `-dev.N` suffix do not perform automatic release checks. If the GitHub API is unavailable, S3Drive can fall back to the official Releases page and `S3Drive-update.json` manifest.

No update is downloaded until **Update** is clicked. The updater checks the ZIP's size, SHA-256, exact file set, and `VERSION.txt`. The separate `S3DriveUpdater.exe` waits for the GUI to exit, replaces only application files, and attempts rollback if replacement fails. `config.json`, installed services, their private rclone executables, and VFS caches are preserved. If a user background mount is active, installation waits for a safe disconnect. Older packages without `S3DriveUpdater.exe` require a full manual ZIP extraction while preserving `config.json`.

## Development and testing

- Python **3.13**, PySide6 **6.12.0**, pywin32 **312**; PyInstaller builds the GUI and updater, and Nuitka builds the elevated administrator helper and protected controller.
- GitHub Actions [Build Windows](https://github.com/medneem/S3Drive/actions/workflows/build-windows.yml) builds the portable Windows ZIP, runs the unittest suite **three times**, verifies Windows EXE metadata and hashes, and smoke-tests the `S3DriveControl` Windows SCM service.
- The separate [S3 deletion E2E workflow](https://github.com/medneem/S3Drive/actions/workflows/s3-deletion-e2e.yml) uses real WinFsp mounts with **an isolated local Moto S3 backend**, rather than a real QNAP server. See [docs/S3-DELETE-REPRO.md](docs/S3-DELETE-REPRO.md).

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-build.txt
python -m pip install -r requirements-admin-build.txt
python -m unittest discover -s tests -v
```

Pushing to `main` **does not create a Git tag or publish a release**. Publication requires explicit owner approval and a manual **Build Windows** workflow run with a `release_version` input (SemVer without `v`). See [GitHub Releases](https://github.com/medneem/S3Drive/releases) for change history and [docs/](docs/) for historical investigations.

## Antivirus detections

`S3DriveAdmin.exe` performs elevated Windows SCM operations. Behavior-based antivirus products may flag it (for example `PDM:Trojan.Win32.Generic`); the bundled upstream `rclone.exe` can be flagged separately. **A passing CI run or matching SHA-256 does not prove that a detection is false.** Keep antivirus enabled, compare files against the official release, and retain the detection report for review. If needed, consult [Kaspersky OpenTIP](https://opentip.kaspersky.com/) or the antivirus vendor. Never include `config.json` or S3 credentials in a report. The project's own executables are not currently publisher-code-signed.

## License

MIT — [LICENSE](LICENSE). Dependency notices: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and [RCLONE_LICENSE.txt](third_party/RCLONE_LICENSE.txt).
