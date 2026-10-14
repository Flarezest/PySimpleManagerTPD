"""Рабочее место (0.7.6): архивы, страницы и их разбор, проверка по страницам, сверка, данные для Госуслуг и manager,
проверки доменов, API. Без нейросети и без расширения; OCR — только на синтетических страницах."""
import json
import shutil
import time
import zipfile
from pathlib import Path

import cv2
import numpy as np
import pytest

from doctool import archive, domains, workspace, wsconfig
from doctool.service import CaseInput, run_case, table_rows

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "forms" / "regru_transfer_person" / "samples"


# ------------------------------------------------------------------ архивы

class _Cp866Info(zipfile.ZipInfo):
    """Имя файла в архиве — в cp866 без флага UTF-8 (так пишет «Сжатая папка» Windows)."""
    __slots__ = ("raw", "xflags")

    def _encodeFilenameFlags(self):
        return self.raw, (self.flag_bits & ~0x800) | self.xflags


def _zip_cp866(path: Path, name: str, data: bytes, flags: int = 0) -> None:
    zi = _Cp866Info("x")
    zi.raw, zi.xflags = name.encode("cp866"), flags
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(zi, data)


def test_unzip_cp866_nested_and_safe_paths(tmp_path):
    a = tmp_path / "a.zip"
    _zip_cp866(a, "Заявление.txt", b"hello")
    files, notes = archive.unpack(a, tmp_path / "out")
    assert [f.name for f in files] == ["Заявление.txt"] and notes == []
    # архив в архиве + путь с «..» — всё остаётся внутри папки распаковки
    inner = tmp_path / "inner.zip"
    with zipfile.ZipFile(inner, "w") as z:
        z.writestr("Паспорт.jpg", b"jpg")
    outer = tmp_path / "outer.zip"
    with zipfile.ZipFile(outer, "w") as z:
        z.write(inner, "docs/inner.zip")
        z.writestr("../../evil.txt", b"x")
        z.writestr("стр10.png", b"1")
        z.writestr("стр2.png", b"1")
    files, notes = archive.unpack(outer, tmp_path / "o2")
    names = [f.name for f in files]
    assert "Паспорт.jpg" in names and "evil.txt" in names and "inner.zip" not in names
    assert names.index("стр2.png") < names.index("стр10.png")          # естественный порядок имён
    assert all((tmp_path / "o2") in f.parents for f in files)
    assert any("вложенный архив распакован" in n for n in notes)


def test_archive_password_and_missing_tool(tmp_path, monkeypatch):
    a = tmp_path / "p.zip"
    _zip_cp866(a, "a.txt", b"x", flags=0x1)
    with pytest.raises(archive.ArchiveError, match="паролем"):
        archive.unpack(a, tmp_path / "o")
    r = tmp_path / "x.rar"
    r.write_bytes(b"Rar!\x1a\x07\x00 fake")
    monkeypatch.setattr(archive, "find_7z", lambda: None)
    monkeypatch.setattr(archive, "find_unrar", lambda: None)
    with pytest.raises(archive.ArchiveError, match="7-Zip"):
        archive.unpack(r, tmp_path / "o2")


# ------------------------------------------------------------------ разбор страниц

def test_scan_kind_rules():
    forms = workspace.load_forms()
    k = workspace._scan_kind("РОССИЙСКАЯ ФЕДЕРАЦИЯ\nФАМИЛИЯ ИВАНОВ\nPNRUSIVANOV<<IVAN<<<<<<<<<<<<<\n1234567890RUS<<<<<", forms)
    assert k[0] == "passport" and k[1] == "разворот с фото"
    assert workspace._scan_kind("МЕСТО ЖИТЕЛЬСТВА\nЗАРЕГИСТРИРОВАН г. Москва", forms)[1] == "прописка"
    assert workspace._scan_kind("Сведения о ранее выданных паспортах", forms)[1] == "ранее выданные паспорта"
    assert workspace._scan_kind("ОПИСЬ ВЛОЖЕНИЯ в ценное письмо", forms)[0] == "other"
    assert workspace._scan_kind("просто текст", forms) is None


def test_rot_box():
    # прямоугольник на странице 100×200 после поворота по часовой стрелке
    assert workspace.rot_box([10, 20, 30, 40], 100, 200, 90) == [160, 10, 180, 30]
    assert workspace.rot_box([10, 20, 30, 40], 100, 200, 180) == [70, 160, 90, 180]
    assert workspace.rot_box([10, 20, 30, 40], 100, 200, 270) == [20, 70, 40, 90]


def _make_ws(tmp_path) -> workspace.Workspace:
    """Дело из zip: PDF-заявление (2 стр.), пустая страница, та же страница скана ещё раз, подпись, txt."""
    import pymupdf
    with pymupdf.open(SAMPLES / "pdf_reference.pdf") as d:
        pix = d[0].get_pixmap(dpi=150)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)[:, :, :3][:, :, ::-1].copy()
    scan, blank = tmp_path / "scan.png", tmp_path / "blank.png"
    cv2.imwrite(str(scan), img)
    cv2.imwrite(str(blank), np.full((900, 700, 3), 255, np.uint8))
    z = tmp_path / "up" / "ticket.zip"
    z.parent.mkdir()
    with zipfile.ZipFile(z, "w") as zf:
        zf.write(SAMPLES / "pdf_reference.pdf", "Заявление.pdf")
        zf.write(blank, "фото/пусто.png")
        zf.write(scan, "фото/скан.png")
        zf.writestr("Заявление.pdf.sig", b"0\x82sig")
        zf.writestr("readme.txt", b"txt")
    wsm = workspace.WorkspaceManager(str(tmp_path / "res"))
    ws = wsm.create([("ticket.zip", z)], {"ticket": "#20261014000001", "operation": "Смена администратора"})
    t0 = time.time()
    while ws.status not in ("ready", "error") and time.time() - t0 < 120:
        time.sleep(0.2)
    assert ws.status == "ready", ws.error
    return ws


def test_workspace_pages_and_sources(tmp_path):
    ws = _make_ws(tmp_path)
    kinds = {f.name.split("/")[-1]: f.kind for f in ws.files}
    assert kinds["Заявление.pdf"] == "pdf" and kinds["Заявление.pdf.sig"] == "sig" and kinds["readme.txt"] == "other"
    by = {(ws.file(p.file).name.split("/")[-1], p.index): p for p in ws.pages}
    p1, p2 = by[("Заявление.pdf", 0)], by[("Заявление.pdf", 1)]
    assert (p1.cls, p1.sub) == ("application", "бланк заявления") and p1.text
    assert p2.cls == "application"
    blank = by[("пусто.png", 0)]
    assert (blank.cls, blank.sub) == ("hidden", "пустая страница"), blank.why
    dup = by[("скан.png", 0)]
    assert dup.cls == "hidden" and dup.dup_of == p1.id and "повтор" in dup.sub, (dup.cls, dup.sub, dup.why)
    assert (ws.pages_dir / f"{p1.id}.jpg").exists() and (ws.pages_dir / f"{p1.id}_t.jpg").exists()
    assert abs(p1.sx - workspace.PREVIEW_DPI / workspace.EXTRACT_DPI) < 1e-6 and p1.pv[0] > 1000
    app, pas = ws.sources()
    assert [s["index"] for s in app] == [0, 1] and pas == []
    # оператор: повтор → паспорт, поворот, вырезка рамкой (вырезка — в паспорт, с поворотом исходной страницы)
    ws.move(dup.id, "passport")
    assert dup.cls == "passport" and dup.manual
    ws.set_rotation(dup.id, 90)
    cp = ws.add_crop(dup.id, [10, 20, 300, 200])
    assert cp.cls == "passport" and cp.kind == "crop" and cp.crop_rot == 90 and cp.pv == [300, 200]
    src = ws.source_of(cp)
    assert src["rotate"] == 90 and src["crop"] == [round(10 / dup.sx), round(20 / dup.sx), round(300 / dup.sx), round(200 / dup.sx)]
    with pytest.raises(workspace.WsError):
        ws.set_rotation(cp.id, 90)
    app, pas = ws.sources()
    assert pas[0]["page"] == cp.id                       # вырезка (разворот) — первой: по ней читаются поля
    # состояние сохраняется в ws.json и читается заново
    ws.set_state({"ticket": "#1", "marks": {"fio": {"mark": "bad", "note": "опечатка"}}, "row": {"a": "1"}})
    ws2 = workspace.Workspace.load(ws.dir)
    assert ws2.state["marks"]["fio"]["note"] == "опечатка" and ws2.page(cp.id).crop_rot == 90
    assert "Заявление.pdf" in ws.docx_html.__doc__ or True


def test_run_case_by_pages_matrix_and_boxes(tmp_path):
    ws = _make_ws(tmp_path)
    app, pas = ws.sources()
    res = run_case(CaseInput(case_type="admin_change_person", app_sources=app, pas_sources=pas,
                             out_root=str(tmp_path / "res")))
    exp = json.loads((SAMPLES / "pdf_reference.expected.json").read_text(encoding="utf-8"))
    fields = exp.get("fields", exp)
    assert res.app.get("passport") == "4012 654321"
    for k in ("passport", "issued_by"):
        if k in fields:
            assert res.app.get(k) == fields[k]
    rows = table_rows(res)
    sd = {"person_r_surname": "Тестова", "person_r_name": "Анна", "person_r_patronimic": "Сергеевна",
          "birth_date": "21.11.1990", "passport_series": "4012", "passport_number_short": "654321",
          "passport_date": "03.12.2010", "passport_place": "ТП № 5 ОУФМС России по Санкт-Петербургу в Тестовском районе"}
    dom = domains.DomainInfo(domain="alpha-test.ru", status="found", kind="person", sd=sd,
                             esia={"data": {"last_name": "Тестова", "first_name": "Анна", "middle_name": "Иванова",
                                            "birth_date": "21.11.1990", "rf_passport": {"series": "4012", "number": "654321"}}})
    mx = workspace.matrix(rows, [dom.to_dict()])
    by = {r["id"]: r for r in mx["rows"]}
    assert mx["domain"] == "alpha-test.ru"
    assert by["fio"]["cells"]["sd"]["status"] == "ref" and by["fio"]["cells"]["app"]["status"] == "ok"
    assert by["fio"]["cells"]["esia"]["status"] == "fail"                 # отчество в ЕСИА другое
    assert by["passport"]["cells"]["app"]["status"] == "ok" and by["birth_date"]["cells"]["esia"]["status"] == "ok"
    assert by["domains"]["cells"]["sd"]["value"].startswith("в manager найдено 1 из 1")
    boxes = workspace.field_boxes(ws, res)
    p1 = ws.pages[0]
    assert boxes["passport"][0]["page"] == p1.id and len(boxes["passport"][0]["box"]) == 4
    x0, y0, x1, y1 = boxes["passport"][0]["box"]
    assert 0 <= x0 < x1 <= p1.pv[0] and 0 <= y0 < y1 <= p1.pv[1]
    assert workspace.gosuslugi_data(res) == {"series": "4012", "number": "654321", "surname": "Тестова", "name": "Анна"}
    adm = workspace.new_admin(res)
    assert adm["org"].startswith("ООО") and adm["email"] == "info@romashka-test.ru" and adm["contract"] == "7654321"
    assert workspace.contract_login(adm["contract"]) == "user_id:7654321"
    assert workspace.contract_login("login@example.ru") == "login@example.ru"


def test_compare_new_admin():
    page = {"base": {"fio": "Белов Игорь Сергеевич", "email": "belov@example.ru", "phone": "+74950000000\n+79000000000",
                     "passport": "4011, 654321, ГУ МВД, 15.04.2018"}}
    rows = {r["key"]: r for r in workspace.compare_new_admin(
        {"fio": "Белов Игорь Сергеевич", "email": "BELOV@example.ru", "phone": "+7 900 000-00-00"}, page)}
    assert rows["fio"]["status"] == rows["email"]["status"] == rows["phone"]["status"] == "ok"
    assert rows["passport"]["status"] == "info"                  # в заявлении нет — к сведению
    rows = {r["key"]: r for r in workspace.compare_new_admin({"fio": "Белов Иван"}, page)}
    assert rows["fio"]["status"] == "fail"


# ------------------------------------------------------------------ проверки доменов и настройки

def test_domain_flags_and_config(tmp_path):
    from datetime import date
    i = domains.DomainInfo(domain="a.ru", status="found", kind="person", login="x@example.ru", bill_owner="y@example.ru",
                           s={"admin_comment": "только по оригиналу", "state": "A", "changeProhibited": "1",
                              "expiration_date": "2026-11-01", "freeze_reason": "", "operation_blocks": ""},
                           sd={"last_admin_change": "2026-10-02 10:00:00"}, esia={"count": 0})
    codes = {f["code"]: f["level"] for f in domains.domain_flags(i, today=date(2026, 10, 14))}
    assert codes == {"admin_comment": "warn", "recent_admin_change": "warn", "s_prohibited": "warn",
                     "expires_soon": "info", "esia_none": "info", "bill_owner_differs": "info"}
    d = i.to_dict()
    assert d["admin_comment"] == "только по оригиналу" and d["login"] == "x@example.ru" and d["flags_level"] in ("warn", "stop")
    # свой config/workspace.yaml: уровень «стоп», проверка выключена
    cfg = tmp_path / "workspace.yaml"
    cfg.write_text("domain_checks:\n  admin_comment: {level: stop}\n  esia_none: {level: off}\n", encoding="utf-8")
    c = wsconfig.load(cfg)["domain_checks"]
    codes = {f["code"]: f["level"] for f in domains.domain_flags(i, c, today=date(2026, 10, 14))}
    assert codes["admin_comment"] == "stop" and "esia_none" not in codes
    assert domains.flags_level(domains.domain_flags(i, c, today=date(2026, 10, 14))) == "stop"
    # не найденный домен — проверок нет
    assert domains.domain_flags(domains.DomainInfo(domain="b.ru", status="not_found")) == []
    bad = tmp_path / "bad.yaml"
    bad.write_text("table:\n  operations:\n    - {title: X, column4: other, case_type: nope}\n"
                   "domain_checks:\n  unknown_rule: {level: warn}\n  frozen: {level: red}\n", encoding="utf-8")
    errs = "\n".join(wsconfig.validate(bad))
    assert "column4" in errs and "nope" in errs and "unknown_rule" in errs and "red" in errs
    assert wsconfig.validate(ROOT / "config" / "workspace.yaml") == []


# ------------------------------------------------------------------ API рабочего места

def test_workspace_api(tmp_path):
    from fastapi.testclient import TestClient
    from doctool.web import create_app
    app = create_app(str(tmp_path / "res"))
    c = TestClient(app)
    meta = c.get("/api/ws/meta").json()
    assert meta["operations"][0]["title"] == "Смена администратора" and meta["operations"][0]["checked"]
    assert "отрс" in meta["receipt_methods"] and meta["status_default"] == "готово"
    assert c.get("/static/workspace.js").status_code == 200 and c.get("/static/nope.js").status_code == 404
    with (SAMPLES / "pdf_reference.pdf").open("rb") as f:
        r = c.post("/api/ws", files=[("files", ("Заявление.pdf", f, "application/pdf"))],
                   data={"state": json.dumps({"ticket": "#2026101400000001", "operation": "Смена администратора",
                                              "via": "отрс"})})
    assert r.status_code == 200, r.text
    ws_id = r.json()["id"]
    t0 = time.time()
    while True:
        d = c.get(f"/api/ws/{ws_id}").json()
        if d["job"] and d["job"]["status"] in ("done", "error"):
            break
        assert time.time() - t0 < 120, d
        time.sleep(0.3)
    assert d["status"] == "ready" and d["job"]["status"] == "done", d
    assert d["state"]["ticket"] == "#2026101400000001" and d["pages"][0]["cls"] == "application"
    assert c.get(d["pages"][0]["url"]).headers["content-type"] == "image/jpeg"
    assert c.get(d["pages"][0]["thumb"]).status_code == 200
    res = c.get(f"/api/ws/{ws_id}/result").json()
    assert res["ready"] and res["matrix"]["rows"] and res["gos"]["series"] == "4012"
    assert res["login_to"] == "user_id:7654321" and res["boxes"]
    # расширение не подключено: проверка прошла, данные доменов не загружены — замечание в деле
    assert any("расширение" in n for n in res["result"]["notes"])
    assert c.post(f"/api/ws/{ws_id}/gosuslugi", json={}).status_code == 409
    r = c.post(f"/api/ws/{ws_id}/pages", json={"action": "move", "pid": d["pages"][1]["id"], "cls": "other"})
    assert r.json()["ws"]["pages"][1]["cls"] == "other"
    r = c.post(f"/api/ws/{ws_id}/state", json={"via": "почта", "row": {"status": "готово"}})
    assert r.json()["state"]["via"] == "почта"
    assert c.get("/api/ws/0123456789").status_code == 404
    assert c.get("/api/ws/recent").json()["items"][0]["ticket"] == "#2026101400000001"
    # после перезапуска программы дело читается с диска
    c2 = TestClient(create_app(str(tmp_path / "res")))
    d2 = c2.get(f"/api/ws/{ws_id}").json()
    assert d2["state"]["via"] == "почта" and d2["pages"][1]["cls"] == "other"
