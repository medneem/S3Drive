"""Russian-language Windows GUI for managing S3 mounts through rclone and WinFsp."""
from __future__ import annotations

import os
import sys
import time
import uuid
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QObject, QRunnable, QThreadPool, Signal
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFormLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QPushButton, QProgressBar, QSystemTrayIcon, QMenu, QVBoxLayout, QWidget,
)

from dependencies import winfsp_installed, winfsp_installer
from background import sessions_to_stop
from drive_actions import (MountState, create_desktop_shortcut, disk_root,
                           mount_state, open_mounted_disk)
from service_ui import run_admin
from drive_labels import notify_explorer, read_native_volume_label
from windows_service import service_status, SERVICE_RUNNING, SERVICE_STOPPED
from startup import StartupStatus, ensure_winfsp
from single_instance import SingleInstance
from mounts import MountManager
from profiles import Profile, ProfileStore, app_data_dir, config_path
from version import APP_VERSION
from transfer_monitor import read_user_snapshot, read_service_snapshot, TransferSnapshot
from tray_startup import get_tray_autostart, set_tray_autostart


def app_icon() -> QIcon:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    renderer = QSvgRenderer(str(root / "assets" / "app.svg"))
    if not renderer.isValid():
        return QIcon()
    pixmap = QPixmap(256, 256)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter)
    painter.end()
    return QIcon(pixmap)


class _MonitorSignals(QObject):
    finished = Signal(str, object, str)


class _MonitorTask(QRunnable):
    """Polling runs away from the Qt GUI thread (RC calls can take seconds)."""

    def __init__(self, profile: Profile, manager: MountManager):
        super().__init__()
        self.profile = profile
        self.manager = manager
        self.signals = _MonitorSignals()

    def run(self):
        try:
            if self.profile.service_mode:
                state = read_service_snapshot(self.profile.id)
            else:
                state = read_user_snapshot(self.manager, self.profile.id)
            self.signals.finished.emit(self.profile.id, state, "")
        except Exception as exc:
            self.signals.finished.emit(self.profile.id, None, str(exc))


class TransferWindow(QDialog):
    """Отдельное немодальное окно. Открывается только при новой передаче."""

    dismissed = Signal(str)

    def __init__(self, profile: Profile, parent: QWidget | None = None):
        super().__init__(parent)
        self.profile_id = profile.id
        self.setWindowTitle(f"Загрузка в S3 — {profile.name} ({profile.drive})")
        self.setWindowFlag(Qt.WindowType.Tool, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setMinimumWidth(390)
        layout = QVBoxLayout(self)
        self.filename = QLabel("Идёт загрузка в S3…")
        self.filename.setWordWrap(True)
        layout.addWidget(self.filename)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        layout.addWidget(self.progress)
        self.details = QLabel()
        self.details.setWordWrap(True)
        layout.addWidget(self.details)

    def display(self, snapshot: TransferSnapshot):
        self.filename.setText(
            f"Файл: {snapshot.current_file[-120:]}"
            if snapshot.current_file else "Ожидание загрузки файлов в S3…"
        )
        self.progress.setVisible(bool(snapshot.current_file))
        self.progress.setValue(snapshot.percent)
        lines = [f"Активных передач: {snapshot.uploading}"]
        if snapshot.queued:
            lines.append(f"В очереди: {snapshot.queued}")
        if snapshot.speed:
            lines.append(f"Скорость: {snapshot.speed / 1048576:.2f} МиБ/с")
        if snapshot.errors:
            lines.append(f"Ошибки: {snapshot.errors}")
        self.details.setText("  |  ".join(lines))

    def closeEvent(self, event):
        self.dismissed.emit(self.profile_id)
        super().closeEvent(event)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"S3Drive v{APP_VERSION}")
        self.setMinimumSize(820, 490)
        self.resize(940, 560)
        self.store = ProfileStore()
        self.mounts = MountManager()
        self.settings_load_error = False
        try:
            self.profiles = self.store.load()
        except Exception as exc:
            self.settings_load_error = True
            self.profiles = []
            QMessageBox.critical(self, "Не удалось открыть config.json",
                f"{exc}\n\nЧтобы избежать потери профилей, сохранение настроек заблокировано. "
                "Исправьте файл config.json и перезапустите S3Drive.")

        try:
            self.mounts.recover(self.profiles)
        except Exception as exc:
            QMessageBox.warning(
                self, "Фоновые подключения",
                f"Не удалось восстановить управление фоновыми дисками: {exc}\n"
                "Существующие диски не отключены. Проверьте состояние перед подключением.",
            )

        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        header = QHBoxLayout()
        title = QLabel("S3Drive — подключение S3 как диска Windows")
        title.setStyleSheet("font-size: 18px; font-weight: 600;")
        header.addWidget(title, 1)
        header.addWidget(QLabel(f"v{APP_VERSION}"))
        layout.addLayout(header)

        columns = QHBoxLayout()
        self.items = QListWidget()
        self.items.setMinimumWidth(240)
        self.items.currentItemChanged.connect(self.select_profile)
        columns.addWidget(self.items, 2)

        right = QVBoxLayout()
        group = QGroupBox("Параметры S3")
        fields = QFormLayout(group)
        self.name = QLineEdit()
        self.name.setPlaceholderText("QNAP S3")
        self.endpoint = QLineEdit()
        self.endpoint.setPlaceholderText("http://nas.local:8010")
        self.bucket = QLineEdit()
        self.bucket.setPlaceholderText("Пусто — показать все доступные бакеты")
        self.bucket.setToolTip(
            "Необязательно. Без имени бакета монтируется корень S3: "
            "в Проводнике отображаются доступные бакеты как папки. "
            "Ключи должны разрешать ListBuckets."
        )
        self.access = QLineEdit()
        self.access.setEchoMode(QLineEdit.EchoMode.Password)
        self.secret = QLineEdit()
        self.secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.drive = QComboBox()
        self.drive.addItems([f"{chr(n)}:" for n in range(ord("D"), ord("Z") + 1)])
        self.drive.setCurrentText("S:")
        self.path_style = QCheckBox("Path-Style (для QNAP / MinIO)")
        self.path_style.setChecked(True)
        self.region = QLineEdit("us-east-1")
        for label, widget in (
            ("Название:", self.name), ("Endpoint:", self.endpoint), ("Бакет (необязательно):", self.bucket),
            ("Access Key:", self.access), ("Secret Key:", self.secret),
            ("Буква диска:", self.drive), ("Регион:", self.region),
        ):
            fields.addRow(label, widget)
        fields.addRow("", self.path_style)
        self.shortcut_checkbox = QCheckBox("Создать ярлык на рабочем столе")
        self.open_checkbox = QCheckBox("Открыть диск после подключения")
        fields.addRow("", self.shortcut_checkbox)
        fields.addRow("", self.open_checkbox)
        self.background_checkbox = QCheckBox("Оставлять диск подключённым после закрытия программы")
        self.background_checkbox.setToolTip(
            "rclone продолжит работать от вашего имени в фоне. "
            "Это не служба Windows, и после перезагрузки автоподключения пока нет."
        )
        fields.addRow("", self.background_checkbox)
        self.service_checkbox = QCheckBox("Запускать как службу Windows (доступно в Проводнике)")
        self.service_checkbox.setToolTip(
            "Реальная служба LocalSystem с автозапуском Windows. "
            "Установка и остановка службы требуют подтверждения UAC."
        )
        fields.addRow("", self.service_checkbox)
        self.service_checkbox.toggled.connect(
            lambda checked: self.background_checkbox.setChecked(False) if checked else None
        )
        self.background_checkbox.toggled.connect(
            lambda checked: self.service_checkbox.setChecked(False) if checked else None
        )
        right.addWidget(group, 1)

        self.status = QLabel("Выберите профиль или создайте новый.")
        self.status.setWordWrap(True)
        right.addWidget(self.status)
        columns.addLayout(right, 4)
        layout.addLayout(columns, 1)

        actions = QHBoxLayout()
        self.add_button = QPushButton("Новый")
        self.save_button = QPushButton("Сохранить")
        self.remove_button = QPushButton("Удалить")
        self.mount_button = QPushButton("Подключить")
        self.unmount_button = QPushButton("Отключить")
        self.remove_service_button = QPushButton("Удалить службу")
        self.clear_cache_button = QPushButton("Очистить кэш")
        self.clear_cache_button.setToolTip(
            "После подтверждения проверяет отсутствие незавершённых загрузок, "
            "отключает диск и очищает его локальный VFS-кэш. "
            "При незавершённых передачах или ошибках ничего не удаляет."
        )
        actions.addWidget(self.add_button)
        actions.addWidget(self.save_button)
        actions.addWidget(self.remove_button)
        actions.addStretch()
        actions.addWidget(self.clear_cache_button)
        actions.addWidget(self.remove_service_button)
        actions.addWidget(self.unmount_button)
        actions.addWidget(self.mount_button)
        layout.addLayout(actions)

        self.add_button.clicked.connect(self.new_profile)
        self.save_button.clicked.connect(self.save_profile)
        self.remove_button.clicked.connect(self.remove_profile)
        self.mount_button.clicked.connect(self.mount_profile)
        self.unmount_button.clicked.connect(self.unmount_profile)
        self.remove_service_button.clicked.connect(self.remove_service_profile)
        self.clear_cache_button.clicked.connect(self.clear_cache_profile)
        self.pending_mounts: dict[str, tuple[Profile, float]] = {}
        self.refresh_items()
        self.new_profile()
        if self.profiles:
            self.items.setCurrentRow(0)
        self.mount_timer = QTimer(self)
        self.mount_timer.timeout.connect(self.poll_mounts)
        self.mount_timer.start(500)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_status)
        self.timer.start(1500)
        self.monitor_pending = False
        self.monitor_index = 0
        self.uploads_seen: set[str] = set()
        self.dismissed_uploads: set[str] = set()
        self.transfer_popup: TransferWindow | None = None
        self.transfer_popup_profile: str | None = None
        self.idle_polls = 0
        self.monitor_last_error = ""
        self.monitor_timer = QTimer(self)
        self.monitor_timer.timeout.connect(self.poll_transfers)
        self.monitor_timer.start(1500)
        self.quitting = False
        self.tray_notice_shown = False
        self.tray: QSystemTrayIcon | None = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(app_icon(), self)
            self.tray.setToolTip("S3Drive — монитор загрузки в S3")
            menu = QMenu(self)
            open_action = menu.addAction("Открыть S3Drive")
            open_action.triggered.connect(self.restore_window)
            self.autostart_action = menu.addAction("Запускать монитор вместе с Windows")
            self.autostart_action.setCheckable(True)
            try:
                self.autostart_action.setChecked(get_tray_autostart())
            except OSError:
                self.autostart_action.setChecked(False)
            self.autostart_action.toggled.connect(self.toggle_autostart)
            menu.addSeparator()
            exit_action = menu.addAction("Выход из S3Drive")
            exit_action.triggered.connect(self.exit_from_tray)
            self.tray.setContextMenu(menu)
            self.tray.activated.connect(self.on_tray_activated)
            self.tray.show()
            QApplication.instance().setQuitOnLastWindowClosed(False)
        self.statusBar().showMessage(f"Конфигурация: {config_path()} | Кэш и журналы: {app_data_dir()}")

    def restore_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.restore_window()

    def toggle_autostart(self, checked: bool):
        try:
            set_tray_autostart(checked)
        except OSError as exc:
            self.autostart_action.blockSignals(True)
            self.autostart_action.setChecked(not checked)
            self.autostart_action.blockSignals(False)
            QMessageBox.warning(self, "Автозапуск S3Drive", str(exc))

    def exit_from_tray(self):
        self.quitting = True
        if self.close():
            if self.tray is not None:
                self.tray.hide()
            QApplication.quit()
        else:
            self.quitting = False

    def selected_id(self) -> str | None:
        item = self.items.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def selected_profile(self) -> Profile | None:
        return next((p for p in self.profiles if p.id == self.selected_id()), None)

    def refresh_items(self, preferred_id: str | None = None):
        self.items.blockSignals(True)
        self.items.clear()
        for profile in self.profiles:
            item = QListWidgetItem(f"{profile.drive}  {profile.name}")
            item.setData(Qt.ItemDataRole.UserRole, profile.id)
            self.items.addItem(item)
            if profile.id == preferred_id:
                self.items.setCurrentItem(item)
        self.items.blockSignals(False)

    def select_profile(self, current, previous=None):
        profile = self.selected_profile()
        if profile is None:
            return
        self.name.setText(profile.name)
        self.endpoint.setText(profile.endpoint)
        self.bucket.setText(profile.bucket)
        self.access.setText(profile.access_key)
        self.secret.setText(profile.secret_key)
        self.drive.setCurrentText(profile.drive)
        self.path_style.setChecked(profile.path_style)
        self.shortcut_checkbox.setChecked(profile.create_shortcut)
        self.open_checkbox.setChecked(profile.open_after_mount)
        self.background_checkbox.setChecked(profile.keep_running)
        self.service_checkbox.setChecked(profile.service_mode)
        self.region.setText(profile.region)
        self.update_status()

    def new_profile(self):
        self.items.clearSelection()
        self.items.setCurrentRow(-1)
        for widget in (self.endpoint, self.bucket, self.access, self.secret):
            widget.clear()
        self.name.setText("Личный сетевой диск")
        self.drive.setCurrentText("S:")
        self.path_style.setChecked(True)
        self.shortcut_checkbox.setChecked(True)
        self.open_checkbox.setChecked(False)
        self.background_checkbox.setChecked(False)
        self.service_checkbox.setChecked(True)
        self.region.setText("us-east-1")
        self.status.setText("Новый профиль. Заполните поля и нажмите «Сохранить».")

    def draft(self) -> Profile:
        profile = Profile(
            id=self.selected_id() or str(uuid.uuid4()),
            name=self.name.text().strip(), endpoint=self.endpoint.text().strip(),
            bucket=self.bucket.text().strip(), access_key=self.access.text().strip(),
            secret_key=self.secret.text(), drive=self.drive.currentText(),
            path_style=self.path_style.isChecked(), region=self.region.text().strip(),
            create_shortcut=self.shortcut_checkbox.isChecked(),
            open_after_mount=self.open_checkbox.isChecked(),
            keep_running=self.background_checkbox.isChecked(),
            service_mode=self.service_checkbox.isChecked(),
        )
        profile.validate()
        return profile

    def save_profile(self) -> bool:
        if self.settings_load_error:
            QMessageBox.critical(self, "Ошибка config.json",
                "Сохранение заблокировано из-за ошибки чтения config.json. "
                "Исправьте конфигурацию и перезапустите программу.")
            return False
        try:
            profile = self.draft()
            existing = next((p for p in self.profiles if p.id == profile.id), None)
            mounted = self.mounts.is_mounted(profile.id)
            svc = service_status(profile.id)
            if svc is not None and existing is not None and profile.drive != existing.drive:
                raise ValueError(
                    "Чтобы сменить букву диска, сначала остановите и удалите старую службу."
                )
            if svc is not None and existing is not None and profile.service_mode != existing.service_mode:
                if svc == SERVICE_RUNNING:
                    raise ValueError("Остановите службу перед сменой режима подключения.")
            if svc == SERVICE_RUNNING and existing is not None:
                service_fields = (
                    "name", "endpoint", "bucket", "access_key", "secret_key",
                    "drive", "path_style", "region",
                )
                if any(getattr(existing, field) != getattr(profile, field)
                       for field in service_fields):
                    raise ValueError("Остановите службу перед изменением её настроек S3.")
            if mounted and existing is not None:
                fixed_fields = (
                    "name", "endpoint", "bucket", "access_key", "secret_key",
                    "drive", "path_style", "region",
                )
                if any(getattr(existing, key) != getattr(profile, key) for key in fixed_fields):
                    raise ValueError("Отключите диск перед изменением настроек S3.")
            updated = [p for p in self.profiles if p.id != profile.id] + [profile]
            if len({p.drive for p in updated}) != len(updated):
                raise ValueError("Буква диска должна быть уникальной для каждого профиля.")
            previous_background = self.mounts.keep_running(profile.id) if mounted else False
            if mounted and previous_background != profile.keep_running:
                self.mounts.set_keep_running(profile.id, profile.keep_running)
            try:
                self.store.save(updated)
            except Exception:
                if mounted and previous_background != profile.keep_running:
                    self.mounts.set_keep_running(profile.id, previous_background)
                raise
            self.profiles = updated
            if mounted:
                self.mounts.sessions[profile.id].profile = profile
            self.refresh_items(profile.id)
            self.status.setText("Настройки сохранены (ключи защищены Windows DPAPI).")
            return True
        except Exception as exc:
            QMessageBox.warning(self, "Не удалось сохранить профиль", str(exc))
            return False

    def remove_profile(self):
        if self.settings_load_error:
            QMessageBox.warning(self, "Ошибка config.json", "Удаление заблокировано из-за ошибки чтения конфигурации.")
            return
        profile = self.selected_profile()
        if profile is None:
            return
        if self.mounts.is_mounted(profile.id) or service_status(profile.id) is not None:
            QMessageBox.warning(self, "Подключение существует",
                "Сначала отключите диск и удалите соответствующую службу Windows.")
            return
        if QMessageBox.question(self, "Удалить профиль", f"Удалить «{profile.name}»?") != QMessageBox.StandardButton.Yes:
            return
        try:
            updated = [p for p in self.profiles if p.id != profile.id]
            self.store.save(updated)
            self.profiles = updated
            self.refresh_items()
            self.new_profile()
        except Exception as exc:
            QMessageBox.warning(self, "Ошибка удаления", str(exc))

    def remove_service_profile(self):
        profile = self.selected_profile()
        if profile is None:
            return
        state = service_status(profile.id)
        if state is None:
            return
        if state != SERVICE_STOPPED:
            QMessageBox.warning(
                self, "Служба работает",
                "Сначала нажмите «Остановить службу и диск» и дождитесь отключения.",
            )
            return
        if QMessageBox.question(
            self, "Удалить службу",
            f"Удалить службу для диска {profile.drive}?\nНастройки подключения в config.json сохранятся.",
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            run_admin("remove", profile.id)
            notify_explorer()
            QMessageBox.information(self, "S3Drive", "Служба удалена.")
        except Exception as exc:
            QMessageBox.warning(self, "Удаление службы", str(exc))
        self.update_status()

    def mount_profile(self):
        if not self.save_profile():
            return
        profile = self.selected_profile()
        if profile is None:
            return
        if not winfsp_installed():
            QMessageBox.warning(
                self, "WinFsp не установлен",
                "Для монтирования нужен системный драйвер WinFsp. "
                "Установите драйвер и запустите S3Drive повторно.",
            )
            return
        try:
            if profile.service_mode:
                if self.mounts.is_mounted(profile.id):
                    raise RuntimeError("Сначала отключите пользовательское монтирование.")
                if service_status(profile.id) == SERVICE_RUNNING:
                    QMessageBox.information(self, "S3Drive", "Служба уже запущена.")
                    return
                run_admin("install", profile.id)
                notify_explorer()
                self.pending_mounts[profile.id] = (profile, time.monotonic())
                self.status.setText(f"Подключение службы и диска {profile.drive}…")
            else:
                if service_status(profile.id) is not None:
                    raise RuntimeError(
                        "Для этой записи установлена служба Windows. "
                        "Остановите и удалите службу перед обычным подключением."
                    )
                self.mounts.start(profile)
                self.pending_mounts[profile.id] = (profile, time.monotonic())
                self.status.setText(f"Подключение диска {profile.drive}…")

        except Exception as exc:
            QMessageBox.warning(self, "Не удалось подключить S3", str(exc))
        self.update_status()

    def unmount_profile(self):
        profile = self.selected_profile()
        if profile is None:
            return
        try:
            state = service_status(profile.id)
            if state is not None:
                if state == SERVICE_STOPPED:
                    QMessageBox.information(self, "S3Drive", "Служба уже остановлена.")
                else:
                    run_admin("stop", profile.id)
                    notify_explorer()
            else:
                self.mounts.stop(profile.id)
            self.pending_mounts.pop(profile.id, None)
            self.status.setText(f"Диск {profile.drive} отключён.")
        except Exception as exc:
            QMessageBox.warning(self, "Не удалось отключить диск", str(exc))
        self.update_status()

    def poll_mounts(self) -> None:
        """Only notify and run optional actions once the drive appears in Explorer."""
        for profile_id, (profile, started_at) in list(self.pending_mounts.items()):
            alive = (service_status(profile_id) == SERVICE_RUNNING
                     if profile.service_mode else self.mounts.is_mounted(profile_id))
            ready = alive and os.path.isdir(disk_root(profile.drive))
            state = mount_state(
                process_alive=alive, drive_ready=ready,
                elapsed_seconds=time.monotonic() - started_at,
            )
            if state == MountState.WAITING:
                continue
            self.pending_mounts.pop(profile_id, None)
            if state == MountState.READY:
                problems = []
                if profile.create_shortcut:
                    try:
                        create_desktop_shortcut(profile.drive, profile.name)
                    except Exception as exc:
                        problems.append(f"Ярлык на рабочем столе не создан: {exc}")
                if profile.open_after_mount:
                    try:
                        open_mounted_disk(profile.drive)
                    except Exception as exc:
                        problems.append(f"Не удалось открыть диск: {exc}")
                message = f"Диск {profile.drive} успешно подключён."
                native_label = read_native_volume_label(profile.drive)
                if native_label and native_label != profile.name:
                    message += (f"\nСистемная метка WinFsp: {native_label}. "
                                "Полное имя дополнительно задано для Проводника Windows.")
                if problems:
                    message += "\n\n" + "\n".join(problems)
                QMessageBox.information(self, "S3Drive", message)
            elif state == MountState.FAILED:
                QMessageBox.warning(
                    self, "Ошибка подключения",
                    f"Не удалось подключить диск {profile.drive}: rclone или служба завершилась.\n"
                    f"Подробности: {app_data_dir() / 'logs' / (profile.id + '.log')}",
                )
            else:
                QMessageBox.warning(
                    self, "Подключение не подтверждено",
                    f"Диск {profile.drive} не появился в Windows за 40 секунд.\n"
                    "Не отключайте компьютер во время записи в S3.\n"
                    "rclone всё ещё работает; проверьте журнал и состояние диска.",
                )
        self.update_status()

    def clear_cache_profile(self):
        profile = self.selected_profile()
        if profile is None:
            return
        svc_state = service_status(profile.id)
        active = svc_state == SERVICE_RUNNING or self.mounts.is_mounted(profile.id)
        if not active:
            QMessageBox.warning(
                self, "Очистка кэша",
                "Для безопасной очистки диск должен быть подключён. "
                "После аварийного завершения в кэше могут остаться неотправленные "
                "файлы, поэтому удалять его без проверки нельзя.",
            )
            return
        answer = QMessageBox.question(
            self, "Очистить кэш",
            f"Проверить загрузки, отключить диск {profile.drive} "
            "и удалить локальный кэш?\n\n"
            "Это отключит диск до следующего ручного подключения. "
            "Если есть незавершённые загрузки, открытые файлы или ошибки, "
            "операция будет отменена.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            if svc_state is not None:
                run_admin("clear-cache", profile.id)
            else:
                self.mounts.clear_cache(profile.id)
            self.pending_mounts.pop(profile.id, None)
            self.uploads_seen.discard(profile.id)
            self.hide_transfer_popup()
            QMessageBox.information(
                self, "Кэш очищен",
                f"Локальный кэш {profile.name} очищен. Диск отключён. "
                "Подключите его снова при необходимости.",
            )
        except Exception as exc:
            QMessageBox.warning(self, "Кэш не очищен", str(exc))
        self.update_status()

    def hide_transfer_popup(self):
        if self.transfer_popup is not None:
            self.transfer_popup.dismissed.disconnect(self.dismiss_current_transfer)
            self.transfer_popup.close()
            self.transfer_popup.deleteLater()
            self.transfer_popup = None
            self.transfer_popup_profile = None
        self.idle_polls = 0

    def dismiss_current_transfer(self, profile_id: str):
        # closeEvent is already executing; closing the same dialog recursively
        # could cause a repeated Qt callback or a flashing window.
        self.dismissed_uploads.add(profile_id)
        if self.transfer_popup is not None:
            self.transfer_popup.deleteLater()
        self.transfer_popup = None
        self.transfer_popup_profile = None
        self.idle_polls = 0

    def poll_transfers(self):
        if self.monitor_pending:
            return
        # Мониторить все работающие диски, а не только выделенную запись:
        # при скрытом окне выбранный профиль может вообще не быть активным.
        active_profiles = [
            p for p in self.profiles
            if (service_status(p.id) == SERVICE_RUNNING
                if p.service_mode else self.mounts.is_mounted(p.id))
        ]
        if not active_profiles:
            self.hide_transfer_popup()
            return
        if (self.transfer_popup is not None and not any(
                p.id == self.transfer_popup_profile for p in active_profiles)):
            self.hide_transfer_popup()
        if (self.transfer_popup_profile and
                any(p.id == self.transfer_popup_profile for p in active_profiles)):
            profile = next(p for p in active_profiles
                           if p.id == self.transfer_popup_profile)
        else:
            profile = active_profiles[self.monitor_index % len(active_profiles)]
            self.monitor_index += 1
        self.monitor_pending = True
        task = _MonitorTask(profile, self.mounts)
        self.monitor_task = task
        task.signals.finished.connect(self.on_transfers)
        QThreadPool.globalInstance().start(task)

    def on_transfers(self, profile_id: str, snapshot: TransferSnapshot | None, error: str):
        self.monitor_pending = False
        profile = next((p for p in self.profiles if p.id == profile_id), None)
        if profile is None:
            return
        if snapshot is None:
            if error != self.monitor_last_error:
                self.monitor_last_error = error
                self.statusBar().showMessage("Монитор загрузок: " + error[:170], 12000)
            # Недостоверная статистика не должна скрывать уже открытую передачу.
            return
        self.monitor_last_error = ""
        transferring = bool(snapshot.queued or snapshot.uploading)
        if transferring:
            self.idle_polls = 0
            self.uploads_seen.add(profile_id)
            if profile_id in self.dismissed_uploads:
                return
            if self.transfer_popup is None:
                self.transfer_popup = TransferWindow(profile, None)
                self.transfer_popup_profile = profile_id
                self.transfer_popup.dismissed.connect(self.dismiss_current_transfer)
                self.transfer_popup.show()
            self.transfer_popup.display(snapshot)
        else:
            self.idle_polls += 1
            if self.idle_polls < 2:
                return
            self.hide_transfer_popup()
            self.dismissed_uploads.discard(profile_id)
            if snapshot.errors:
                self.statusBar().showMessage(
                    "S3Drive: обнаружены ошибки передачи. Проверьте журнал rclone.", 15000
                )
            elif profile_id in self.uploads_seen:
                self.uploads_seen.discard(profile_id)
                self.statusBar().showMessage("S3Drive: загрузка файлов завершена", 8000)

    def update_status(self):
        profile = self.selected_profile()
        if profile is None:
            self.remove_service_button.setEnabled(False)
            self.clear_cache_button.setEnabled(False)
            self.unmount_button.setEnabled(False)
            self.mount_button.setEnabled(True)
            return
        service = service_status(profile.id)
        service_running = service == SERVICE_RUNNING
        user_running = self.mounts.is_mounted(profile.id)
        active = service_running or user_running
        ready = active and os.path.isdir(disk_root(profile.drive))
        self.remove_service_button.setEnabled(service == SERVICE_STOPPED)
        self.clear_cache_button.setEnabled(active)
        self.unmount_button.setEnabled(active)
        self.unmount_button.setText(
            "Остановить службу и диск" if service is not None
            else "Отключить фон и диск" if user_running and self.mounts.keep_running(profile.id)
            else "Отключить диск"
        )
        self.mount_button.setEnabled(not active)
        if profile.id in self.pending_mounts:
            text = "Подключение…"
        elif service_running:
            text = "Служба работает; диск в Проводнике" if ready else "Служба запущена; диск не обнаружен"
        elif service == SERVICE_STOPPED:
            text = "Служба установлена, остановлена"
        elif user_running:
            text = "Подключён (в фоне)" if self.mounts.keep_running(profile.id) else "Подключён"
        else:
            text = "Отключён"
        self.status.setText(f"{profile.drive}: {text}")

    def closeEvent(self, event):
        # Keep a GUI monitor in the interactive session even when only the
        # LocalSystem Windows service is mounting disks. Services in Session 0
        # cannot display user-facing windows.
        if self.tray is not None and self.tray.isVisible() and not self.quitting:
            event.ignore()
            self.hide()
            if not self.tray_notice_shown:
                self.tray.showMessage(
                    "S3Drive работает в фоне",
                    "Монитор загрузок продолжает работать. Открыть программу "
                    "можно через значок в системном трее.",
                    QSystemTrayIcon.MessageIcon.Information, 5000,
                )
                self.tray_notice_shown = True
            return
        running = [
            self.mounts.sessions[p.id] for p in self.profiles
            if self.mounts.is_mounted(p.id)
        ]
        to_stop = sessions_to_stop(running)
        if to_stop:
            answer = QMessageBox.question(
                self, "Завершение S3Drive",
                "Отключить диски без фонового режима и закрыть программу?\n"
                "Фоновые подключения останутся активными.\n\n"
                "Перед отключением закройте изменяемые файлы и дождитесь загрузки в S3.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            for profile_id in to_stop:
                try:
                    self.mounts.stop(profile_id)
                except Exception as exc:
                    QMessageBox.warning(self, "Не удалось отключить диск", str(exc))
                    event.ignore()
                    return
        super().closeEvent(event)


def startup_ready() -> bool:
    """Prevent the main window from opening until WinFsp is installed."""
    def ask_install() -> bool:
        answer = QMessageBox.question(
            None,
            "Требуется WinFsp",
            "Для подключения S3 как диска Windows необходим драйвер WinFsp.\n\n"
            "Драйвер не обнаружен. Установить его сейчас?\n"
            "Для установки понадобятся права администратора.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        return answer == QMessageBox.StandardButton.Yes

    outcome = ensure_winfsp(
        winfsp_installed, winfsp_installer(),
        ask_install, lambda installer: os.startfile(str(installer)),
    )
    if outcome.status == StartupStatus.READY:
        return True
    if outcome.status == StartupStatus.INSTALLER_STARTED:
        QMessageBox.information(
            None, "Установка WinFsp",
            "Установщик WinFsp запущен.\n\n"
            "Завершите установку, затем запустите S3Drive повторно. "
            "Программа сейчас закроется.",
        )
    elif outcome.status == StartupStatus.DECLINED:
        QMessageBox.warning(
            None, "WinFsp необходим",
            "Без драйвера WinFsp S3Drive не сможет подключать диски.\n\n"
            "Программа будет закрыта.",
        )
    elif outcome.status == StartupStatus.INSTALLER_MISSING:
        QMessageBox.critical(
            None, "Не найден WinFsp",
            "Драйвер WinFsp не установлен, а установщик отсутствует рядом с S3Drive.exe.\n\n"
            "Скачайте полный архив из GitHub Releases и распакуйте его целиком.\n"
            "Без драйвера работа невозможна. Программа будет закрыта.",
        )
    elif outcome.status == StartupStatus.INSTALLER_FAILED:
        QMessageBox.critical(
            None, "Ошибка запуска WinFsp",
            f"Не удалось открыть установщик WinFsp: {outcome.error}\n\n"
            "Без драйвера работа невозможна. Программа будет закрыта.",
        )
    return False


def main() -> int:
    # Acquire before checking WinFsp or creating the main window. This also
    # prevents two simultaneous first-run installer dialogs.
    with SingleInstance() as instance:
        app = QApplication(sys.argv)
        if not instance.acquired:
            if "--tray" not in sys.argv:
                QMessageBox.information(
                    None, "S3Drive уже запущен",
                    "S3Drive уже запущен в системном трее.\n"
                    "Откройте существующее окно через значок S3Drive возле часов.",
                )
            return 0
        if not startup_ready():
            return 1
        app.setApplicationName("S3Drive")
        app.setWindowIcon(app_icon())
        window = MainWindow()
        if "--tray" not in sys.argv or window.tray is None:
            window.show()
        return app.exec()


if __name__ == "__main__":
    sys.exit(main())
