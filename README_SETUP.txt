S3Drive — Getting started (Windows 10/11 x64)
English guide. Russian guide: README_SETUP_RU.txt

1. Extract the ENTIRE portable ZIP into one writable folder. Do not run
   S3Drive directly from the ZIP.
2. Run S3Drive.exe as a normal user, NOT as administrator.
3. S3Drive checks WinFsp automatically:
   - If the driver is missing, approve installation of the bundled
     winfsp-2.2.26215.msi and Windows UAC.
   - Complete installation, then start S3Drive.exe again.
   - Without WinFsp, Windows drives cannot be mounted.
4. Create a connection with Endpoint, Access Key, Secret Key, drive letter,
   optional bucket, and Path-style mode (for QNAP/MinIO as needed).
   If the bucket is blank, accessible S3 buckets appear as root folders,
   provided the credentials have ListBuckets permissions.
5. Optionally enable Create desktop shortcut and Open drive after connecting.
6. Click Save to create config.json beside S3Drive.exe.
7. Optionally enable background mode or Run as Windows service.
8. Click Connect; wait until the drive appears in Windows Explorer.
9. To choose Russian: Settings -> Interface language -> Русский -> Save.
   The selected language persists in config.json. English is the default.

BACKGROUND MODE
A regular user background mount can stay active after closing S3Drive.
It does not auto-reconnect after Windows reboot. No tray icon or S3 upload
monitor remains visible while the GUI is closed. Disconnect safely from
S3Drive before replacing files that the background mount may be using.

WINDOWS SERVICE MODE
Use Run as Windows service, click Save, then Connect and approve UAC.
The LocalSystem service starts with Windows; the drive remains available
even when the GUI is closed. Stop it with Stop service and drive. Once it
is stopped, Remove service keeps the saved connection profile intact.
An app update does not automatically replace a running service's
rclone.exe or configuration. Wait for uploads before reconnecting.
Service-mounted drives may be accessible to other local Windows users.

SECURITY AND CONFIGURATION
Access and secret keys are encrypted using Windows DPAPI in config.json.
Keep the file private. The app directory must allow writing config.json.

UPDATES
At launch, S3Drive checks for new stable GitHub Releases in the background.
Nothing downloads automatically; click Update when offered.
S3Drive verifies the downloaded archive before using S3DriveUpdater.exe
to replace application files. Config, services, and VFS caches are preserved.
Active user background mounts can postpone an update until safe disconnect.
Old versions without the updater must be updated manually with the full ZIP.

CAUTION — S3 IS NOT NTFS
Close changed files and wait for S3 uploads before disconnecting.
Do not manually remove the VFS cache if writes may still be pending.
A first-level folder in S3 account-root mode represents a real bucket.
Deleting it may delete that bucket if permissions allow.
Avoid placing live databases or active Git repositories on S3 drives.

rclone.exe is already included. WinFsp 2026 Beta4 (v2.2B4) is a beta
driver; monitor later WinFsp security and stability updates.
A second S3Drive GUI instance in the same session is blocked.

Source: https://github.com/medneem/S3Drive
Licenses: LICENSE, THIRD_PARTY_NOTICES.md, RCLONE_LICENSE.txt


------------------------------------------------------------
РУССКИЙ / RUSSIAN
------------------------------------------------------------

S3Drive — первоначальная установка (Windows 10/11 x64)

1. Распакуйте ВСЁ содержимое архива в одну папку. Не запускайте из ZIP.
2. Запустите S3Drive.exe от обычного пользователя, НЕ от администратора.
3. При запуске S3Drive автоматически проверит наличие WinFsp:
   - Если драйвер уже установлен, откроется основное окно.
   - Если драйвера нет, программа предложит запустить включённый в архив
     winfsp-2.2.26215.msi. Нажмите «Да» и подтвердите UAC в Windows.
   - После запуска MSI S3Drive закроется. Завершите установку WinFsp,
     затем запустите S3Drive.exe снова.
   - При отказе, отсутствии MSI или ошибке установки программа объяснит
     причину и завершится: без WinFsp подключение диска невозможно.
4. Создайте S3-профиль (Endpoint, Access/Secret Key, Path-Style, буква).
   Бакет можно не указывать: тогда диск покажет доступные бакеты как папки
   в корне S3. Для списка нужны права ListBuckets.
   При ограниченных правах впишите имя конкретного бакета.
5. При необходимости включите «Создать ярлык на рабочем столе» и
   «Открыть диск после подключения». Настройки сохраняются в профиле.
6. Нажмите «Сохранить»: рядом с S3Drive.exe создастся config.json.
   При следующих запусках S3Drive автоматически загрузит сохранённые подключения.
7. При необходимости включите «Оставлять диск подключённым после закрытия программы».
8. Нажмите «Подключить». Когда диск появится, S3Drive покажет сообщение
   об успешном подключении и выполнит выбранные действия.

С установленной галочкой можно закрыть окно S3Drive — диск продолжит работать.
При следующем запуске S3Drive обнаружит фоновый процесс. При закрытии GUI
никакого значка в системном трее нет, а отдельное окно загрузки на S3 не
показывается. Для отключения
нажмите «Отключить фон и диск». С незавершённой записью отключение запрещено.

После перезагрузки Windows пользовательское монтирование без службы
не запускается автоматически. Если профиль установлен как служба Windows,
диск запускается службой автоматически, независимо от окна S3Drive.

Access Key и Secret Key сохраняются зашифрованными Windows DPAPI.
Для работы config.json требуется возможность записи в папку приложения.
Не копируйте config.json в общий доступ: на другом ПК DPAPI не расшифрует ключи.

АВТОМАТИЧЕСКАЯ ПРОВЕРКА ОБНОВЛЕНИЙ
При запуске S3Drive проверяет последний опубликованный стабильный GitHub Release
в фоне. Интернет необязателен: при недоступности GitHub приложение работает
как обычно. Если есть новая версия, рядом с номером текущей появится
кнопка «Обновить». Ничего автоматически не скачивается и не устанавливается.

После нажатия «Обновить» S3Drive скачает официальный ZIP, проверит SHA-256
из GitHub Releases и состав архива, а затем штатно закроет окно и запустит
отдельную утилиту S3DriveUpdater.exe. Она дождётся завершения S3Drive,
заменит только файлы программы и перезапустит её. Если не удаётся заменить
файлы, обновлятор пытается вернуть их прежние версии. Установленные профили
и config.json сохраняются. Службы Windows, их копии rclone.exe и VFS-кэши
при обновлении НЕ останавливаются, НЕ заменяются и НЕ очищаются.

Если активен пользовательский фоновый диск, обновление будет отложено:
сначала дождитесь синхронизации и безопасно отключите такой диск.
После обновления службы Windows используют прежний rclone.exe, пока вы
не остановите и заново не подключите их штатным способом. При незавершённых
загрузках делать это нельзя. Старые версии без S3DriveUpdater.exe
обновляются вручную распаковкой полного ZIP (с сохранением config.json).

rclone.exe уже в этом архиве: отдельно скачивать и устанавливать его не нужно.

WinFsp 2026 Beta4 (v2.2B4) — тестовая ветка со свежими исправлениями
безопасности. Проверяйте последующие обновления WinFsp.

ВАЖНО: S3 — не NTFS. Перед отключением диска закройте все файлы и дождитесь
записи данных в S3. Не храните базы данных или работающие Git-репозитории
на таком диске.

Исходники: https://github.com/medneem/S3Drive
Лицензии сторонних компонентов: THIRD_PARTY_NOTICES.md, RCLONE_LICENSE.txt


РЕЖИМ СЛУЖБЫ WINDOWS
- Включите «Запускать как службу Windows (доступно в Проводнике)».
- Нажмите «Сохранить», затем «Подключить» и подтвердите UAC.
- После установки служба стартует автоматически при загрузке Windows,
  диск должен появляться в Проводнике даже при закрытой S3Drive.
- Нажмите «Остановить службу и диск», чтобы корректно отключить диск.
- Нажмите «Удалить службу» после её остановки, если больше не нужна.
- Системная служба запускается как LocalSystem: доступ к диску может быть
  предоставлен другим локальным пользователям ПК.

ВАЖНО ПРИ ОБНОВЛЕНИИ СЛУЖБЫ:
Если служба была установлена предыдущей версией S3Drive, её параметры
остаются прежними. Чтобы применить новые права на удаление файлов:
закройте файлы, дождитесь синхронизации, нажмите «Остановить службу и диск»,
затем снова «Подключить». Службу удалять не требуется.
Новый FileSecurity=...FA... даёт всем локальным пользователям Windows
полные права на файлы и папки этого диска.

ЗАПУСК ПРОГРАММЫ:
Повторный запуск S3Drive.exe в той же пользовательской сессии Windows
покажет сообщение «S3Drive уже запущен» и завершится. Диск и службы
Windows от этого не отключаются.

При обновлении со сборок с треем старая запись автозапуска монитора
S3DriveMonitor (HKCU Run) удаляется при следующем запуске S3Drive.
