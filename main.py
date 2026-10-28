"""Russian-language Windows GUI for managing S3 mounts through rclone and WinFsp."""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QPushButton, QVBoxLayout, QWidget,
)

from mounts import MountManager
from profiles import Profile, ProfileStore, app_data_dir
from version import APP_VERSION


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


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"S3Drive v{APP_VERSION}")
        self.setMinimumSize(820, 490)
        self.resize(940, 560)
        self.store = ProfileStore()
        self.mounts = MountManager()
        try:
            self.profiles = self.store.load()
        except Exception as exc:
            self.profiles = []
            QMessageBox.warning(self, "Не удалось открыть настройки", str(exc))

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
        self.bucket.setPlaceholderText("Имя бакета")
        self.access = QLineEdit()
        self.secret = QLineEdit()
        self.secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.drive = QComboBox()
        self.drive.addItems([f"{chr(n)}:" for n in range(ord("D"), ord("Z") + 1)])
        self.drive.setCurrentText("S:")
        self.path_style = QCheckBox("Path-Style (для QNAP / MinIO)")
        self.path_style.setChecked(True)
        self.region = QLineEdit("us-east-1")
        for label, widget in (
            ("Название:", self.name), ("Endpoint:", self.endpoint), ("Бакет:", self.bucket),
            ("Access Key:", self.access), ("Secret Key:", self.secret),
            ("Буква диска:", self.drive), ("Регион:", self.region),
        ):
            fields.addRow(label, widget)
        fields.addRow("", self.path_style)
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
        actions.addWidget(self.add_button)
        actions.addWidget(self.save_button)
        actions.addWidget(self.remove_button)
        actions.addStretch()
        actions.addWidget(self.unmount_button)
        actions.addWidget(self.mount_button)
        layout.addLayout(actions)

        self.add_button.clicked.connect(self.new_profile)
        self.save_button.clicked.connect(self.save_profile)
        self.remove_button.clicked.connect(self.remove_profile)
        self.mount_button.clicked.connect(self.mount_profile)
        self.unmount_button.clicked.connect(self.unmount_profile)
        self.refresh_items()
        self.new_profile()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_status)
        self.timer.start(1500)
        self.statusBar().showMessage(f"Настройки и журналы: {app_data_dir()}")

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
        self.region.setText(profile.region)
        self.update_status()

    def new_profile(self):
        self.items.clearSelection()
        self.items.setCurrentRow(-1)
        for widget in (self.name, self.endpoint, self.bucket, self.access, self.secret):
            widget.clear()
        self.drive.setCurrentText("S:")
        self.path_style.setChecked(True)
        self.region.setText("us-east-1")
        self.status.setText("Новый профиль. Заполните поля и нажмите «Сохранить».")

    def draft(self) -> Profile:
        profile = Profile(
            id=self.selected_id() or str(uuid.uuid4()),
            name=self.name.text().strip(), endpoint=self.endpoint.text().strip(),
            bucket=self.bucket.text().strip(), access_key=self.access.text().strip(),
            secret_key=self.secret.text(), drive=self.drive.currentText(),
            path_style=self.path_style.isChecked(), region=self.region.text().strip(),
        )
        profile.validate()
        return profile

    def save_profile(self) -> bool:
        try:
            profile = self.draft()
            if self.mounts.is_mounted(profile.id):
                raise ValueError("Отключите диск перед редактированием параметров подключения.")
            updated = [p for p in self.profiles if p.id != profile.id] + [profile]
            if len({p.drive for p in updated}) != len(updated):
                raise ValueError("Буква диска должна быть уникальной для каждого профиля.")
            self.store.save(updated)
            self.profiles = updated
            self.refresh_items(profile.id)
            self.status.setText("Настройки сохранены (ключи защищены Windows DPAPI).")
            return True
        except Exception as exc:
            QMessageBox.warning(self, "Не удалось сохранить профиль", str(exc))
            return False

    def remove_profile(self):
        profile = self.selected_profile()
        if profile is None:
            return
        if self.mounts.is_mounted(profile.id):
            QMessageBox.warning(self, "Диск подключён", "Сначала отключите диск.")
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

    def mount_profile(self):
        if not self.save_profile():
            return
        profile = self.selected_profile()
        if profile is None:
            return
        try:
            self.mounts.start(profile)
            self.status.setText(f"Запущено монтирование {profile.drive}. Проверьте диск в Проводнике.")
        except Exception as exc:
            QMessageBox.warning(self, "Не удалось подключить S3", str(exc))
        self.update_status()

    def unmount_profile(self):
        profile = self.selected_profile()
        if profile is None:
            return
        try:
            self.mounts.stop(profile.id)
            self.status.setText(f"Диск {profile.drive} отключён.")
        except Exception as exc:
            QMessageBox.warning(self, "Не удалось отключить диск", str(exc))
        self.update_status()

    def update_status(self):
        profile = self.selected_profile()
        is_running = bool(profile and self.mounts.is_mounted(profile.id))
        is_ready = bool(is_running and Path(profile.drive + "\\").exists())
        self.unmount_button.setEnabled(is_running)
        self.mount_button.setEnabled(not is_running)
        if profile:
            self.status.setText(
                f"{profile.drive}: " + ("Подключён" if is_ready else "Монтирование запущено…" if is_running else "Отключён")
            )

    def closeEvent(self, event):
        running = [p for p in self.profiles if self.mounts.is_mounted(p.id)]
        if running:
            result = QMessageBox.question(
                self, "Завершение S3Drive",
                "Отключить все диски и закрыть программу?\n"
                "Перед отключением закройте изменяемые файлы и дождитесь их загрузки в S3.",
            )
            if result != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            for profile in running:
                try:
                    self.mounts.stop(profile.id)
                except Exception as exc:
                    QMessageBox.warning(self, "Не удалось отключить диск", str(exc))
                    event.ignore()
                    return
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("S3Drive")
    app.setWindowIcon(app_icon())
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
