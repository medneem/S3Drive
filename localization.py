"""English (default) and Russian text for the S3Drive desktop interface."""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

SUPPORTED_LANGUAGES = ("en", "ru")
_language = "en"

# Source strings are kept in Russian to avoid accidental changes to existing logic.
# Templates with {named_fields} also translate formatted runtime status messages.
ENGLISH = {
    "S3Drive — подключение S3 как диска Windows": "S3Drive — Mount S3 as a Windows drive",
    "Обновить": "Update",
    "Настройки": "Settings",
    "Проверять новую версию при запуске": "Check for new versions at startup",
    "Проверить обновление": "Check for updates",
    "Проверка обновлений…": "Checking for updates…",
    "Проверка обновлений уже выполняется.": "An update check is already running.",
    "Новых версий S3Drive нет.": "S3Drive is up to date.",
    "Не удалось проверить обновления": "Unable to check for updates",
    "Не удалось проверить обновления: {error}": "Unable to check for updates: {error}",
    "Не удалось обновить имя диска {drive}: {error}": "Unable to update drive {drive} label: {error}",
    "Не удалось сохранить настройки": "Could not save settings",
    "Настройки S3Drive": "S3Drive settings",
    "Язык интерфейса:": "Interface language:",
    "Отмена": "Cancel",
    "Сохранить": "Save",
    "Параметры S3": "S3 settings",
    "Название:": "Name:",
    "Бакет (необязательно):": "Bucket (optional):",
    "Буква диска:": "Drive letter:",
    "Регион:": "Region:",
    "Пусто — показать все доступные бакеты": "Leave empty to show all accessible buckets",
    "Необязательно. Без имени бакета монтируется корень S3: в Проводнике отображаются доступные бакеты как папки. Ключи должны разрешать ListBuckets.": "Optional. With no bucket name, the S3 account root is mounted and accessible buckets appear as folders in Explorer. The credentials must allow ListBuckets.",
    "Path-Style (для QNAP / MinIO)": "Path-style (for QNAP / MinIO)",
    "Создать ярлык на рабочем столе": "Create desktop shortcut",
    "Открыть диск после подключения": "Open drive after connecting",
    "Оставлять диск подключённым после закрытия программы": "Keep drive connected after closing the app",
    "rclone продолжит работать от вашего имени в фоне. Это не служба Windows, и после перезагрузки автоподключения пока нет.": "rclone keeps running in the background as your user. This is not a Windows service and does not automatically reconnect after a reboot.",
    "Запускать как службу Windows (доступно в Проводнике)": "Run as Windows service (visible in Explorer)",
    "Реальная служба LocalSystem с автозапуском Windows. Установка и остановка службы требуют подтверждения UAC.": "A real LocalSystem service that starts with Windows. Installing and stopping the service require UAC confirmation.",
    "Выберите профиль или создайте новый.": "Select a profile or create a new one.",
    "Новый": "New",
    "Удалить": "Delete",
    "Подключить": "Connect",
    "Отключить": "Disconnect",
    "Удалить службу": "Remove service",
    "Очистить кэш": "Clear cache",
    "После подтверждения проверяет отсутствие незавершённых загрузок, отключает диск и очищает его локальный VFS-кэш. При незавершённых передачах или ошибках ничего не удаляет.": "After confirmation, checks for pending uploads, disconnects the drive and clears its local VFS cache. Nothing is deleted when transfers are pending or errors occur.",
    "Конфигурация: {config} | Кэш и журналы: {data}": "Configuration: {config} | Cache and logs: {data}",
    "Личный сетевой диск": "Personal network drive",
    "Новый профиль. Заполните поля и нажмите «Сохранить».": "New profile. Fill in the fields and click Save.",
    "Настройки сохранены (ключи защищены Windows DPAPI).": "Settings saved (keys protected with Windows DPAPI).",
    "Остановить службу и диск": "Stop service and drive",
    "Отключить фон и диск": "Stop background mount and drive",
    "Отключить диск": "Disconnect drive",
    "Подключение…": "Connecting…",
    "Служба работает; диск в Проводнике": "Service running; drive visible in Explorer",
    "Служба запущена; диск не обнаружен": "Service running; drive not found",
    "Служба установлена, остановлена": "Service installed, stopped",
    "Подключён (в фоне)": "Connected (background)",
    "Подключён": "Connected",
    "Отключён": "Disconnected",
    "Загрузка на S3 — {name} ({drive})": "Uploading to S3 — {name} ({drive})",
    "Мониторинг отправки на S3…": "Monitoring S3 uploads…",
    "Направление": "Direction",
    "Имя файла": "File name",
    "Прогресс": "Progress",
    "Скорость": "Speed",
    "Осталось": "Remaining",
    "↑ На S3": "↑ To S3",
    "⌛ В очереди": "⌛ Queued",
    "На S3: {count}   |   В очереди: {queued}": "Uploading: {count}   |   Queued: {queued}",
    "   |   Ошибки: {count}": "   |   Errors: {count}",
    "Ожидает": "Waiting",
    "МиБ": "MiB",
    "МиБ/с": "MiB/s",
    "Загрузка…": "Downloading…",
    "Загрузка… {percent}%": "Downloading… {percent}%",
    "Скачивание S3Drive v{version}… {percent}%": "Downloading S3Drive v{version}… {percent}%",
    "Проверка SHA-256…": "Verifying SHA-256…",
    "Проверка SHA-256 S3Drive v{version}…": "Verifying S3Drive v{version} SHA-256…",
    "Доступна S3Drive v{version}": "S3Drive v{version} available",
    "Доступна новая версия S3Drive v{version}. Нажмите «Обновить».": "S3Drive v{version} is available. Click Update.",
    "Скачивание и проверка S3Drive v{version}…": "Downloading and verifying S3Drive v{version}…",
    "Монитор загрузок: {error}": "Upload monitor: {error}",
    "S3Drive: обнаружены ошибки передачи. Проверьте журнал rclone.": "S3Drive: transfer errors detected. Check the rclone log.",
    "S3Drive: загрузка файлов на S3 завершена": "S3Drive: S3 uploads completed",
    "Подключение службы и диска {drive}…": "Connecting service and drive {drive}…",
    "Подключение диска {drive}…": "Connecting drive {drive}…",
    "Диск {drive} отключён.": "Drive {drive} disconnected.",
    "Диск {drive} успешно подключён.": "Drive {drive} connected successfully.",
    "Системная метка WinFsp: {label}. Полное имя дополнительно задано для Проводника Windows.": "WinFsp system label: {label}. The full name is also configured for Windows Explorer.",
    "Ярлык на рабочем столе не создан: {error}": "Could not create desktop shortcut: {error}",
    "Не удалось открыть диск: {error}": "Could not open drive: {error}",
    "Служба уже остановлена.": "The service is already stopped.",
    "Служба уже запущена.": "The service is already running.",
    "Служба удалена.": "Service removed.",
    "Для этой записи установлена служба Windows. Остановите и удалите службу перед обычным подключением.": "A Windows service is installed for this profile. Stop and remove it before a regular connection.",
    "Сначала отключите пользовательское монтирование.": "Disconnect the user mount first.",
    "Чтобы сменить букву диска, сначала остановите и удалите старую службу.": "Stop and remove the existing service before changing the drive letter.",
    "Остановите службу перед сменой режима подключения.": "Stop the service before changing connection mode.",
    "Остановите службу перед изменением её настроек S3.": "Stop the service before changing its S3 settings.",
    "Отключите диск перед изменением настроек S3.": "Disconnect the drive before changing S3 settings.",
    "Буква диска должна быть уникальной для каждого профиля.": "Each profile must use a unique drive letter.",
    "Не удалось открыть config.json": "Could not open config.json",
    "Ошибка config.json": "config.json error",
    "Сохранение заблокировано из-за ошибки чтения config.json. Исправьте конфигурацию и перезапустите программу.": "Saving is blocked because config.json could not be read. Fix the file and restart the app.",
    "Чтобы избежать потери профилей, сохранение настроек заблокировано. Исправьте файл config.json и перезапустите S3Drive.": "To prevent profile loss, saving is disabled. Fix config.json and restart S3Drive.",
    "Удаление заблокировано из-за ошибки чтения конфигурации.": "Deleting is blocked because the configuration could not be read.",
    "Фоновые подключения": "Background connections",
    "Не удалось восстановить управление фоновыми дисками: {error}\nСуществующие диски не отключены. Проверьте состояние перед подключением.": "Could not restore background-drive management: {error}\nExisting drives were not disconnected. Check their state before connecting.",
    "Не удалось сохранить профиль": "Could not save profile",
    "Подключение существует": "Connection exists",
    "Сначала отключите диск и удалите соответствующую службу Windows.": "Disconnect the drive and remove its Windows service first.",
    "Удалить профиль": "Delete profile",
    "Удалить «{name}»?": "Delete “{name}”?",
    "Ошибка удаления": "Deletion error",
    "Служба работает": "Service is running",
    "Сначала нажмите «Остановить службу и диск» и дождитесь отключения.": "Click Stop service and drive first, then wait for disconnection.",
    "Удалить службу для диска {drive}?\nНастройки подключения в config.json сохранятся.": "Remove the service for drive {drive}?\nConnection settings in config.json will be kept.",
    "Удаление службы": "Service removal",
    "WinFsp не установлен": "WinFsp is not installed",
    "Для монтирования нужен системный драйвер WinFsp. Установите драйвер и запустите S3Drive повторно.": "The WinFsp system driver is required. Install it and restart S3Drive.",
    "Не удалось подключить S3": "Could not connect to S3",
    "Не удалось отключить диск": "Could not disconnect drive",
    "Ошибка подключения": "Connection error",
    "Не удалось подключить диск {drive}: rclone или служба завершилась.\nПодробности: {log}": "Could not mount drive {drive}: rclone or the service exited.\nDetails: {log}",
    "Подключение не подтверждено": "Connection not confirmed",
    "Диск {drive} не появился в Windows за 40 секунд.\nНе отключайте компьютер во время записи в S3.\nrclone всё ещё работает; проверьте журнал и состояние диска.": "Drive {drive} did not appear in Windows within 40 seconds.\nDo not shut down the computer while writing to S3.\nrclone is still running; check the log and drive status.",
    "Очистка кэша": "Cache cleanup",
    "Для безопасной очистки диск должен быть подключён. После аварийного завершения в кэше могут остаться неотправленные файлы, поэтому удалять его без проверки нельзя.": "The drive must be connected to clear the cache safely. Unsent files may remain in the cache after a crash, so it cannot be deleted without checking.",
    "Проверить загрузки, отключить диск {drive} и удалить локальный кэш?\n\nЭто отключит диск до следующего ручного подключения. Если есть незавершённые загрузки, открытые файлы или ошибки, операция будет отменена.": "Check uploads, disconnect drive {drive}, and delete the local cache?\n\nThe drive will remain disconnected until you reconnect manually. The operation is cancelled if uploads are pending or files are open or errors occur.",
    "Кэш очищен": "Cache cleared",
    "Локальный кэш {name} очищен. Диск отключён. Подключите его снова при необходимости.": "Local cache for {name} cleared. The drive is disconnected. Reconnect it if needed.",
    "Кэш не очищен": "Cache not cleared",
    "Завершение S3Drive": "Exit S3Drive",
    "Отключить диски без фонового режима и закрыть программу?\nФоновые подключения останутся активными.\n\nПеред отключением закройте изменяемые файлы и дождитесь загрузки в S3.": "Disconnect drives without background mode and exit?\nBackground connections will remain active.\n\nClose files being edited and wait for S3 uploads before disconnecting.",
    "Не удалось скачать обновление": "Could not download update",
    "Не удалось проверить загруженный архив.": "Could not verify the downloaded archive.",
    "Обновление отложено": "Update postponed",
    "Не удалось запустить обновление": "Could not start updater",
    "Сначала безопасно отключите фоновые диски {drives} после завершения загрузки в S3. Службы Windows не затронуты.": "Safely disconnect background drives {drives} after S3 uploads complete. Windows services are unaffected.",
    "Требуется WinFsp": "WinFsp required",
    "Для подключения S3 как диска Windows необходим драйвер WinFsp.\n\nДрайвер не обнаружен. Установить его сейчас?\nДля установки понадобятся права администратора.": "The WinFsp driver is required to mount S3 as a Windows drive.\n\nThe driver was not found. Install it now?\nAdministrator permission is required.",
    "Установка WinFsp": "Installing WinFsp",
    "Установщик WinFsp запущен.\n\nЗавершите установку, затем запустите S3Drive повторно. Программа сейчас закроется.": "The WinFsp installer has started.\n\nFinish installation, then run S3Drive again. The app will now close.",
    "WinFsp необходим": "WinFsp required",
    "Без драйвера WinFsp S3Drive не сможет подключать диски.\n\nПрограмма будет закрыта.": "S3Drive cannot mount drives without WinFsp.\n\nThe app will close.",
    "Не найден WinFsp": "WinFsp not found",
    "Драйвер WinFsp не установлен, а установщик отсутствует рядом с S3Drive.exe.\n\nСкачайте полный архив из GitHub Releases и распакуйте его целиком.\nБез драйвера работа невозможна. Программа будет закрыта.": "WinFsp is not installed and its installer is missing beside S3Drive.exe.\n\nDownload and extract the full archive from GitHub Releases.\nThe app cannot work without the driver and will close.",
    "Ошибка запуска WinFsp": "WinFsp launch error",
    "Не удалось открыть установщик WinFsp: {error}\n\nБез драйвера работа невозможна. Программа будет закрыта.": "Could not start the WinFsp installer: {error}\n\nThe app cannot work without the driver and will close.",
    "S3Drive уже запущен": "S3Drive is already running",
    "S3Drive уже запущен. Закройте существующее окно программы, прежде чем запускать другую копию.": "S3Drive is already running. Close the existing window before starting another copy.",
    "Некорректный идентификатор подключения (UUID).": "Invalid connection identifier (UUID).",
    "Введите название подключения.": "Enter a connection name.",
    "Название не должно превышать 128 символов или содержать управляющие символы.": "The name must be at most 128 characters and contain no control characters.",
    "Endpoint должен начинаться с http:// или https://.": "Endpoint must start with http:// or https://.",
    "Endpoint должен содержать только схему, адрес и порт.": "Endpoint may contain only a scheme, host, and port.",
    "Некорректный порт Endpoint.": "Invalid endpoint port.",
    "Укажите корректное имя S3-бакета (2–63 символа) или оставьте поле пустым.": "Enter a valid S3 bucket name (2–63 characters) or leave it empty.",
    "Буква диска должна иметь вид S:.": "A drive letter must look like S:.",
    "Укажите Access Key и Secret Key.": "Enter an Access Key and Secret Key.",
    "Некорректный регион S3.": "Invalid S3 region.",
    "Параметры ярлыка и открытия диска должны быть true/false.": "Shortcut and open-drive options must be true/false.",
    "Фоновый режим должен быть true или false.": "Background mode must be true or false.",
    "Режим службы должен быть true или false.": "Service mode must be true or false.",
    "Выберите службу Windows или пользовательский фон, но не оба варианта.": "Choose Windows service or user background mode, not both.",
    "Некорректный порт управления.": "Invalid control port.",
    "Не найден rclone.exe. Установите rclone или положите его рядом с S3Drive.exe.": "rclone.exe was not found. Install rclone or put it beside S3Drive.exe.",
    "Это подключение уже запущено.": "This connection is already running.",
    "Буква диска уже используется другим подключением S3Drive.": "The drive letter is already used by another S3Drive connection.",
    "Монтирование поддерживается только в Windows.": "Mounting is supported only on Windows.",
    "Диск {drive} уже занят.": "Drive {drive} is already in use.",
    "Для безопасной очистки подключите диск и дождитесь окончания передач. Кэш остановленного или аварийно завершённого процесса не удаляется.": "Connect the drive and wait for transfers to finish before clearing the cache. The cache of a stopped or crashed process cannot be deleted.",
    "Не удалось проверить состояние VFS rclone; диск не отключён. Проверьте журнал и повторите попытку.": "Could not check the rclone VFS state; the drive remains connected. Check the log and try again.",
    "Диск не отключён: {error}": "Drive not disconnected: {error}",
    "rclone не завершился. Диск оставлен подключённым.": "rclone did not exit. The drive remains connected.",
    "Фоновый rclone не завершился. Диск оставлен подключённым.": "Background rclone did not exit. The drive remains connected.",
    "Службы поддерживаются только в Windows.": "Services are supported only on Windows.",
    "Некорректная буква диска.": "Invalid drive letter.",
    "Буква диска изменилась: сначала удалите старую службу.": "Drive letter changed: remove the old service first.",
    "Буква {drive} занята. Отключите предыдущий диск.": "Drive letter {drive} is occupied. Disconnect the previous drive.",
    "Служба уже запущена. Для изменения настроек остановите её.": "The service is running. Stop it before changing settings.",
    "Рядом с S3Drive отсутствует rclone.exe.": "rclone.exe is missing beside S3Drive.",
    "Служба не установлена.": "Service not installed.",
    "Служба меняет состояние. Повторите попытку позже.": "The service is changing state. Try again later.",
    "Служба ещё не остановлена. Кэш не удалён.": "The service has not stopped yet. Cache was not deleted.",
    "Сначала остановите службу и дождитесь отключения диска.": "Stop the service and wait for the drive to disconnect.",
    "Недопустимое действие службы.": "Invalid service action.",
    "Доступно только в Windows.": "Available only on Windows.",
    "S3DriveAdmin.exe отсутствует. Распакуйте полный архив.": "S3DriveAdmin.exe is missing. Extract the complete archive.",
    "Управление службой не завершилось вовремя.": "Service operation timed out.",
    "Операция службы не завершена. Смотрите окно ошибки помощника.": "Service operation failed. Check the helper's error dialog.",
    "Название подключения не может быть пустым.": "Connection name cannot be empty.",
    "Ярлыки поддерживаются только в Windows.": "Shortcuts are supported only on Windows.",
    "Открытие диска поддерживается только в Windows.": "Opening a drive is supported only on Windows.",
    "В релизе отсутствует список файлов.": "Release file list is missing.",
    "Не найден единственный официальный ZIP-архив S3Drive.": "Could not find the official S3Drive ZIP archive.",
    "Некорректная ссылка на обновление.": "Invalid update link.",
    "GitHub не предоставил корректный SHA-256 архива.": "GitHub did not provide a valid archive SHA-256.",
    "Недопустимый размер обновления.": "Invalid update size.",
    "Некорректный SHA-256.": "Invalid SHA-256.",
    "Не совпадает SHA-256 загруженного архива.": "Downloaded archive SHA-256 does not match.",
    "Недопустимый набор файлов обновления.": "Invalid update file set.",
    "Недопустимый тип файла обновления.": "Invalid update file type.",
    "Слишком большой распакованный архив.": "Extracted archive is too large.",
    "Версия в архиве не совпадает с версией релиза.": "Archive version does not match release version.",
    "Размер загрузки превысил размер релиза.": "Download exceeded the release size.",
    "Загружен неполный архив.": "Incomplete archive downloaded.",
    "Не найдены исполняемые файлы S3Drive или обновлятора.": "S3Drive or updater executable was not found.",
    "Не удалось сохранить язык интерфейса": "Could not save interface language",
}

def set_language(language: str) -> None:
    global _language
    if language not in SUPPORTED_LANGUAGES:
        raise ValueError("Unsupported interface language.")
    _language = language

def get_language() -> str:
    return _language

def read_language(path: Path) -> str:
    """Load only the UI language for startup dialogs, without decrypting credentials."""
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        language = data.get("language", "en")
        return language if language in SUPPORTED_LANGUAGES else "en"
    except (OSError, ValueError, TypeError, AttributeError):
        return "en"

@lru_cache(maxsize=1)
def _patterns():
    patterns = []
    for original, translation in ENGLISH.items():
        if "{" not in original:
            continue
        pieces = []
        last = 0
        for field in re.finditer(r"\{([a-z_]+)\}", original):
            pieces.append(re.escape(original[last:field.start()]))
            pieces.append(f"(?P<{field.group(1)}>.*?)")
            last = field.end()
        pieces.append(re.escape(original[last:]))
        patterns.append((re.compile("^" + "".join(pieces) + "$", re.DOTALL), translation))
    return patterns

def tr(text: str, **values) -> str:
    """Translate UI text; unknown backend errors are returned unchanged."""
    if values:
        text = text.format(**values)
    if _language == "ru":
        return text
    exact = ENGLISH.get(text)
    if exact is not None:
        return exact
    for pattern, translated in _patterns():
        match = pattern.fullmatch(text)
        if match:
            return translated.format(**match.groupdict())
    return text
