"""(0.8.2) Безбумажные операции (доверенность в manager, /userdocs/loa_add) и смена администратора без доменов.
Без расширения Chrome: связь с ним подменяется записью команд."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from doctool import workspace, wsconfig


def test_loa_operation_in_config(tmp_path):
    for cfg in (wsconfig.load(), wsconfig.load(Path("/nonexistent/workspace.yaml"))):   # встроенный файл и DEFAULT
        op = next(o for o in cfg["table"]["operations"] if o["title"] == "Безбумажные операции")
        assert op["column4"] == "domain" and op["manager_page"] == "loa_add" and not op.get("case_type")
    assert wsconfig.validate() == []
    bad = tmp_path / "workspace.yaml"
    bad.write_text("table:\n  operations:\n    - {title: X, manager_page: loa_edit}\n", encoding="utf-8")
    assert any("manager_page" in e for e in wsconfig.validate(bad))


def test_loa_doc_from_passport_and_application():
    pas = {"series_number": "40 12 654321", "surname": "ПЕТРОВА-ВОДКИНА", "given_name": "АННА", "patronymic": "ИВАНОВНА"}
    res = SimpleNamespace(pas=SimpleNamespace(get=lambda k, d=None: pas.get(k, d)), app=None)
    assert workspace.loa_doc(res) == {"passport": "4012 654321", "fio": "Петрова-Водкина Анна Ивановна"}
    assert workspace.loa_doc(None) == {"passport": "", "fio": ""}


@pytest.fixture
def app_ext(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from doctool.web import create_app
    app = create_app(str(tmp_path / "res"))
    calls = []

    def fake(cmd, params, timeout=240, on_progress=None):
        calls.append((cmd, params))
        return {"ok": True, "data": {"changed": [], "notes": []}}
    monkeypatch.setattr(app.state.bridge, "request_sync", fake)
    app.state.bridge.ws = object()
    wsm = app.state.workspaces
    ws = workspace.Workspace("10a0000001", wsm.root / "20261015_100000_10a0000001")
    (ws.dir / "files").mkdir(parents=True)
    ws.status = "ready"
    ws.save()
    return TestClient(app), wsm, calls


def test_domain_move_without_domains(app_ext):
    # пустой список доменов - не ошибка: расширение открывает страницу, поле «Домены» не трогает
    c, wsm, calls = app_ext
    r = c.post("/api/ws/10a0000001/manager/fill", json={"domains": [], "change_admin": True, "files": []})
    assert r.status_code == 200 and r.json()["ok"], r.text
    assert calls[-1][0] == "domain_move_fill" and calls[-1][1]["domains"] == []
    assert wsm.get("10a0000001").state["mgr"]["step"] == 1


def test_loa_fill_api(app_ext):
    c, wsm, calls = app_ext
    meta = c.get("/api/ws/meta").json()
    assert any(o["title"] == "Безбумажные операции" and o["manager_page"] == "loa_add" for o in meta["operations"])
    # нечего вписывать - 400, расширение не вызывается
    n = len(calls)
    r = c.post("/api/ws/10a0000001/manager/loa_fill", json={"fields": {"passport_inn": "  ", "other": "x"}})
    assert r.status_code == 400 and len(calls) == n
    r = c.post("/api/ws/10a0000001/manager/loa_fill", json={"fields": {
        "passport_inn": " 4012  654321 ", "fullname": "Тестова   Анна Ивановна", "email": "a@example.ru",
        "customer_phone": "", "doc_start_date": "01.01.2026", "user_id": "1"}})
    assert r.status_code == 200 and r.json()["ok"], r.text
    cmd, p = calls[-1]
    # только поля доверенности, пустые и чужие (срок, user_id) не передаются; пробелы схлопнуты
    assert cmd == "loa_fill" and p == {"fields": {"passport_inn": "4012 654321", "fullname": "Тестова Анна Ивановна",
                                                  "email": "a@example.ru"}}
    st = wsm.get("10a0000001").state["loa"]
    assert st["step"] == 1 and st["filled"] == ["email", "fullname", "passport_inn"]
    # правки окна сохраняются в деле
    r = c.post("/api/ws/10a0000001/state", json={"loa": {"src": "x.ru", "edit": {"email": "b@example.ru"}, "edit_src": "x.ru"}})
    assert r.json()["state"]["loa"]["edit"] == {"email": "b@example.ru"} and r.json()["state"]["loa"]["step"] == 1


def test_loa_fill_without_extension(tmp_path):
    from fastapi.testclient import TestClient
    from doctool.web import create_app
    app = create_app(str(tmp_path / "res"))
    wsm = app.state.workspaces
    ws = workspace.Workspace("10a0000002", wsm.root / "20261015_100001_10a0000002")
    (ws.dir / "files").mkdir(parents=True)
    ws.status = "ready"
    ws.save()
    r = TestClient(app).post("/api/ws/10a0000002/manager/loa_fill", json={"fields": {"passport_inn": "RU", "fullname": "7701234567"}})
    assert r.status_code == 409 and "не подключено" in r.json()["detail"]
