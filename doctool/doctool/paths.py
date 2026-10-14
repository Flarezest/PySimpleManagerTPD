"""Где программа берёт описания бланков и правила и куда пишет результаты (0.7.5).

Два режима:

- **Запуск из исходников** (`python -m doctool …`, `doctool_web.bat`) — как до 0.7.5: результаты в `results/`
  текущей папки, бланки — `forms/`, правила — `config/case_types.yaml` рядом с пакетом.
- **Установленная программа** (сборка PyInstaller) или переменная `DOCTOOL_HOME` — папка данных пользователя:
  `Документы/PySimpleManager` (или путь из `DOCTOOL_HOME`):

      results/                 результаты проверок и реестр_проверок.xlsx
      forms/<id>/              свои модули заявлений; модуль с тем же id заменяет встроенный
      config/case_types.yaml   свои типы заявлений и правила вердикта (если файла нет — встроенные)
      config/workspace.yaml    (0.7.6) своя строка таблицы и проверки доменов рабочего места
      ПРОЧТИ.txt               что где лежит

  Обновление программы папку данных не трогает.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

APP_FOLDER = "PySimpleManager"
# корень программы: forms/, config/ (в сборке PyInstaller — папка _internal)
APP_DIR = Path(__file__).resolve().parent.parent
FROZEN = bool(getattr(sys, "frozen", False))
BUNDLED_FORMS = APP_DIR / "forms"
BUNDLED_CONFIG = APP_DIR / "config" / "case_types.yaml"
BUNDLED_WORKSPACE = APP_DIR / "config" / "workspace.yaml"     # (0.7.6) рабочее место: строка таблицы, проверки доменов
README_NAME = "ПРОЧТИ.txt"


def _windows_documents() -> Path | None:
    """Папка «Документы» из Windows (учитывает перенос в OneDrive и на другой диск)."""
    import ctypes
    import uuid
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                    ("Data4", wintypes.BYTE * 8)]

    folder_id = GUID.from_buffer_copy(uuid.UUID("{FDD39AD0-238F-46AF-ADB4-6C85480369C7}").bytes_le)  # Documents
    buf = ctypes.c_wchar_p()
    if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(folder_id), 0, None, ctypes.byref(buf)) != 0:
        return None
    try:
        return Path(buf.value) if buf.value else None
    finally:
        ctypes.windll.ole32.CoTaskMemFree(buf)


def _xdg_documents() -> Path | None:
    """Папка документов Linux (xdg-user-dir: «Документы» в русской системе)."""
    try:
        out = subprocess.run(["xdg-user-dir", "DOCUMENTS"], capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    # без настроенных папок xdg-user-dir отвечает домашней папкой
    return Path(out) if out and Path(out) != Path.home() else None


def documents_dir() -> Path:
    found = None
    try:
        if os.name == "nt":
            found = _windows_documents()
        elif sys.platform.startswith("linux"):
            found = _xdg_documents()
    except Exception:                       # не мешаем запуску: ниже — обычная папка Documents
        found = None
    return found or Path.home() / "Documents"


def user_dir() -> Path | None:
    """Папка данных пользователя или None (запуск из исходников без DOCTOOL_HOME — всё как до 0.7.5)."""
    env = os.environ.get("DOCTOOL_HOME")
    if env:
        return Path(env).expanduser()
    if FROZEN:
        return documents_dir() / APP_FOLDER
    return None


def results_dir() -> str:
    """Папка результатов по умолчанию (параметр -o/--out)."""
    u = user_dir()
    return str(u / "results") if u else "results"


def user_forms_dir() -> Path | None:
    u = user_dir()
    return u / "forms" if u else None


def forms_dirs(bundled: Path = BUNDLED_FORMS) -> list[Path]:
    """Где искать модули заявлений: сначала свои (папка данных), затем встроенные."""
    own = user_forms_dir()
    return ([own] if own and own.is_dir() else []) + [Path(bundled)]


def config_file() -> Path:
    """Типы заявлений и правила вердикта: свой файл из папки данных, если он есть, иначе встроенный."""
    u = user_dir()
    own = u / "config" / "case_types.yaml" if u else None
    return own if own and own.is_file() else BUNDLED_CONFIG


def workspace_config_file() -> Path:
    """(0.7.6) Настройки рабочего места (строка таблицы, проверки доменов): свой config/workspace.yaml из папки
    данных, если он есть, иначе встроенный."""
    u = user_dir()
    own = u / "config" / "workspace.yaml" if u else None
    return own if own and own.is_file() else BUNDLED_WORKSPACE


def ensure_user_dir() -> Path | None:
    """Создаёт папку данных (results, forms, config) и ПРОЧТИ.txt. None — режим исходников."""
    u = user_dir()
    if u is None:
        return None
    for sub in ("results", "forms", "config"):
        (u / sub).mkdir(parents=True, exist_ok=True)
    exe = f'"{sys.executable}"' if FROZEN else "python -m doctool"
    text = README_TEXT.replace("\\", os.sep).format(data=u, forms=BUNDLED_FORMS, config=BUNDLED_CONFIG, exe=exe)
    readme = u / README_NAME
    try:
        if not readme.exists() or readme.read_text(encoding="utf-8-sig") != text:
            readme.write_text(text, encoding="utf-8-sig")      # с BOM — чтобы Блокнот не путал кодировку
    except OSError:
        pass
    return u


def describe() -> list[str]:
    """Строки для вывода: какие папки и файлы использует программа."""
    from .formspec import forms_dirs as dirs
    u = user_dir()
    return [f"Папка данных: {u}" if u else "Папка данных: нет (запуск из исходников, results — в текущей папке)",
            "Модули заявлений: " + "; ".join(str(d) for d in dirs()),
            f"Типы заявлений и правила: {config_file()}",
            f"Рабочее место (строка таблицы, проверки доменов): {workspace_config_file()}"]


README_TEXT = """\
PySimpleManager — папка данных
==============================

{data}

results\\        результаты проверок: папка на каждое дело (report.html, export.json, result.json, crops)
                и общий реестр_проверок.xlsx
forms\\          свои модули заявлений (необязательно). Модуль — папка forms\\<id>\\ с form.yaml
                (README.md в папке программы, раздел «Как добавить новое заявление»).
                Модуль с тем же id, что у встроенного, заменяет встроенный.
config\\         свои типы заявлений и правила вердикта (необязательно): файл config\\case_types.yaml.
                Если файла нет, действуют встроенные правила.
                Своя строка таблицы и проверки доменов рабочего места — config\\workspace.yaml
                (копия встроенного workspace.yaml из папки программы).

Встроенные модули заявлений: {forms}
Встроенные правила:          {config}

Чтобы изменить правила, скопируйте встроенный case_types.yaml в папку config\\ и правьте копию.
Учтите: пока лежит своя копия, изменения правил в новых версиях программы к вам не попадут —
после обновления сравните свою копию со встроенным файлом.

Проверить свои описания (в командной строке): {exe} validate

Обновление и удаление программы эту папку не трогают.
"""
