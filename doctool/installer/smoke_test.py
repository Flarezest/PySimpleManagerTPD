"""Проверка собранной или установленной программы (0.7.5): запускает её как пользователь и смотрит на результат.

    python installer/smoke_test.py <путь к PySimpleManager[.exe]>

Что проверяется:
1. --version совпадает с doctool/__init__.py;
2. validate без DOCTOOL_HOME — папка данных «Документы/PySimpleManager» и ПРОЧТИ.txt;
3. check по образцам модулей (DOCX, PDF): поля совпадают с <образец>.expected.json;
4. check по картинке (страница PDF-образца → PNG): работает Tesseract из состава программы, найдены домены;
5. web: /api/meta отвечает; повторный запуск (без аргументов, как с ярлыка) видит запущенную программу
   и завершается.

Нужен pymupdf (есть в requirements.txt). Временные файлы — во временной папке, она удаляется.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAILS: list[str] = []


def ok(cond: bool, what: str) -> bool:
    print(("  ✓ " if cond else "  ✗ ") + what, flush=True)
    if not cond:
        FAILS.append(what)
    return cond


def run(exe: str, args: list[str], env: dict, timeout: int = 600, stdin=subprocess.DEVNULL) -> subprocess.CompletedProcess:
    p = subprocess.run([exe, *args], env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=timeout, stdin=stdin)
    if p.returncode != 0:
        print(f"    код {p.returncode}\n    stdout: {p.stdout[-2000:]}\n    stderr: {p.stderr[-2000:]}")
    return p


def expected_version() -> str:
    m = re.search(r'__version__ = "([^"]+)"', (ROOT / "doctool" / "__init__.py").read_text(encoding="utf-8"))
    return m.group(1)


def case_dir(home: Path, stem: str) -> Path | None:
    found = sorted((home / "results").glob(f"*_{stem}"))
    return found[-1] if found else None


def check_sample(exe: str, env: dict, home: Path, src: Path, exp: Path) -> None:
    p = run(exe, ["check", "-a", str(src)], env)
    d = case_dir(home, src.stem)
    if not ok(p.returncode == 0 and d is not None and (d / "result.json").exists(), f"check {src.name}"):
        return
    fields = json.loads((d / "result.json").read_text(encoding="utf-8"))["application"]["fields"]
    want = json.loads(exp.read_text(encoding="utf-8"))["fields"]
    bad = {}
    for key, value in want.items():           # простые поля: значение целиком
        if "." in key or not isinstance(value, str):
            continue
        got = (fields.get(key) or {}).get("value") or ""
        if got != value:
            bad[key] = (value, got)
    ok(not bad, f"{src.name}: поля совпадают с {exp.name}" + (f" — {json.dumps(bad, ensure_ascii=False)}" if bad else ""))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def meta(port: int) -> dict | None:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{port}/api/meta", timeout=5) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def main() -> int:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    exe = str(Path(sys.argv[1]).resolve()) if Path(sys.argv[1]).exists() else shutil.which(sys.argv[1])
    if not exe:
        sys.exit(f"Нет файла {sys.argv[1]}")
    version = expected_version()
    tmp = Path(tempfile.mkdtemp(prefix="pysm_smoke_"))
    home = tmp / "home"
    base = {k: v for k, v in os.environ.items() if k not in ("DOCTOOL_HOME", "TESSERACT_CMD", "TESSDATA_PREFIX")}
    base["PYTHONIOENCODING"] = "utf-8"
    env = {**base, "DOCTOOL_HOME": str(home), "DOCTOOL_NO_BROWSER": "1"}
    print(f"Проверка {exe} (ожидается версия {version})")
    try:
        p = run(exe, ["--version"], env, 120)
        ok(p.stdout.strip() == f"PySimpleManager {version}", f"--version: {p.stdout.strip()!r}")

        p = run(exe, ["validate"], base, 300)
        m = re.search(r"Папка данных: (.+)", p.stdout)
        data = Path(m.group(1).strip()) if m else None
        ok(p.returncode == 0 and "Конфигурация в порядке" in p.stdout, "validate")
        ok(data is not None and data.name == "PySimpleManager" and (data / "ПРОЧТИ.txt").exists(),
           f"папка данных по умолчанию: {data}")
        print("    " + "\n    ".join(l for l in p.stdout.splitlines() if l.startswith(("Папка", "Модули", "Типы"))))

        for form_dir in sorted((ROOT / "forms").glob("*/samples")):
            for exp in sorted(form_dir.glob("*.expected.json")):
                stem = exp.name[: -len(".expected.json")]
                src = next((exp.with_name(stem + x) for x in (".docx", ".pdf") if exp.with_name(stem + x).exists()), None)
                if src:
                    check_sample(exe, env, home, src, exp)

        import pymupdf
        pdf = next((ROOT / "forms").glob("*/samples/pdf_reference.pdf"))
        png = tmp / "scan_page.png"
        with pymupdf.open(pdf) as doc:
            doc[0].get_pixmap(dpi=200).save(str(png))
        p = run(exe, ["check", "-a", str(png)], env)
        d = case_dir(home, "scan_page")
        doms = ""
        if p.returncode == 0 and d:
            fields = json.loads((d / "result.json").read_text(encoding="utf-8"))["application"]["fields"]
            doms = (fields.get("domains") or {}).get("value") or ""
        ok("test.ru" in doms, f"скан (Tesseract): домены «{doms}»")

        port = 8765 if not meta(8765) else free_port()
        args = ["web", "--no-browser"] + (["--port", str(port)] if port != 8765 else [])
        srv = subprocess.Popen([exe, *args], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace")
        try:
            got = None
            for _ in range(120):
                got = meta(port)
                if got or srv.poll() is not None:
                    break
                time.sleep(1)
            ok(bool(got) and got.get("app") == "PySimpleManager" and got.get("version") == version,
               f"web: /api/meta на порту {port}")
            if port == 8765:          # повторный запуск с ярлыка (без аргументов)
                p = run(exe, [], env, 60)
            else:
                p = run(exe, ["web", "--no-browser", "--port", str(port)], env, 60)
            ok(p.returncode == 0 and "уже запущен" in p.stdout, "повторный запуск: программа уже запущена")
        finally:
            srv.terminate()
            try:
                out, _ = srv.communicate(timeout=20)
            except subprocess.TimeoutExpired:
                srv.kill()
                out, _ = srv.communicate()
            if FAILS:
                print("    вывод веб-сервера:\n" + out[-2000:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\nИтог: {'ошибок нет' if not FAILS else 'ошибки: ' + '; '.join(FAILS)}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
