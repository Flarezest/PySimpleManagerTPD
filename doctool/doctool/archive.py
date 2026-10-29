"""Распаковка архивов из тикетницы (0.7.6): zip - средствами Python, rar и 7z - программой 7-Zip или UnRAR.

    files, notes = unpack(path, dest)      # файлы архива (вложенные архивы тоже распаковываются)

zip: имена файлов в архивах из Windows без флага UTF-8 записаны в кодировке cp866 - они переводятся, иначе
русские имена превращаются в «Р-Р°СЏРІР»РµРЅРёРµ». Пути внутри архива «обезвреживаются» (без «..» и дисков).
rar, 7z: ищется 7-Zip - в составе программы (установщик для Windows кладёт 7z.exe в папку программы), в PATH,
в «C:\\Program Files\\7-Zip»; для rar - ещё UnRAR (WinRAR). Пароль не запрашивается: архив с паролем - ошибка
с понятным текстом.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import zipfile
from pathlib import Path, PurePosixPath

ARCHIVE_EXT = {".zip", ".rar", ".7z"}
MAX_DEPTH = 2                     # вложенные архивы: архив в архиве в архиве - дальше не распаковываем
MAX_FILES = 300
MAX_TOTAL = 1024 * 1024 * 1024    # 1 ГБ распакованных данных - защита от «zip-бомб»


class ArchiveError(RuntimeError):
    pass


def _app_dir() -> Path:
    from .paths import APP_DIR
    return APP_DIR


def find_7z() -> str | None:
    """7-Zip: в составе программы (_internal/7zip/7z.exe), в PATH (7z, 7zz, 7za), в стандартных папках Windows."""
    own = _app_dir() / "7zip" / ("7z.exe" if os.name == "nt" else "7z")
    cands = [str(own) if own.is_file() else None, shutil.which("7z"), shutil.which("7zz"), shutil.which("7za")]
    if os.name == "nt":
        for env in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
            base = os.environ.get(env)
            if base:
                cands.append(str(Path(base) / "7-Zip" / "7z.exe"))
    return next((c for c in cands if c and Path(c).is_file()), None)


def find_unrar() -> str | None:
    cands = [shutil.which("unrar"), shutil.which("UnRAR")]
    if os.name == "nt":
        for env in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
            base = os.environ.get(env)
            if base:
                cands.append(str(Path(base) / "WinRAR" / "UnRAR.exe"))
    return next((c for c in cands if c and Path(c).is_file()), None)


def is_archive(path: str | Path) -> bool:
    return Path(path).suffix.lower() in ARCHIVE_EXT


def _safe_rel(name: str) -> Path | None:
    """Путь внутри архива → безопасный относительный путь (без «..», дисков, абсолютных путей)."""
    parts = [p for p in PurePosixPath(name.replace("\\", "/")).parts if p not in ("", ".", "..", "/") and ":" not in p]
    return Path(*parts) if parts else None


def _zip_name(info: zipfile.ZipInfo) -> str:
    if info.flag_bits & 0x800:            # имя в UTF-8
        return info.filename
    try:
        return info.filename.encode("cp437").decode("cp866")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return info.filename


def _unzip(path: Path, dest: Path) -> None:
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile as e:
        raise ArchiveError(f"{path.name}: архив повреждён или это не zip ({e})") from e
    with zf:
        total = 0
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if len(infos) > MAX_FILES:
            raise ArchiveError(f"{path.name}: в архиве больше {MAX_FILES} файлов")
        for info in infos:
            if info.flag_bits & 0x1:
                raise ArchiveError(f"{path.name}: архив защищён паролем - распакуйте его вручную")
            total += info.file_size
            if total > MAX_TOTAL:
                raise ArchiveError(f"{path.name}: распакованные файлы больше 1 ГБ")
            rel = _safe_rel(_zip_name(info))
            if rel is None:
                continue
            out = dest / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, out.open("wb") as dst:
                shutil.copyfileobj(src, dst)


def _run(cmd: list[str], what: str) -> subprocess.CompletedProcess:
    kw = {"creationflags": 0x08000000} if os.name == "nt" else {}      # CREATE_NO_WINDOW: без чёрного окна
    try:
        return subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, timeout=180, **kw)
    except subprocess.TimeoutExpired as e:
        raise ArchiveError(f"{what}: распаковка не уложилась в 3 минуты") from e
    except OSError as e:
        raise ArchiveError(f"{what}: не удалось запустить {cmd[0]} ({e})") from e


def _out_text(p: subprocess.CompletedProcess) -> str:
    raw = (p.stderr or b"") + b"\n" + (p.stdout or b"")
    for enc in ("utf-8", "cp866", "cp1251"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _external(path: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    seven = find_7z()
    tried = []
    if seven:
        # -p: пароль заведомо неверный - архив с паролем не повиснет на вопросе о пароле, а вернёт ошибку
        p = _run([seven, "x", "-y", "-bd", "-pPySimpleManager", f"-o{dest}", str(path)], path.name)
        if p.returncode == 0:
            return
        out = _out_text(p)
        if "password" in out.lower() or "парол" in out.lower():
            raise ArchiveError(f"{path.name}: архив защищён паролем - распакуйте его вручную")
        tried.append(f"7-Zip: {out.strip().splitlines()[-1] if out.strip() else p.returncode}")
    if path.suffix.lower() == ".rar":
        unrar = find_unrar()
        if unrar:
            p = _run([unrar, "x", "-y", "-p-", "-idq", str(path), str(dest) + os.sep], path.name)
            if p.returncode == 0:
                return
            out = _out_text(p)
            if "password" in out.lower() or "парол" in out.lower():
                raise ArchiveError(f"{path.name}: архив защищён паролем - распакуйте его вручную")
            tried.append(f"UnRAR: {out.strip().splitlines()[-1] if out.strip() else p.returncode}")
    if not tried:
        raise ArchiveError(f"{path.name}: для архивов {path.suffix.lower()} нужен 7-Zip (https://7-zip.org) или WinRAR. "
                           "В установленной программе 7-Zip уже есть; при запуске из исходников установите 7-Zip "
                           "или распакуйте архив и перетащите файлы.")
    raise ArchiveError(f"{path.name}: архив не распаковался ({'; '.join(tried)})")


def unpack(path: str | Path, dest: str | Path, depth: int = 0) -> tuple[list[Path], list[str]]:
    """Распаковать архив в dest. Возвращает файлы (не архивы) по порядку имён и замечания.
    Вложенные архивы распаковываются в папку рядом (до MAX_DEPTH уровней)."""
    path, dest = Path(path), Path(dest)
    notes: list[str] = []
    if path.suffix.lower() == ".zip":
        _unzip(path, dest)
    else:
        _external(path, dest)
    files: list[Path] = []
    for f in sorted((x for x in dest.rglob("*") if x.is_file()), key=lambda x: natural_key(str(x.relative_to(dest)))):
        if is_archive(f):
            if depth + 1 > MAX_DEPTH:
                notes.append(f"{f.name}: вложенный архив слишком глубоко - не распакован")
                files.append(f)
                continue
            sub = f.with_name(f.name + "_файлы")
            try:
                inner, more = unpack(f, sub, depth + 1)
                files += inner
                notes += more
                notes.append(f"{f.name}: вложенный архив распакован")
            except ArchiveError as e:
                notes.append(str(e))
                files.append(f)
            continue
        if f.name.startswith(".") or f.name.lower() in ("thumbs.db", "desktop.ini") or "__MACOSX" in f.parts:
            continue
        files.append(f)
    return files, notes


def natural_key(s: str) -> list:
    """Сортировка «как человек»: стр2 < стр10."""
    import re
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]
