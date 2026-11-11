# S3Drive

**English** | [Русский](README.ru.md)

S3Drive is an open-source, portable Windows application that mounts S3-compatible storage as a Windows drive using [rclone](https://rclone.org/) and [WinFsp](https://winfsp.dev/). Built with Python 3.13 and PySide6.

**Interface languages:** English (default) and Russian. Click **Settings → Interface language**, select a language, and save. The choice persists in config.json across launches and does not disconnect running drives.

## Features

- Multiple S3 connection profiles: endpoint, bucket, access and secret key, custom name, region, path-style option, and drive letter.
- S3-compatible HTTP(S) services including QNAP and MinIO.
- Optional bucket: leave empty to mount the S3 account root. Accessible buckets appear as folders, provided the credentials have ListBuckets / s3:ListAllMyBuckets permission.
- Windows network drive via WinFsp and rclone; custom volume name and a unique UNC path per mount.
- Explorer uses the configured profile name without showing the private UNC server identifier in parentheses. S3Drive stores the network-drive display name under the current Windows user's Explorer MountPoints2 registry entry; the unique UNC path and network-drive semantics remain unchanged.
- Windows service mode using LocalSystem, with Windows autostart. The drive can stay mounted independently of the GUI, including after a reboot.
- Optional user background mode, which keeps a drive mounted after closing S3Drive. This mode does not auto-reconnect after reboot.
- VFS writes cache, safe disconnect, and guarded cache cleanup that avoids discarding queued or unsent writes.
- Separate S3 upload monitor showing pending and active uploads, file names, progress, speed, and remaining time. Windows Explorer handles download progress.
- Optional desktop shortcut and opening the drive after a successful connection.
- Access Key and Secret Key encrypted with Windows DPAPI in config.json.
- Single-instance interface without a tray icon. A duplicate launch cannot start a second GUI; it asks the existing window to restore and come forward (Windows-session mutex and activation event).
- Update button appears when a newer stable GitHub Release is found. Downloads and installation require an explicit click; the official archive is SHA-256 verified. While downloading, the Update button shows a rotating spinner and percentage based on bytes received, followed by a distinct SHA-256 verification state.
- English and Russian interface and setup documentation.

## Requirements

- Windows 10 or 11, x64.
- WinFsp driver. The release ZIP contains the WinFsp installer; S3Drive offers to install it if missing.
- S3-compatible credentials and endpoint, and permission to create config.json beside S3Drive.exe.

rclone.exe is bundled with the portable ZIP. S3Drive pins the SHA-256 of
the official Windows rclone 1.76.0 executable and verifies it before launching
a drive or copying the binary into a Windows service. The Windows CI checks
this digest against the executable extracted from the checksum-verified
upstream archive. Missing or modified rclone is rejected; S3Drive does not
fall back to an unrelated `rclone.exe` on `PATH`.

### Antivirus detections

`S3DriveAdmin.exe` is a PyInstaller-packaged elevated helper that creates
Windows services and protects their executable/configuration files under
ProgramData. Behavior-based products such as Kaspersky may flag this activity
as `PDM:Trojan.Win32.Generic`; this is not by itself proof of either infection
or a false positive. The upstream rclone executable may be flagged separately
as `Backdoor.Win64.Freyja.bc`. Keep antivirus protection enabled, preserve its
event report, and do not restore or execute quarantined files until reviewed.
Check the full official S3Drive Release ZIP and hashes, and submit both
samples and detection logs to [Kaspersky OpenTIP](https://opentip.kaspersky.com/)
or Kaspersky technical support for independent analysis. A release hash or
successful unit test is not an antivirus verdict. Code signing is recommended
for future releases, but requires a genuine publisher certificate.

## Installation and first use

1. Download the latest ZIP from [GitHub Releases](https://github.com/medneem/S3Drive/releases) and extract **all files** to one writable folder. Do not run S3Drive directly inside the ZIP.
2. Start S3Drive.exe as a normal Windows user, not as administrator.
3. If WinFsp is missing, accept the install prompt, finish installing the bundled MSI, and run S3Drive again.
4. Enter an Endpoint, Access Key, Secret Key, optional Bucket, and drive letter. Enable Path-style for servers that require it.
5. Select the desired shortcut, open-after-mount, and background/service options.
6. Click **Save**, then **Connect** and wait for the drive to appear in Explorer.
7. Open **Settings** to select English or Русский; the setting is remembered.

For step-by-step help see [README_SETUP.txt](README_SETUP.txt) (English) and [README_SETUP_RU.txt](README_SETUP_RU.txt) (Russian).

## Configuration and language

A portable config.json lives beside S3Drive.exe. It stores both profiles and the UI language. The top-level language setting is "en" by default or "ru" for Russian. Older config.json files without this property automatically use English; existing profiles remain unchanged.

Connection keys are stored encrypted using Windows DPAPI. Do not publish your config.json. DPAPI encryption is tied to Windows protection and cannot simply be decrypted on another machine/account. Changing the UI language does not touch the mounted drive or service state.

## Windows service and background mounts

**Run as Windows service** installs a LocalSystem service that can start automatically with Windows and keep the drive available in Explorer while S3Drive is closed. UAC confirmation is required for service administration. Use **Stop service and drive** for safe shutdown, then **Remove service** if it is no longer needed; removing a stopped service keeps the saved profile.

An already-running service does not automatically receive new mount options after an application update. To apply them, first close edited files, wait for S3 transfers, and disconnect/reconnect via S3Drive. Do not force-stop or delete the cache of a service with pending uploads.

**Keep drive connected after closing the app** keeps an ordinary user mount in the background without installing a service. This mode does not provide reconnection after a Windows reboot. S3Drive does not display a tray icon or transfer popup after the GUI closes.

## S3 filesystem behavior and safety

S3 is object storage, not NTFS. A "folder" may be an object prefix or a zero-byte directory marker. S3Drive configures rclone to handle directory markers and has S3 directory deletion regression tests, but server permissions and S3 semantics still apply.

In an account-root mount (bucket left blank), each top-level folder represents a **real bucket**. Creating or deleting such a folder can create or delete a bucket, if the account has permission. Use caution.

The VFS write cache can hold data that has not yet reached S3. Use **Clear cache** in the app only after pending transfers have been checked. Do not manually delete cached files while any upload may be outstanding. Avoid storing live databases or active Git working trees on S3 mounts.

## Updating S3Drive

By default, S3Drive checks for a newer stable GitHub Release at startup. In **Settings** you can disable **Check for new versions at startup** (saved in `config.json`), or click **Check for updates** whenever you want, even when automatic checks are disabled. When `api.github.com` is unreachable, the updater checks the official GitHub Releases page instead. If a newer version exists, it reads the `S3Drive-update.json` sidecar asset from that release to obtain the exact ZIP SHA-256. It never installs a ZIP when verified metadata is missing. A failed automatic check is retried after five minutes; manual check errors are shown in a dialog. Offline S3 mounts are unaffected. **Update** appears in the main window only when a newer release is found; nothing downloads automatically.

The packaged rclone version is stable **1.76.0**, which fixes deletion of account-root buckets when S3 directory markers are enabled.

After confirmation, S3Drive downloads and verifies the official release ZIP, closes safely, and launches S3DriveUpdater.exe. The GUI restores Qt's normal last-window shutdown setting and schedules a clean quit before the updater waits for the old PID to exit. If an update fails, the window is shown again. The updater preserves config.json, service-specific rclone binaries, and VFS caches. If a user background mount is active, the update can be postponed until it is safely disconnected.

Older S3Drive distributions without S3DriveUpdater.exe require extracting a full new ZIP manually while preserving config.json.

## Development and testing

The project runs on Python 3.13 and PySide6. Install dependencies using requirements-build.txt, then run:

    python -m unittest discover -s tests -v
    python -m compileall -q main.py localization.py profiles.py tests

GitHub Actions builds a Windows x64 portable ZIP and runs tests in three verification passes. Pushes to main **do not create releases**; releases are published only after an explicitly requested manual workflow. The repository also includes separate Windows/S3 directory-deletion integration tests.

For technical details see the [docs directory](docs/), [third-party notices](THIRD_PARTY_NOTICES.md), [rclone license](third_party/RCLONE_LICENSE.txt), and [LICENSE](LICENSE).

Project: https://github.com/medneem/S3Drive
