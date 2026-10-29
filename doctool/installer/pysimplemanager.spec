# -*- mode: python ; coding: utf-8 -*-
# Сборка PyInstaller: программа целиком в одной папке (onedir), с окном консоли.
# Запускается из installer/build.py (переменные окружения ниже задаёт он); вручную:
#   pyinstaller installer/pysimplemanager.spec --distpath installer/_build/dist --workpath installer/_build/work
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).resolve().parent          # корень программы: doctool/, forms/, config/
TESSERACT = os.environ.get("PYSM_TESSERACT")    # папка с Tesseract для установщика (Windows, macOS), иначе нет
SEVENZIP = os.environ.get("PYSM_7ZIP")         # (0.7.6) папка с 7z.exe и 7z.dll (Windows), иначе нет
ICON = os.environ.get("PYSM_ICON") or None

def tree(src: Path, dest: str) -> list[tuple[str, str]]:
    """Файлы папки для datas - без __pycache__ и служебных файлов git."""
    out = []
    for f in sorted(src.rglob("*")):
        rel = f.relative_to(src)
        if f.is_file() and "__pycache__" not in rel.parts and not f.name.startswith(".git"):
            out.append((str(f), str(Path(dest) / rel.parent)))
    return out


datas = (tree(ROOT / "forms", "forms")          # встроенные модули заявлений → paths.BUNDLED_FORMS
         + tree(ROOT / "config", "config")      # встроенные правила → paths.BUNDLED_CONFIG
         + tree(ROOT / "doctool" / "static", "doctool/static"))
# словари pymorphy3 находятся через метаданные пакета (entry points) - без copy_metadata их не видно
datas += collect_data_files("pymorphy3") + collect_data_files("pymorphy3_dicts_ru") + copy_metadata("pymorphy3_dicts_ru")
if TESSERACT:
    datas.append((TESSERACT, "tesseract"))      # → ocr.find_tesseract: <программа>/_internal/tesseract
if SEVENZIP:
    datas.append((SEVENZIP, "7zip"))            # → archive.find_7z: <программа>/_internal/7zip/7z.exe

a = Analysis(
    [str(ROOT / "installer" / "launcher.py")],
    pathex=[str(ROOT)],
    datas=datas,
    hiddenimports=collect_submodules("doctool"),   # hooks.py модулей импортируют части ядра по имени
    excludes=["tkinter", "PySide6", "PyQt5", "PyQt6", "matplotlib", "pytest", "IPython"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PySimpleManager",
    console=True,                                # окно консоли: закрыли окно - программа остановилась
    icon=ICON,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="PySimpleManager", upx=False)
