# Выпуск версии и установщики (с 0.7.5)

Установщики для Windows, Linux и macOS собирает **GitHub Actions** — автоматизация GitHub. По тегу версии GitHub
запускает свои временные компьютеры (Windows, Ubuntu, macOS), собирает на них программу, проверяет её и выкладывает
установщики на страницу **Releases** репозитория. Для публичного репозитория это бесплатно.

Инструкция для GitHub — файл `.github/workflows/build.yml` **в корне репозитория** (рядом с папкой `doctool`;
в другом месте GitHub его не увидит). Всё остальное — в папке программы, `installer/`.

## Как выпустить версию

1. Поднимите версию в `doctool/__init__.py` и допишите раздел в `CHANGELOG.md` (тест сверяет их между собой).
2. Закоммитьте и отправьте изменения: `git push`.
3. Поставьте тег **ровно с этой версией** и отправьте его:

   ```
   git tag v0.7.5
   git push origin v0.7.5
   ```

   Если тег не совпадает с версией программы, сборка остановится с ошибкой — так в Releases не попадёт
   установщик с чужим номером версии.
4. Откройте вкладку **Actions** репозитория: там запуск «Сборка установщиков» с тремя задачами — Windows, Linux,
   macOS. У каждого шага — зелёная галка или красный крест; щелчок по шагу показывает его вывод.
5. Когда Windows и Linux готовы, появится задача «Выпуск (Releases)», а на странице **Releases** — выпуск
   «PySimpleManager v0.7.5» с файлами:

   | Файл | Для чего |
   |---|---|
   | `PySimpleManager-0.7.5-windows-x64-setup.exe` | Windows 10/11, Tesseract внутри |
   | `pysimplemanager_0.7.5_amd64.deb` | Ubuntu 22.04+ / Debian 12, Tesseract ставится зависимостью |
   | `PySimpleManager-0.7.5-macos-arm64.dmg` | macOS на Apple Silicon — пробная сборка, может отсутствовать |

   Текст выпуска (как установить) — `installer/release_notes.md`.

Сборка идёт несколько минут на каждую систему, все три — одновременно. Ссылку на файл из Releases можно давать
коллегам.

**Пробная сборка без выпуска:** Actions → «Сборка установщиков» → **Run workflow** (кнопка справа) → выбрать ветку
→ Run workflow. Установщики появятся внизу страницы запуска в разделе **Artifacts** (хранятся 90 дней), в Releases
ничего не попадёт. Тег для этого не нужен.

**Если сборка упала:** откройте красный шаг и скопируйте последние строки вывода. Ошибка macOS не мешает выпуску
для Windows и Linux. Чтобы пересобрать тот же тег после исправления: удалите тег (`git tag -d v0.7.5`,
`git push origin :refs/tags/v0.7.5`) и черновой выпуск в Releases, если он появился, затем поставьте тег заново.

## Что делает сборка

На каждой системе (`installer/build.py`):

1. **Tesseract.** Windows — установщик UB Mannheim 5.4.0 (тот же, что в README), распаковывается 7-Zip без
   установки; берутся `tesseract.exe` и только нужные ему DLL (по таблицам импорта) — без программ обучения моделей.
   macOS — Tesseract из Homebrew с библиотеками рядом. Языки `rus`, `eng`, `osd` — из tesseract-ocr/tessdata 4.1.0.
   Все скачанные файлы сверяются по SHA-256 (суммы — в начале `build.py`). Linux — Tesseract из системы
   (зависимость пакета `tesseract-ocr-rus`).
2. **PyInstaller** (`installer/pysimplemanager.spec`) — Python и все пакеты внутри, одна папка `PySimpleManager`
   с `PySimpleManager[.exe]` и `_internal/` (встроенные `forms/`, `config/`, Tesseract).
3. Рядом с программой: `chrome_extension/` (расширение + `КАК_УСТАНОВИТЬ.txt`), `docs/`, `README.md`, `CHANGELOG.md`.
4. **Проверка собранной программы** (`installer/smoke_test.py`): версия; `validate`; папка данных
   «Документы/PySimpleManager»; `check` по образцам модулей (поля = `*.expected.json`); `check` по картинке —
   работает Tesseract из состава программы; веб-интерфейс отвечает; повторный запуск видит запущенную программу.
5. **Установщик:** Windows — Inno Setup (`installer/windows/pysimplemanager.iss`), Linux — `.deb`, macOS — `.dmg`.
6. GitHub ставит готовый установщик (Windows — тихая установка, Linux — `apt install`) и ещё раз запускает
   проверку уже **установленной** программы. Тесты (`pytest`) идут перед сборкой на Linux.

## Установленная программа

| | Windows | Linux | macOS |
|---|---|---|---|
| Куда ставится | `%LOCALAPPDATA%\Programs\PySimpleManager` (без прав администратора) | `/opt/pysimplemanager`, команда `pysimplemanager` | куда перетащили папку (обычно «Программы») |
| Запуск | ярлык «PySimpleManager» (Пуск, рабочий стол) | «PySimpleManager» в меню приложений | `Запустить PySimpleManager.command` |
| Расширение Chrome | Пуск → PySimpleManager → «Расширение Chrome (папка)» | `/opt/pysimplemanager/chrome_extension` | `chrome_extension` в папке программы |

- Запуск открывает браузер и окно консоли; закрыли окно — программа остановилась. Повторный запуск, пока программа
  работает, просто открывает её страницу. При ошибке окно не закрывается, пока не нажать Enter.
- Данные — «Документы/PySimpleManager»: `results/` (дела и реестр), свои модули заявлений `forms/` и правила
  `config/case_types.yaml` поверх встроенных (`ПРОЧТИ.txt` там же). Обновление и удаление программы их не трогают.
- Командная строка та же, что `python -m doctool`: `PySimpleManager.exe check -a заявление.pdf`, `… validate`.
- Обновление — новый установщик поверх старого. Расширение Chrome после обновления — кнопка ⟳ на его карточке.
- Windows без сертификата подписи: «Система Windows защитила ваш компьютер» → «Подробнее» → «Выполнить в любом
  случае». macOS без нотаризации Apple: при первом запуске «Системные настройки → Конфиденциальность и
  безопасность → Всё равно открыть»; дальше `.command` сам снимает карантин с файлов программы.

## Собрать у себя

Нужны Python 3.12 и пакеты; на Windows ещё 7-Zip и Inno Setup 6, на Linux — `dpkg-deb` и Tesseract, на macOS —
`brew install tesseract dylibbundler`. Из папки программы:

```
pip install -r requirements.txt -r requirements-web.txt -r installer/requirements-build.txt
python installer/build.py
```

Результат — `installer/_out/`, промежуточные файлы — `installer/_build/` (обе папки в `installer/.gitignore`).
`--no-package` — только папка программы, `--no-smoke` — без проверки.

## Обновить Tesseract

В `installer/build.py`: `WIN_TESSERACT` (адрес установщика UB Mannheim и его SHA-256), `TESSDATA` (суммы файлов
языков). SHA-256 файла: `certutil -hashfile файл SHA256` (Windows) или `sha256sum файл`. После смены версии
Tesseract прочтения сканов могут немного измениться — прогоните наборы (`check_all.bat`).
