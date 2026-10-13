"""Сборка установщика PySimpleManager на текущей системе (0.7.5).

    python installer/build.py                 # всё: Tesseract → PyInstaller → проверка → установщик
    python installer/build.py --no-smoke      # без проверки сборки (installer/smoke_test.py)

Запускается из корня программы (рядом с папками doctool, forms, config), в окружении с пакетами
requirements.txt, requirements-web.txt и installer/requirements-build.txt. Обычно его запускает GitHub Actions
(.github/workflows/build.yml в корне репозитория); подробно — docs/RELEASE.md.

Результат — в installer/_out/:
  Windows  PySimpleManager-<версия>-windows-x64-setup.exe   (Inno Setup; Tesseract внутри)
  Linux    pysimplemanager_<версия>_amd64.deb                 (Tesseract — зависимость пакета)
  macOS    PySimpleManager-<версия>-macos-<arch>.dmg          (Tesseract внутри; сборка пробная)
Промежуточные файлы — installer/_build/ (кэш скачанного Tesseract там же).
"""
from __future__ import annotations

import argparse
import hashlib
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BUILD = HERE / "_build"
OUT = HERE / "_out"
DIST = BUILD / "dist" / "PySimpleManager"
APP = "PySimpleManager"

# Tesseract для Windows: сборка UB Mannheim (её же ставят по README). Файл проверяется по SHA-256.
WIN_TESSERACT = ("https://github.com/UB-Mannheim/tesseract/releases/download/v5.4.0.20240606/"
                 "tesseract-ocr-w64-setup-5.4.0.20240606.exe",
                 "c885fff6998e0608ba4bb8ab51436e1c6775c2bafc2559a19b423e18678b60c9")
# Языки — tesseract-ocr/tessdata 4.1.0 (README советует брать rus.traineddata оттуда).
TESSDATA = {
    "rus": "681be2c2bead1bc7bd235df88c44e8e60ae73ae866840c0ad4e3b4c247bd37c2",
    "eng": "daa0c97d651c19fba3b25e81317cd697e9908c8208090c94c3905381c23fc047",
    "osd": "e19f2ae860792fdf372cf48d8ce70ae5da3c4052962fe22e9de1f680c374bb0e",
}
TESSDATA_URL = "https://raw.githubusercontent.com/tesseract-ocr/tessdata/4.1.0/{}.traineddata"


def log(msg: str) -> None:
    print(f"\n==> {msg}", flush=True)


def sh(*cmd, **kw) -> None:
    print("   $ " + " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], check=True, **kw)


def version() -> str:
    m = re.search(r'__version__ = "([^"]+)"', (ROOT / "doctool" / "__init__.py").read_text(encoding="utf-8"))
    v = m.group(1)
    if os.environ.get("GITHUB_REF_TYPE") == "tag":          # сборка по тегу: тег = версия программы
        tag = os.environ.get("GITHUB_REF_NAME", "")
        if tag != f"v{v}":
            sys.exit(f"[!] Тег {tag} не совпадает с версией программы {v} (doctool/__init__.py). "
                     f"Поставьте тег v{v} или поправьте версию.")
    return v


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, dest: Path, digest: str) -> Path:
    """Скачивает файл (если его ещё нет в кэше) и проверяет SHA-256."""
    if not dest.exists() or sha256(dest) != digest:
        dest.parent.mkdir(parents=True, exist_ok=True)
        print(f"   скачиваю {url}", flush=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        with urllib.request.urlopen(url, timeout=300) as r, tmp.open("wb") as f:
            shutil.copyfileobj(r, f)
        got = sha256(tmp)
        if got != digest:
            tmp.unlink()
            sys.exit(f"[!] {dest.name}: SHA-256 {got}, ожидалось {digest}")
        tmp.replace(dest)
    return dest


def tessdata(dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for lang, digest in TESSDATA.items():
        f = download(TESSDATA_URL.format(lang), BUILD / "cache" / f"{lang}.traineddata", digest)
        shutil.copy2(f, dest / f.name)


# ------------------------------------------------------------------ Tesseract

def seven_zip() -> str:
    for c in (shutil.which("7z"), shutil.which("7za"), r"C:\Program Files\7-Zip\7z.exe"):
        if c and Path(c).exists():
            return c
    sys.exit("[!] Нужен 7-Zip (7z), чтобы распаковать установщик Tesseract")


def dll_closure(folder: Path, exe: str) -> list[str]:
    """DLL из папки, которые нужны exe (по таблицам импорта, рекурсивно). Остальное в установщике Tesseract —
    программы и библиотеки для обучения моделей."""
    import pefile
    avail = {f.name.lower(): f.name for f in folder.glob("*.dll")}
    need, todo = set(), [exe]
    while todo:
        pe = pefile.PE(str(folder / todo.pop()), fast_load=True)
        pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"],
                                               pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT"]])
        for attr in ("DIRECTORY_ENTRY_IMPORT", "DIRECTORY_ENTRY_DELAY_IMPORT"):
            for entry in getattr(pe, attr, []):
                name = entry.dll.decode("ascii", "replace").lower()
                if name in avail and name not in need:
                    need.add(name)
                    todo.append(avail[name])
        pe.close()
    return sorted(avail[n] for n in need)


def tesseract_windows() -> Path:
    """Tesseract из установщика UB Mannheim без его запуска: распаковка 7-Zip, только tesseract.exe с нужными DLL,
    configs/tessconfigs и языки rus, eng, osd."""
    url, digest = WIN_TESSERACT
    setup = download(url, BUILD / "cache" / url.rsplit("/", 1)[1], digest)
    raw = BUILD / "tesseract_raw"
    shutil.rmtree(raw, ignore_errors=True)
    sh(seven_zip(), "x", "-y", f"-o{raw}", setup, stdout=subprocess.DEVNULL)
    dest = BUILD / "tesseract"
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    shutil.copy2(raw / "tesseract.exe", dest)
    dlls = dll_closure(raw, "tesseract.exe")
    for name in dlls:
        shutil.copy2(raw / name, dest)
    for sub in ("configs", "tessconfigs"):
        shutil.copytree(raw / "tessdata" / sub, dest / "tessdata" / sub)
    tessdata(dest / "tessdata")
    print(f"   tesseract.exe + {len(dlls)} DLL")
    return dest


def tesseract_macos() -> Path:
    """Tesseract из Homebrew (brew install tesseract dylibbundler) с библиотеками рядом (@executable_path/libs)."""
    def brew(*args: str) -> Path:
        return Path(subprocess.run(["brew", "--prefix", *args], check=True, capture_output=True,
                                   text=True).stdout.strip())

    prefix = brew("tesseract")
    dest = BUILD / "tesseract"
    shutil.rmtree(dest, ignore_errors=True)
    (dest / "libs").mkdir(parents=True)
    exe = dest / "tesseract"
    shutil.copy2((prefix / "bin" / "tesseract").resolve(), exe)
    exe.chmod(0o755)
    # если библиотека не найдётся, dylibbundler спрашивает папку — отвечаем quit: ошибка вместо ожидания
    sh("dylibbundler", "-of", "-b", "-x", exe, "-d", dest / "libs", "-p", "@executable_path/libs/",
       "-s", brew() / "lib", input="quit\n", text=True, timeout=900)
    for f in [exe, *sorted((dest / "libs").glob("*.dylib"))]:
        f.chmod(0o755)
        sh("codesign", "--force", "--sign", "-", f)             # подпись «ad hoc»: без неё arm64 не запустит
    share = prefix / "share" / "tessdata"
    for sub in ("configs", "tessconfigs"):
        if (share / sub).is_dir():
            shutil.copytree(share / sub, dest / "tessdata" / sub)
    tessdata(dest / "tessdata")
    return dest


def check_tesseract(folder: Path) -> None:
    exe = folder / ("tesseract.exe" if os.name == "nt" else "tesseract")
    env = {**os.environ, "TESSDATA_PREFIX": str(folder / "tessdata")}
    out = subprocess.run([str(exe), "--list-langs"], env=env, capture_output=True, text=True, check=True)
    langs = set((out.stdout + out.stderr).split())
    missing = {"rus", "eng", "osd"} - langs
    if missing:
        sys.exit(f"[!] Tesseract: нет языков {missing}\n{out.stdout}{out.stderr}")
    print("   Tesseract в составе программы: " + out.stdout.strip().splitlines()[0])


# ------------------------------------------------------------------ сборка

def icon() -> Path | None:
    png = ROOT / "doctool_extension" / "icons" / "icon128.png"
    if os.name != "nt" or not png.exists():
        return None
    from PIL import Image
    ico = BUILD / "pysimplemanager.ico"
    ico.parent.mkdir(parents=True, exist_ok=True)
    Image.open(png).save(ico, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128)])
    return ico


def pyinstaller(tess: Path | None, ico: Path | None) -> Path:
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    env.pop("PYSM_TESSERACT", None)
    if tess:
        env["PYSM_TESSERACT"] = str(tess)
    if ico:
        env["PYSM_ICON"] = str(ico)
    shutil.rmtree(DIST, ignore_errors=True)
    sh(sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--log-level", "WARN",
       "--distpath", BUILD / "dist", "--workpath", BUILD / "work", HERE / "pysimplemanager.spec", cwd=ROOT, env=env)
    exe = DIST / (APP + (".exe" if os.name == "nt" else ""))
    if not exe.exists():
        sys.exit(f"[!] PyInstaller не создал {exe}")
    return exe


EXT_README = """\
Расширение Chrome «PySimpleManager» — загрузка данных доменов и аккаунтов из manager
====================================================================================

Ставится один раз:

1. Откройте в Chrome адрес chrome://extensions
2. Включите «Режим разработчика» (переключатель справа вверху).
3. Нажмите «Загрузить распакованное расширение» и выберите ЭТУ папку:
   {path}
4. Запустите PySimpleManager — в окне расширения (значок на панели Chrome) появится «PySimpleManager подключён».

После обновления программы нажмите на карточке расширения в chrome://extensions кнопку «Обновить» (круглая стрелка).
Папку не перемещайте и не удаляйте: Chrome загружает расширение из неё.

Подробно — docs/MANAGER.md (в папке программы).
"""


def extras() -> None:
    """Рядом с программой: расширение Chrome, документация, README, CHANGELOG."""
    ext = DIST / "chrome_extension"
    shutil.copytree(ROOT / "doctool_extension", ext, ignore=shutil.ignore_patterns("__pycache__"))
    if os.name == "nt":
        where = (r"%LOCALAPPDATA%\Programs\PySimpleManager\chrome_extension" "\n"
                 "   (Пуск → PySimpleManager → «Расширение Chrome (папка)»)")
    elif sys.platform.startswith("linux"):
        where = "/opt/pysimplemanager/chrome_extension"
    else:
        where = "/Applications/PySimpleManager/chrome_extension (если папку программы перенесли в «Программы»)"
    (ext / "КАК_УСТАНОВИТЬ.txt").write_text(EXT_README.format(path=where), encoding="utf-8-sig")
    (DIST / "docs").mkdir(exist_ok=True)
    for f in ("USAGE.md", "MANAGER.md", "RELEASE.md"):
        if (ROOT / "docs" / f).exists():
            shutil.copy2(ROOT / "docs" / f, DIST / "docs" / f)
    for f in ("README.md", "CHANGELOG.md"):
        if (ROOT / f).exists():
            shutil.copy2(ROOT / f, DIST / f)


def iscc() -> str:
    for c in (shutil.which("iscc"), shutil.which("ISCC"), r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
              r"C:\Program Files\Inno Setup 6\ISCC.exe"):
        if c and Path(c).exists():
            return c
    sys.exit("[!] Не найден Inno Setup 6 (ISCC.exe). Установите: choco install innosetup")


def package_windows(v: str, ico: Path | None) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    args = [iscc(), "/Q", f"/DAppVersion={v}", f"/DSourceDir={DIST}", f"/DOutDir={OUT}"]
    if ico:
        args.append(f"/DIconFile={ico}")
    sh(*args, HERE / "windows" / "pysimplemanager.iss")
    return OUT / f"{APP}-{v}-windows-x64-setup.exe"


DESKTOP = """\
[Desktop Entry]
Type=Application
Name=PySimpleManager
Comment=Проверка заявлений и паспортов РФ
Exec=pysimplemanager
Icon=pysimplemanager
Terminal=true
Categories=Office;Utility;
"""

CONTROL = """\
Package: pysimplemanager
Version: {v}
Section: utils
Priority: optional
Architecture: amd64
Depends: tesseract-ocr, tesseract-ocr-rus
Recommends: xdg-user-dirs, xdg-utils
Installed-Size: {kb}
Maintainer: PySimpleManager <noreply@github.com>
Homepage: https://github.com/Flarezest/PyTextRecognitionTPD
Description: PySimpleManager - проверка заявлений и паспортов РФ
 Локальная проверка заявлений и паспортов РФ: веб-интерфейс в браузере,
 сверка с данными manager через расширение Chrome (папка
 /opt/pysimplemanager/chrome_extension).
"""


def package_linux(v: str) -> Path:
    root = BUILD / "deb" / f"pysimplemanager_{v}_amd64"
    shutil.rmtree(root.parent, ignore_errors=True)
    opt = root / "opt" / "pysimplemanager"
    shutil.copytree(DIST, opt, symlinks=True)
    (root / "usr" / "bin").mkdir(parents=True)
    os.symlink("/opt/pysimplemanager/PySimpleManager", root / "usr" / "bin" / "pysimplemanager")
    apps = root / "usr" / "share" / "applications"
    apps.mkdir(parents=True)
    (apps / "pysimplemanager.desktop").write_text(DESKTOP, encoding="utf-8")
    icons = root / "usr" / "share" / "icons" / "hicolor" / "128x128" / "apps"
    icons.mkdir(parents=True)
    shutil.copy2(ROOT / "doctool_extension" / "icons" / "icon128.png", icons / "pysimplemanager.png")
    kb = sum(f.stat().st_size for f in root.rglob("*") if f.is_file() and not f.is_symlink()) // 1024
    (root / "DEBIAN").mkdir()
    (root / "DEBIAN" / "control").write_text(CONTROL.format(v=v, kb=kb), encoding="utf-8")
    for d in [root, *[p for p in root.rglob("*") if p.is_dir() and not p.is_symlink()]]:
        d.chmod(0o755)
    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / f"pysimplemanager_{v}_amd64.deb"
    sh("dpkg-deb", "--build", "--root-owner-group", "-Zxz", root, out)
    return out


COMMAND = """\
#!/bin/bash
# Запуск PySimpleManager в окне Терминала. Закройте окно — программа остановится.
cd "$(dirname "$0")" || exit 1
# Первый запуск: macOS разрешил этот файл («Конфиденциальность и безопасность → Всё равно открыть») —
# снимаем карантин с остальных файлов программы, иначе система спросит про каждый.
xattr -dr com.apple.quarantine . 2>/dev/null
exec ./PySimpleManager "$@"
"""


def package_macos(v: str) -> Path:
    stage = BUILD / "dmg"
    shutil.rmtree(stage, ignore_errors=True)
    app_dir = stage / APP
    shutil.copytree(DIST, app_dir, symlinks=True)
    cmd = app_dir / "Запустить PySimpleManager.command"
    cmd.write_text(COMMAND, encoding="utf-8")
    cmd.chmod(0o755)
    os.symlink("/Applications", stage / "Applications")
    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / f"{APP}-{v}-macos-{platform.machine()}.dmg"
    out.unlink(missing_ok=True)
    for attempt in range(3):            # hdiutil на машинах GitHub иногда отвечает «Resource busy»
        try:
            sh("hdiutil", "create", "-volname", f"{APP} {v}", "-srcfolder", stage, "-ov", "-format", "UDZO", out)
            break
        except subprocess.CalledProcessError:
            if attempt == 2:
                raise
            time.sleep(10)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Сборка установщика PySimpleManager на текущей системе")
    ap.add_argument("--no-smoke", action="store_true", help="не проверять собранную программу")
    ap.add_argument("--no-package", action="store_true", help="только папка программы, без установщика")
    args = ap.parse_args()
    v = version()
    log(f"{APP} {v}: {platform.system()} {platform.machine()}, Python {platform.python_version()}")
    tess = None
    if os.name == "nt":
        log("Tesseract для Windows")
        tess = tesseract_windows()
    elif sys.platform == "darwin":
        log("Tesseract для macOS")
        tess = tesseract_macos()
    if tess:
        check_tesseract(tess)
    ico = icon()
    log("PyInstaller")
    exe = pyinstaller(tess, ico)
    extras()
    if not args.no_smoke:
        log("Проверка собранной программы")
        sh(sys.executable, HERE / "smoke_test.py", exe)
    if args.no_package:
        return
    log("Установщик")
    if os.name == "nt":
        out = package_windows(v, ico)
    elif sys.platform == "darwin":
        out = package_macos(v)
    else:
        out = package_linux(v)
    print(f"\nГотово: {out} ({out.stat().st_size / 1e6:.0f} МБ)")


if __name__ == "__main__":
    main()
