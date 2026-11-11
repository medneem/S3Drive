"""Bilingual Windows GUI for managing S3 mounts through rclone and WinFsp."""
from __future__ import annotations

import os
import sys
import time
import uuid
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QObject, QRunnable, QThreadPool, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFormLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox as QtMessageBox,
    QPushButton, QProgressBar, QVBoxLayout, QWidget,
    QTableWidget, QTableWidgetItem, QAbstractItemView, QHeaderView,
)

from dependencies import winfsp_installed, winfsp_installer
from background import sessions_to_stop
from drive_actions import (MountState, create_desktop_shortcut, disk_root,
                           mount_state, open_mounted_disk)
from service_ui import run_admin
from service_control import (
    ControlUnavailable, EnrollmentRequired, ReconfigurationRequired,
    send_control,
)
from drive_labels import notify_explorer, read_native_volume_label, install_explorer_network_label
from windows_service import service_status, SERVICE_RUNNING, SERVICE_STOPPED
from startup import StartupStatus, ensure_winfsp
from single_instance import SingleInstance
from mounts import MountManager
from profiles import Profile, ProfileStore, app_data_dir, config_path
from version import APP_VERSION
from auto_update import (UpdateInfo, StagedUpdate, check_latest_release,
                         stage_update, discard_update, launch_updater)
from transfer_monitor import read_user_snapshot, read_service_snapshot, TransferSnapshot
from legacy_startup import remove_legacy_tray_autostart
from localization import tr, set_language, get_language, read_language


class QMessageBox(QtMessageBox):
    """Translate all modal dialogs, including the existing warning paths."""

    @staticmethod
    def critical(parent, title, message, *args):
        return QtMessageBox.critical(parent, tr(title), tr(message), *args)

    @staticmethod
    def warning(parent, title, message, *args):
        return QtMessageBox.warning(parent, tr(title), tr(message), *args)

    @staticmethod
    def information(parent, title, message, *args):
        return QtMessageBox.information(parent, tr(title), tr(message), *args)

    @staticmethod
    def question(parent, title, message, *args):
        return QtMessageBox.question(parent, tr(title), tr(message), *args)


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



class _UpdateSignals(QObject):
    checked = Signal(object, str)
    downloaded = Signal(object, str)
    progress = Signal(int, int)
    verifying = Signal()


class _ReleaseCheckTask(QRunnable):
    """Check release metadata on startup without blocking the GUI."""

    def __init__(self):
        super().__init__()
        self.signals = _UpdateSignals()

    def run(self):
        try:
            self.signals.checked.emit(check_latest_release(APP_VERSION), "")
        except Exception as exc:
            self.signals.checked.emit(None, str(exc))


class _ReleaseDownloadTask(QRunnable):
    """Download only after the user explicitly clicks the Update button."""

    def __init__(self, release: UpdateInfo):
        super().__init__()
        self.release = release
        self.signals = _UpdateSignals()

    def run(self):
        try:
            self.signals.downloaded.emit(stage_update(
                self.release,
                on_progress=self.signals.progress.emit,
                on_verifying=self.signals.verifying.emit,
            ), "")
        except Exception as exc:
            self.signals.downloaded.emit(None, str(exc))


class TransferWindow(QDialog):
    """Отдельное окно отложенной отправки файлов на S3."""

    dismissed = Signal(str)

    def __init__(self, profile: Profile, parent: QWidget | None = None):
        super().__init__(parent)
        self.profile_id = profile.id
        self.profile_name = profile.name
        self.profile_drive = profile.drive
        self.last_snapshot = None
        self.setWindowTitle(tr("Загрузка на S3 — {name} ({drive})", name=profile.name, drive=profile.drive))
        self.setWindowFlag(Qt.WindowType.Tool, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.resize(740, 330)
        self.setMinimumWidth(580)
        layout = QVBoxLayout(self)
        self.summary = QLabel(tr("Мониторинг отправки на S3…"))
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.overall_progress = QProgressBar()
        self.overall_progress.setRange(0, 100)
        layout.addWidget(self.overall_progress)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels([
            tr("Направление"), tr("Имя файла"), tr("Прогресс"), tr("Скорость"), tr("Осталось"),
        ])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 125)
        self.table.setColumnWidth(2, 105)
        self.table.setColumnWidth(3, 110)
        self.table.setColumnWidth(4, 100)
        layout.addWidget(self.table)

    def retranslate_ui(self):
        self.setWindowTitle(tr("Загрузка на S3 — {name} ({drive})", name=self.profile_name, drive=self.profile_drive))
        self.table.setHorizontalHeaderLabels([
            tr("Направление"), tr("Имя файла"), tr("Прогресс"), tr("Скорость"), tr("Осталось"),
        ])
        self.summary.setText(tr("Мониторинг отправки на S3…"))
        if self.last_snapshot is not None:
            self.display(self.last_snapshot)

    def display(self, snapshot: TransferSnapshot):
        self.last_snapshot = snapshot
        labels = {
            "upload": tr("↑ На S3"),
            "queued": tr("⌛ В очереди"),
        }
        # Windows already displays its own download progress dialog.
        # Never show incoming downloads or unclassified RC transfers here.
        items = tuple(item for item in snapshot.transfers
                      if item.direction in ("upload", "queued"))
        self.summary.setText(
            tr("На S3: {count}   |   В очереди: {queued}", count=snapshot.uploading, queued=snapshot.queued)
            + (tr("   |   Ошибки: {count}", count=snapshot.errors) if snapshot.errors else "")
        )
        self.table.setRowCount(len(items))
        for row, item in enumerate(items):
            percent = f"{item.percent}%"
            if item.size:
                percent += (f" ({item.bytes_done / 1048576:.1f}/"
                            f"{item.size / 1048576:.1f} {tr('МиБ')})")
            values = (
                labels[item.direction],
                item.name,
                percent if item.direction != "queued" else tr("Ожидает"),
                f"{item.speed / 1048576:.2f} {tr('МиБ/с')}" if item.speed else "—",
                f"{int(item.eta)} s" if item.eta is not None and get_language() == "en"
                else f"{int(item.eta)} с" if item.eta is not None else "—",
            )
            for col, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setToolTip(value)
                self.table.setItem(row, col, cell)
        active = [item for item in items
                  if item.direction != "queued" and item.size]
        total = sum(item.size for item in active)
        done = sum(min(item.bytes_done, item.size) for item in active)
        self.overall_progress.setVisible(bool(total))
        if total:
            self.overall_progress.setValue(round(done * 100 / total))

    def closeEvent(self, event):
        self.dismissed.emit(self.profile_id)
        super().closeEvent(event)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"S3Drive v{APP_VERSION}")
        self._closing_for_update = False
        self.setMinimumSize(820, 490)
        self.resize(940, 560)
        self.store = ProfileStore()
        self.mounts = MountManager()
        self.settings_load_error = False
        try:
            self.profiles = self.store.load()
            set_language(self.store.language)
        except Exception as exc:
            self.settings_load_error = True
            self.profiles = []
            QMessageBox.critical(self, "Не удалось открыть config.json",
                f"{exc}\n\n" + tr("Чтобы избежать потери профилей, сохранение настроек заблокировано. "
                "Исправьте файл config.json и перезапустите S3Drive."))

        try:
            self.mounts.recover(self.profiles)
        except Exception as exc:
            QMessageBox.warning(
                self, "Фоновые подключения",
                tr("Не удалось восстановить управление фоновыми дисками: {error}\n"
                "Существующие диски не отключены. Проверьте состояние перед подключением.", error=exc),
            )

        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        header = QHBoxLayout()
        self.header_title = QLabel(tr("S3Drive — подключение S3 как диска Windows"))
        self.header_title.setStyleSheet("font-size: 18px; font-weight: 600;")
        header.addWidget(self.header_title, 1)
        header.addWidget(QLabel(f"v{APP_VERSION}"))
        self.update_button = QPushButton(tr("Обновить"))
        self.update_button.setVisible(False)
        self.update_button.setEnabled(False)
        self.update_button.clicked.connect(self.start_release_update)
        self._downloading_update = False
        self._update_spinner_angle = 0
        self.update_spinner = QTimer(self)
        self.update_spinner.timeout.connect(self._animate_update_spinner)
        header.addWidget(self.update_button)
        self.settings_button = QPushButton(tr("Настройки"))
        self.settings_button.clicked.connect(self.open_settings)
        header.addWidget(self.settings_button)
        layout.addLayout(header)

        columns = QHBoxLayout()
        self.items = QListWidget()
        self.items.setMinimumWidth(240)
        self.items.currentItemChanged.connect(self.select_profile)
        columns.addWidget(self.items, 2)

        right = QVBoxLayout()
        self.s3_group = QGroupBox(tr("Параметры S3"))
        fields = QFormLayout(self.s3_group)
        self.fields = fields
        self.name = QLineEdit()
        self.name.setPlaceholderText("QNAP S3")
        self.endpoint = QLineEdit()
        self.endpoint.setPlaceholderText("http://nas.local:8010")
        self.bucket = QLineEdit()
        self.bucket.setPlaceholderText(tr("Пусто — показать все доступные бакеты"))
        self.bucket.setToolTip(tr(
            "Необязательно. Без имени бакета монтируется корень S3: "
            "в Проводнике отображаются доступные бакеты как папки. "
            "Ключи должны разрешать ListBuckets."
        ))
        self.access = QLineEdit()
        self.access.setEchoMode(QLineEdit.EchoMode.Password)
        self.secret = QLineEdit()
        self.secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.drive = QComboBox()
        self.drive.addItems([f"{chr(n)}:" for n in range(ord("D"), ord("Z") + 1)])
        self.drive.setCurrentText("S:")
        self.path_style = QCheckBox(tr("Path-Style (для QNAP / MinIO)"))
        self.path_style.setChecked(True)
        self.region = QLineEdit("us-east-1")
        self.field_labels = (
            ("Название:", self.name), ("Endpoint:", self.endpoint), ("Бакет (необязательно):", self.bucket),
            ("Access Key:", self.access), ("Secret Key:", self.secret),
            ("Буква диска:", self.drive), ("Регион:", self.region),
        )
        for label, widget in self.field_labels:
            fields.addRow(tr(label), widget)
        fields.addRow("", self.path_style)
        self.shortcut_checkbox = QCheckBox(tr("Создать ярлык на рабочем столе"))
        self.open_checkbox = QCheckBox(tr("Открыть диск после подключения"))
        fields.addRow("", self.shortcut_checkbox)
        fields.addRow("", self.open_checkbox)
        self.background_checkbox = QCheckBox(tr("Оставлять диск подключённым после закрытия программы"))
        self.background_checkbox.setToolTip(tr(
            "rclone продолжит работать от вашего имени в фоне. "
            "Это не служба Windows, и после перезагрузки автоподключения пока нет."
        ))
        fields.addRow("", self.background_checkbox)
        self.service_checkbox = QCheckBox(tr("Запускать как службу Windows (доступно в Проводнике)"))
        self.service_checkbox.setToolTip(tr(
            "Реальная служба LocalSystem с автозапуском Windows. "
            "Установка и изменение настроек требуют UAC; обычный запуск и безопасная остановка — без UAC."
        ))
        fields.addRow("", self.service_checkbox)
        self.service_checkbox.toggled.connect(
            lambda checked: self.background_checkbox.setChecked(False) if checked else None
        )
        self.background_checkbox.toggled.connect(
            lambda checked: self.service_checkbox.setChecked(False) if checked else None
        )
        right.addWidget(self.s3_group, 1)

        self.status = QLabel(tr("Выберите профиль или создайте новый."))
        self.status.setWordWrap(True)
        right.addWidget(self.status)
        columns.addLayout(right, 4)
        layout.addLayout(columns, 1)

        actions = QHBoxLayout()
        self.add_button = QPushButton(tr("Новый"))
        self.save_button = QPushButton(tr("Сохранить"))
        self.remove_button = QPushButton(tr("Удалить"))
        self.mount_button = QPushButton(tr("Подключить"))
        self.unmount_button = QPushButton(tr("Отключить"))
        self.remove_service_button = QPushButton(tr("Удалить службу"))
        self.clear_cache_button = QPushButton(tr("Очистить кэш"))
        self.clear_cache_button.setToolTip(tr(
            "После подтверждения проверяет отсутствие незавершённых загрузок, "
            "отключает диск и очищает его локальный VFS-кэш. "
            "При незавершённых передачах или ошибках ничего не удаляет."
        ))
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
        self.retranslate_ui()
        self.pending_mounts: dict[str, tuple[Profile, float]] = {}
        self.refresh_items()
        self.new_profile()
        if self.profiles:
            self.items.setCurrentRow(0)
        self.mount_timer = QTimer(self)
        self.mount_timer.timeout.connect(self.poll_mounts)
        self.mount_timer.start(500)

        # The label is stored in the interactive user's HKCU, not the
        # LocalSystem account running the Windows rclone service.
        self._applied_network_labels: dict[str, str] = {}
        self.label_timer = QTimer(self)
        self.label_timer.timeout.connect(self.refresh_mounted_drive_labels)
        self.label_timer.start(8000)
        QTimer.singleShot(0, self.refresh_mounted_drive_labels)

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
        self.statusBar().showMessage(tr("Конфигурация: {config} | Кэш и журналы: {data}", config=config_path(), data=app_data_dir()))
        self.available_release: UpdateInfo | None = None
        self.update_pool = QThreadPool(self)
        self.update_pool.setMaxThreadCount(1)
        self._release_check_running = False
        self._manual_release_check = False
        # Retry a missed startup check; never download until Update is clicked.
        self.release_check_timer = QTimer(self)
        self.release_check_timer.setSingleShot(True)
        self.release_check_timer.timeout.connect(self.check_release_on_startup)
        QTimer.singleShot(0, self.check_release_on_startup)

    def retranslate_ui(self) -> None:
        """Update static widgets in-place; never interrupt a mounted drive."""
        self.header_title.setText(tr("S3Drive — подключение S3 как диска Windows"))
        self.settings_button.setText(tr("Настройки"))
        if not self._downloading_update:
            self.update_button.setText(tr("Обновить"))
        self.s3_group.setTitle(tr("Параметры S3"))
        for label, widget in self.field_labels:
            field_label = self.fields.labelForField(widget)
            if field_label is not None:
                field_label.setText(tr(label))
        self.bucket.setPlaceholderText(tr("Пусто — показать все доступные бакеты"))
        self.bucket.setToolTip(tr(
            "Необязательно. Без имени бакета монтируется корень S3: "
            "в Проводнике отображаются доступные бакеты как папки. "
            "Ключи должны разрешать ListBuckets."
        ))
        self.path_style.setText(tr("Path-Style (для QNAP / MinIO)"))
        self.shortcut_checkbox.setText(tr("Создать ярлык на рабочем столе"))
        self.open_checkbox.setText(tr("Открыть диск после подключения"))
        self.background_checkbox.setText(tr("Оставлять диск подключённым после закрытия программы"))
        self.background_checkbox.setToolTip(tr(
            "rclone продолжит работать от вашего имени в фоне. "
            "Это не служба Windows, и после перезагрузки автоподключения пока нет."
        ))
        self.service_checkbox.setText(tr("Запускать как службу Windows (доступно в Проводнике)"))
        self.service_checkbox.setToolTip(tr(
            "Реальная служба LocalSystem с автозапуском Windows. "
            "Установка и изменение настроек требуют UAC; обычный запуск и безопасная остановка — без UAC."
        ))
        for button, caption in (
            (self.add_button, "Новый"),
            (self.save_button, "Сохранить"),
            (self.remove_button, "Удалить"),
            (self.mount_button, "Подключить"),
            (self.remove_service_button, "Удалить службу"),
            (self.clear_cache_button, "Очистить кэш"),
        ):
            button.setText(tr(caption))
        self.clear_cache_button.setToolTip(tr(
            "После подтверждения проверяет отсутствие незавершённых загрузок, "
            "отключает диск и очищает его локальный VFS-кэш. "
            "При незавершённых передачах или ошибках ничего не удаляет."
        ))
        self.statusBar().showMessage(tr(
            "Конфигурация: {config} | Кэш и журналы: {data}",
            config=config_path(), data=app_data_dir(),
        ))
        self.unmount_button.setText(tr("Отключить"))
        self.update_status()
        if self.selected_profile() is None:
            self.status.setText(tr("Новый профиль. Заполните поля и нажмите «Сохранить»."))

    def open_settings(self) -> None:
        """Save UI settings independently from running S3 services."""
        if self.settings_load_error:
            QMessageBox.warning(self, "Ошибка config.json",
                                "Сохранение заблокировано из-за ошибки чтения config.json. "
                                "Исправьте конфигурацию и перезапустите программу.")
            return
        dialog = QDialog(self)
        self._manual_update_dialog = dialog
        dialog.finished.connect(lambda: setattr(self, "_manual_update_dialog", None))
        dialog.setWindowTitle(tr("Настройки S3Drive"))
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        language = QComboBox()
        language.addItem("English", "en")
        language.addItem("Русский", "ru")
        language.setCurrentIndex(language.findData(get_language()))
        form.addRow(tr("Язык интерфейса:"), language)
        check_updates = QCheckBox(tr("Проверять новую версию при запуске"))
        check_updates.setChecked(self.store.check_updates_on_startup)
        form.addRow(check_updates)
        layout.addLayout(form)
        check_now = QPushButton(tr("Проверить обновление"))
        check_now.clicked.connect(lambda: self.check_release(manual=True))
        layout.addWidget(check_now)
        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton(tr("Отмена"))
        save = QPushButton(tr("Сохранить"))
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        cancel.clicked.connect(dialog.reject)
        save.clicked.connect(dialog.accept)
        layout.addLayout(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        selected = language.currentData()
        selected_checks = check_updates.isChecked()
        previous_language = self.store.language
        previous_checks = self.store.check_updates_on_startup
        if selected == previous_language and selected_checks == previous_checks:
            return
        try:
            self.store.language = selected
            self.store.check_updates_on_startup = selected_checks
            self.store.save(self.profiles)
            if not selected_checks:
                self.release_check_timer.stop()
        except Exception as exc:
            self.store.language = previous_language
            self.store.check_updates_on_startup = previous_checks
            QMessageBox.warning(self, "Не удалось сохранить настройки", str(exc))
            return
        if selected != previous_language:
            set_language(selected)
            self.retranslate_ui()
            if self.transfer_popup is not None:
                self.transfer_popup.retranslate_ui()

    def check_release_on_startup(self) -> None:
        """Check stable GitHub releases only when enabled and in the built app."""
        if not self.store.check_updates_on_startup or not getattr(sys, "frozen", False):
            return
        self.check_release(manual=False)

    def check_release(self, *, manual: bool = False) -> None:
        """Read GitHub metadata in a worker; manual checks ignore startup preference."""
        if self._release_check_running:
            if manual:
                parent = getattr(self, "_manual_update_dialog", None) or self
                QMessageBox.information(parent, "S3Drive", tr("Проверка обновлений уже выполняется."))
            return
        self._release_check_running = True
        self._manual_release_check = manual
        task = _ReleaseCheckTask()
        task.signals.checked.connect(self.on_release_checked)
        if manual:
            self.statusBar().showMessage(tr("Проверка обновлений…"))
        self.update_pool.start(task)

    def on_release_checked(self, release: UpdateInfo | None, error: str) -> None:
        """Never silently swallow a manual check failure; automatic checks stay quiet."""
        manual = self._manual_release_check
        self._manual_release_check = False
        self._release_check_running = False
        if error:
            self.statusBar().showMessage(
                tr("Не удалось проверить обновления: {error}", error=error)
            )
            if manual:
                parent = getattr(self, "_manual_update_dialog", None) or self
                QMessageBox.warning(parent, tr("Не удалось проверить обновления"), error)
            elif self.store.check_updates_on_startup:
                # Retry a transient offline startup check, never a failed manual check.
                self.release_check_timer.start(5 * 60 * 1000)
            return
        if release is None:
            if manual:
                parent = getattr(self, "_manual_update_dialog", None) or self
                QMessageBox.information(parent, "S3Drive", tr("Новых версий S3Drive нет."))
            return
        self.available_release = release
        self.update_button.setToolTip(tr("Доступна S3Drive v{version}", version=release.version))
        self.update_button.setEnabled(True)
        self.update_button.setVisible(True)
        self.statusBar().showMessage(
            tr("Доступна новая версия S3Drive v{version}. Нажмите «Обновить».", version=release.version)
        )
        if manual:
            parent = getattr(self, "_manual_update_dialog", None) or self
            QMessageBox.information(
                parent, "S3Drive", tr("Доступна S3Drive v{version}", version=release.version)
            )

    def _animate_update_spinner(self) -> None:
        """Draw a lightweight native-size animated arc inside the Update button."""
        self._update_spinner_angle = (self._update_spinner_angle - 28) % 360
        pixmap = QPixmap(18, 18)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#1685cf"), 2))
        painter.drawArc(3, 3, 12, 12, self._update_spinner_angle * 16, 260 * 16)
        painter.end()
        self.update_button.setIcon(QIcon(pixmap))

    def on_release_progress(self, written: int, expected: int) -> None:
        """Show actual bytes received, not a fake timer-based percentage."""
        if not self._downloading_update or not self.available_release or expected <= 0:
            return
        percent = min(100, max(0, written * 100 // expected))
        self.update_button.setText(tr("Загрузка… {percent}%", percent=percent))
        self.statusBar().showMessage(tr(
            "Скачивание S3Drive v{version}… {percent}%",
            version=self.available_release.version, percent=percent
        ))

    def on_release_verifying(self) -> None:
        """Do not describe SHA-256 verification as downloading at 100%."""
        if not self._downloading_update or not self.available_release:
            return
        self.update_button.setText(tr("Проверка SHA-256…"))
        self.statusBar().showMessage(tr(
            "Проверка SHA-256 S3Drive v{version}…",
            version=self.available_release.version
        ))

    def start_release_update(self) -> None:
        """User-requested download, never triggered automatically."""
        if self.available_release is None or not self.update_button.isEnabled():
            return
        self.update_button.setEnabled(False)
        self._downloading_update = True
        self.update_button.setText(tr("Загрузка… {percent}%", percent=0))
        self.update_spinner.start(90)
        self._animate_update_spinner()
        self.statusBar().showMessage(tr(
            "Скачивание S3Drive v{version}… {percent}%",
            version=self.available_release.version, percent=0
        ))
        task = _ReleaseDownloadTask(self.available_release)
        task.signals.progress.connect(self.on_release_progress)
        task.signals.verifying.connect(self.on_release_verifying)
        task.signals.downloaded.connect(self.on_release_downloaded)
        self.update_pool.start(task)

    def on_release_downloaded(self, staged: StagedUpdate | None, error: str) -> None:
        """Close normally, then let a separate updater replace the executable."""
        self._downloading_update = False
        self.update_spinner.stop()
        self.update_button.setIcon(QIcon())
        self.update_button.setText(tr("Обновить"))
        if error or staged is None:
            self.update_button.setEnabled(True)
            QMessageBox.warning(
                self, "Не удалось скачать обновление",
                error or "Не удалось проверить загруженный архив."
            )
            return

        # A user background rclone process may still use rclone.exe in the
        # portable directory. Never stop it forcibly or discard VFS writes.
        background = [
            p.drive for p in self.profiles
            if p.keep_running and self.mounts.is_mounted(p.id)
        ]
        if background:
            discard_update(staged)
            self.update_button.setEnabled(True)
            QMessageBox.warning(
                self, "Обновление отложено",
                tr("Сначала безопасно отключите фоновые диски {drives} после завершения загрузки в S3. Службы Windows не затронуты.", drives=", ".join(background))
            )
            return

        application = QApplication.instance()
        if application is not None:
            application.setQuitOnLastWindowClosed(False)
        accepted = self.close()  # Existing closeEvent enforces VFS upload checks.
        if not accepted:
            if application is not None:
                application.setQuitOnLastWindowClosed(True)
            discard_update(staged)
            self.update_button.setEnabled(True)
            return
        try:
            launch_updater(
                staged, Path(sys.executable).resolve().parent, parent_pid=os.getpid()
            )
        except Exception as exc:
            discard_update(staged)
            self.show()
            self.monitor_timer.start(1500)
            if application is not None:
                application.setQuitOnLastWindowClosed(True)
            self.update_button.setEnabled(True)
            QMessageBox.warning(self, "Не удалось запустить обновление", str(exc))
            return
        self._closing_for_update = True
        # Stop GUI-only timers before the event loop exits. None of these
        # timers controls the independently running rclone Windows service.
        for timer in (self.mount_timer, self.label_timer, self.timer,
                      self.monitor_timer, self.release_check_timer):
            timer.stop()
        if hasattr(self, "activation_timer"):
            self.activation_timer.stop()
        if application is not None:
            # Restore the Qt default; otherwise a hidden window can leave
            # the GUI process holding its single-instance mutex.
            application.setQuitOnLastWindowClosed(True)
            QTimer.singleShot(0, application.quit)

    def bind_single_instance(self, instance: SingleInstance) -> None:
        """Listen only while the existing GUI is alive; never create another."""
        self.activation_timer = QTimer(self)
        self.activation_timer.timeout.connect(
            lambda: self.activate_existing_window()
            if instance.activation_requested() else None
        )
        self.activation_timer.start(250)

    def activate_existing_window(self) -> None:
        """A duplicate launch restores the one permitted GUI window."""
        if self._closing_for_update:
            return
        if self.isMinimized():
            self.showNormal()
        else:
            self.show()
        self.raise_()
        self.activateWindow()

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
        self.name.setText(tr("Личный сетевой диск"))
        self.drive.setCurrentText("S:")
        self.path_style.setChecked(True)
        self.shortcut_checkbox.setChecked(True)
        self.open_checkbox.setChecked(False)
        self.background_checkbox.setChecked(False)
        self.service_checkbox.setChecked(True)
        self.region.setText("us-east-1")
        self.status.setText(tr("Новый профиль. Заполните поля и нажмите «Сохранить»."))

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
            self.status.setText(tr("Настройки сохранены (ключи защищены Windows DPAPI)."))
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
                state = service_status(profile.id)
                if state is None:
                    # First installation: UAC only once, configure protected
                    # LocalSystem broker and enroll the interactive user SID.
                    run_admin("install", profile.id)
                elif state == SERVICE_STOPPED:
                    try:
                        send_control("start", profile)
                    except (ControlUnavailable, EnrollmentRequired):
                        # One-time migration of services installed before v1.5.5.
                        run_admin("setup-control", profile.id)
                        send_control("start", profile)
                    except ReconfigurationRequired:
                        # Updating protected rclone settings still requires UAC.
                        run_admin("install", profile.id)
                else:
                    raise RuntimeError("Служба меняет состояние. Повторите попытку.")
                notify_explorer()
                self.pending_mounts[profile.id] = (profile, time.monotonic())
                self.status.setText(tr("Подключение службы и диска {drive}…", drive=profile.drive))
            else:
                if service_status(profile.id) is not None:
                    raise RuntimeError(
                        "Для этой записи установлена служба Windows. "
                        "Остановите и удалите службу перед обычным подключением."
                    )
                self.mounts.start(profile)
                self.pending_mounts[profile.id] = (profile, time.monotonic())
                self.status.setText(tr("Подключение диска {drive}…", drive=profile.drive))

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
                    try:
                        send_control("stop", profile)
                    except (ControlUnavailable, EnrollmentRequired):
                        # Existing installation needs a one-time UAC migration.
                        run_admin("setup-control", profile.id)
                        send_control("stop", profile)
                    notify_explorer()
            else:
                self.mounts.stop(profile.id)
            self.pending_mounts.pop(profile.id, None)
            self.status.setText(tr("Диск {drive} отключён.", drive=profile.drive))
        except Exception as exc:
            QMessageBox.warning(self, "Не удалось отключить диск", str(exc))
        self.update_status()

    def refresh_mounted_drive_labels(self) -> None:
        """Rename only active S3Drive UNC shares in the current user's Explorer.

        A Windows service runs as LocalSystem, which has a different HKCU.
        Reapply once when the GUI starts to cover services mounted at boot.
        """
        if os.name != "nt":
            return
        for profile in self.profiles:
            if self._applied_network_labels.get(profile.id) == profile.name:
                continue
            active = (service_status(profile.id) == SERVICE_RUNNING
                      or self.mounts.is_mounted(profile.id))
            if not active or not os.path.isdir(disk_root(profile.drive)):
                continue
            try:
                install_explorer_network_label(profile.id, profile.name)
                self._applied_network_labels[profile.id] = profile.name
            except OSError as exc:
                self.statusBar().showMessage(
                    tr("Не удалось обновить имя диска {drive}: {error}",
                       drive=profile.drive, error=exc),
                    8000,
                )

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
                self.refresh_mounted_drive_labels()
                problems = []
                if profile.create_shortcut:
                    try:
                        create_desktop_shortcut(profile.drive, profile.name)
                    except Exception as exc:
                        problems.append(tr("Ярлык на рабочем столе не создан: {error}", error=exc))
                if profile.open_after_mount:
                    try:
                        open_mounted_disk(profile.drive)
                    except Exception as exc:
                        problems.append(tr("Не удалось открыть диск: {error}", error=exc))
                message = tr("Диск {drive} успешно подключён.", drive=profile.drive)
                native_label = read_native_volume_label(profile.drive)
                if native_label and native_label != profile.name:
                    message += "\n" + tr("Системная метка WinFsp: {label}. Полное имя дополнительно задано для Проводника Windows.", label=native_label)
                if problems:
                    message += "\n\n" + "\n".join(problems)
                QMessageBox.information(self, "S3Drive", message)
            elif state == MountState.FAILED:
                QMessageBox.warning(
                    self, "Ошибка подключения",
                    tr("Не удалось подключить диск {drive}: rclone или служба завершилась.\n"
                    "Подробности: {log}", drive=profile.drive,
                    log=app_data_dir() / 'logs' / (profile.id + '.log')),
                )
            else:
                QMessageBox.warning(
                    self, "Подключение не подтверждено",
                    tr("Диск {drive} не появился в Windows за 40 секунд.\n"
                    "Не отключайте компьютер во время записи в S3.\n"
                    "rclone всё ещё работает; проверьте журнал и состояние диска.", drive=profile.drive),
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
            tr("Проверить загрузки, отключить диск {drive} "
            "и удалить локальный кэш?\n\n"
            "Это отключит диск до следующего ручного подключения. "
            "Если есть незавершённые загрузки, открытые файлы или ошибки, "
            "операция будет отменена.", drive=profile.drive),
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
                tr("Локальный кэш {name} очищен. Диск отключён. "
                "Подключите его снова при необходимости.", name=profile.name),
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
                self.statusBar().showMessage(tr("Монитор загрузок: {error}", error=error[:170]), 12000)
            # Недостоверная статистика не должна скрывать уже открытую передачу.
            return
        self.monitor_last_error = ""
        # Show our window for confirmed VFS uploads only. Windows Explorer
        # already owns the UI for downloads from the mounted drive.
        transferring = snapshot.has_upload_activity
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
                    tr("S3Drive: обнаружены ошибки передачи. Проверьте журнал rclone."), 15000
                )
            elif profile_id in self.uploads_seen:
                self.uploads_seen.discard(profile_id)
                self.statusBar().showMessage(tr("S3Drive: загрузка файлов на S3 завершена"), 8000)

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
            tr("Остановить службу и диск") if service is not None
            else tr("Отключить фон и диск") if user_running and self.mounts.keep_running(profile.id)
            else tr("Отключить диск")
        )
        self.mount_button.setEnabled(not active)
        if profile.id in self.pending_mounts:
            text = tr("Подключение…")
        elif service_running:
            text = tr("Служба работает; диск в Проводнике") if ready else tr("Служба запущена; диск не обнаружен")
        elif service == SERVICE_STOPPED:
            text = tr("Служба установлена, остановлена")
        elif user_running:
            text = tr("Подключён (в фоне)") if self.mounts.keep_running(profile.id) else tr("Подключён")
        else:
            text = tr("Отключён")
        self.status.setText(f"{profile.drive}: {text}")

    def closeEvent(self, event):
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
        # Закрытие основного окна завершает только GUI. Служба Windows
        # остаётся работать независимо; собственное окно загрузки закрываем,
        # чтобы в сеансе не оставался отдельный процесс мониторинга.
        self.monitor_timer.stop()
        self.hide_transfer_popup()
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
        set_language(read_language(config_path()))
        if not instance.acquired:
            instance.request_activation()
            QMessageBox.information(
                None, "S3Drive уже запущен",
                "S3Drive уже запущен. Закройте существующее окно программы, "
                "прежде чем запускать другую копию.",
            )
            return 0
        # Миграция: ранее S3Drive мог включить скрытый запуск из HKCU Run.
        # Удаляем только нашу прежнюю запись, ничего не добавляя в автозапуск.
        try:
            remove_legacy_tray_autostart()
        except OSError:
            pass
        # Старые HKCU записи вызывают S3Drive.exe --tray. Не показываем из-за
        # них неожиданное окно при входе в Windows.
        if "--tray" in sys.argv:
            return 0
        if not startup_ready():
            return 1
        app.setApplicationName("S3Drive")
        app.setWindowIcon(app_icon())
        window = MainWindow()
        window.show()
        try:
            instance.enable_activation()
            window.bind_single_instance(instance)
        except OSError:
            # The mutex still prevents a second GUI if focus signalling fails.
            pass
        return app.exec()


if __name__ == "__main__":
    sys.exit(main())
