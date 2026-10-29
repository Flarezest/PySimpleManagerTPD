"""0.7.5: папка данных пользователя (установленная программа, DOCTOOL_HOME), свои модули заявлений и правила
поверх встроенных, Tesseract в составе программы, повторный запуск веб-интерфейса."""
import json
import os
import shutil
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from doctool import formspec, paths  # noqa: E402
from doctool.verdict import load_case_types  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCTOOL_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


def test_source_run_keeps_old_layout(monkeypatch):
    monkeypatch.delenv("DOCTOOL_HOME", raising=False)
    assert paths.user_dir() is None and paths.results_dir() == "results"
    assert paths.ensure_user_dir() is None
    assert formspec.forms_dirs() == [formspec.FORMS_DIR]
    assert paths.config_file() == paths.BUNDLED_CONFIG


def test_installed_program_uses_documents(monkeypatch, tmp_path):
    monkeypatch.delenv("DOCTOOL_HOME", raising=False)
    monkeypatch.setattr(paths, "FROZEN", True)
    monkeypatch.setattr(paths, "documents_dir", lambda: tmp_path / "Документы")
    assert paths.user_dir() == tmp_path / "Документы" / "PySimpleManager"
    assert Path(paths.results_dir()) == tmp_path / "Документы" / "PySimpleManager" / "results"


def test_documents_dir_exists_or_is_home_documents():
    d = paths.documents_dir()
    assert d.is_absolute()


def test_ensure_user_dir(home):
    assert paths.ensure_user_dir() == home
    for sub in ("results", "forms", "config"):
        assert (home / sub).is_dir()
    text = (home / paths.README_NAME).read_text(encoding="utf-8-sig")
    assert str(home) in text and str(paths.BUNDLED_FORMS) in text
    paths.ensure_user_dir()                      # повторно - без ошибок, файл тот же
    assert (home / paths.README_NAME).read_text(encoding="utf-8-sig") == text


def test_own_module_replaces_bundled_and_adds_new(home):
    paths.ensure_user_dir()
    src = ROOT / "forms" / "regru_transfer_person"
    own = home / "forms" / "regru_transfer_person"
    shutil.copytree(src, own, ignore=shutil.ignore_patterns("samples", "__pycache__"))
    text = (own / "form.yaml").read_text(encoding="utf-8")
    (own / "form.yaml").write_text(text.replace("title:", "title: СВОЙ", 1), encoding="utf-8")
    extra = home / "forms" / "test_extra"
    extra.mkdir()
    (extra / "form.yaml").write_text("id: test_extra\ntitle: Проба\ndetect: {keywords: [проба]}\nfields: {}\n",
                                     encoding="utf-8")
    forms = {f["id"]: f for f in formspec.load_forms()}
    assert formspec.forms_dirs() == [home / "forms", formspec.FORMS_DIR]
    assert Path(forms["regru_transfer_person"]["_module"]) == own            # свой вместо встроенного
    assert str(forms["regru_transfer_person"].get("title", "")).startswith("СВОЙ")
    assert "test_extra" in forms
    assert len([f for f in formspec.load_forms() if f["id"] == "regru_transfer_person"]) == 1
    assert formspec.hooks(forms["regru_transfer_person"]) is not None          # hooks.py своего модуля


def test_empty_user_forms_dir_changes_nothing(home):
    paths.ensure_user_dir()
    assert [f["id"] for f in formspec.load_forms()] == [f["id"] for f in formspec.load_forms(formspec.FORMS_DIR)]


def test_own_case_types(home):
    paths.ensure_user_dir()
    bundled = load_case_types()
    assert paths.config_file() == paths.BUNDLED_CONFIG
    (home / "config" / "case_types.yaml").write_text(
        "case_types:\n  only_mine:\n    title: Мой тип\n    form: null\n", encoding="utf-8")
    assert list(load_case_types()) == ["only_mine"]
    assert load_case_types(paths.BUNDLED_CONFIG) == bundled


def test_validate_prints_paths(home, capsys):
    from doctool.__main__ import main
    main(["validate"])
    out = capsys.readouterr().out
    assert f"Папка данных: {home}" in out and "Конфигурация в порядке" in out
    assert (home / paths.README_NAME).exists()


def test_cli_default_out_is_user_results(home, monkeypatch):
    import doctool.__main__ as m
    seen = {}
    monkeypatch.setattr(m, "cmd_web", lambda args: seen.setdefault("out", args.out))
    monkeypatch.setattr(m, "check_tesseract", lambda: None)
    m.main(["web", "--no-browser"])
    assert Path(seen["out"]) == home / "results"


def test_bundled_tesseract_first(tmp_path, monkeypatch):
    from doctool import ocr
    monkeypatch.delenv("TESSERACT_CMD", raising=False)
    # setenv перед delenv: monkeypatch запоминает исходное состояние и после теста вернёт его - иначе TESSDATA_PREFIX,
    # заданный find_tesseract(), остаётся и следующие тесты с OCR ищут языки во временной папке этого теста
    monkeypatch.setenv("TESSDATA_PREFIX", "")
    monkeypatch.delenv("TESSDATA_PREFIX")
    exe = tmp_path / "tesseract" / ("tesseract.exe" if os.name == "nt" else "tesseract")
    (exe.parent / "tessdata").mkdir(parents=True)
    exe.write_text("")
    monkeypatch.setattr(paths, "APP_DIR", tmp_path)
    assert ocr.find_tesseract() == str(exe)
    assert os.environ["TESSDATA_PREFIX"] == str(exe.parent / "tessdata")
    monkeypatch.setenv("TESSERACT_CMD", "/свой/tesseract")
    assert ocr.find_tesseract() == "/свой/tesseract"


def test_no_bundled_tesseract_falls_back_to_path(tmp_path, monkeypatch):
    from doctool import ocr
    monkeypatch.delenv("TESSERACT_CMD", raising=False)
    monkeypatch.setattr(paths, "APP_DIR", tmp_path)
    monkeypatch.setattr(ocr.shutil, "which", lambda name: "/usr/bin/tesseract" if name == "tesseract" else None)
    assert ocr.find_tesseract() == "/usr/bin/tesseract"


def _serve(handler):
    srv = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_already_running():
    from doctool import APP_NAME
    from doctool.web import already_running

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        free = s.getsockname()[1]
    assert already_running("127.0.0.1", free) is None

    class Ours(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"app": APP_NAME, "version": "9.9.9"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    class Other(Ours):
        def do_GET(self):
            self.send_response(404)
            self.end_headers()

    for handler, want in ((Ours, "9.9.9"), (Other, "")):
        srv = _serve(handler)
        try:
            assert already_running("127.0.0.1", srv.server_address[1]) == want
            assert already_running("0.0.0.0", srv.server_address[1]) == want
        finally:
            srv.shutdown()
            srv.server_close()


def test_serve_second_start_opens_browser(monkeypatch, capsys):
    import webbrowser

    from doctool import web
    opened = []
    monkeypatch.setattr(web, "already_running", lambda host, port: "0.7.5")
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url))
    web.serve(port=8765, open_browser=True)
    assert opened == ["http://127.0.0.1:8765"] and "уже запущен" in capsys.readouterr().out
    monkeypatch.setattr(web, "already_running", lambda host, port: "")
    with pytest.raises(SystemExit, match="занят другой программой"):
        web.serve(port=8765)
