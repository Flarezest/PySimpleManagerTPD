"""(0.8.0) Рабочее место: строка «Поиск» (настройки, API сайта рядом через расширение) и удаление недавних дел.
Без расширения Chrome: связь с ним подменяется записью команд."""
import json
from pathlib import Path

import pytest

from doctool import workspace, wsconfig


def test_search_config_defaults():
    cfg = wsconfig.load(Path("/nonexistent/workspace.yaml"))["search"]
    assert cfg["open_in"] == "auto" and cfg["window_share"] == 40
    assert [s["id"] for s in cfg["services"]] == ["google", "egrul"]          # (0.8.1) без Яндекса
    assert cfg["services"][1]["fill"] == "egrul" and "{q}" in cfg["services"][0]["url"]
    assert [x["title"] for x in cfg["links"]] == ["Nextcloud", "Проверка ЭП · Госуслуги", "Контур.Крипто"]
    # встроенный config/workspace.yaml совпадает с умолчаниями и проходит проверку
    assert wsconfig.load()["search"] == cfg and wsconfig.validate() == []


def test_search_config_validate_and_clean(tmp_path):
    p = tmp_path / "workspace.yaml"
    p.write_text("""
search:
  open_in: sideways
  window_share: 95
  services:
    - {id: g, title: Google, url: "https://www.google.com/search?q={q}"}
    - {title: Без адреса}
    - {title: Плохой, url: "ftp://example.test"}
    - {title: Форма, url: "https://example.test/", fill: neural}
  links:
    - {title: Ссылка, url: "https://example.test/x"}
    - {url: "https://example.test/no-title"}
""", encoding="utf-8")
    errs = wsconfig.validate(p)
    joined = "\n".join(errs)
    assert "search.open_in" in joined and "window_share" in joined and "url должен" in joined
    assert "сервис «Без адреса»: url должен" in joined and "сервис «Плохой»: url должен" in joined
    assert "сервис «Форма»: fill" in joined and "search.links[2] — нет title" in joined
    cfg = wsconfig.load(p)["search"]
    assert cfg["open_in"] == "auto" and cfg["window_share"] == 70
    assert [s["title"] for s in cfg["services"]] == ["Google", "Форма"] and cfg["services"][1]["fill"] is None
    assert cfg["links"] == [{"title": "Ссылка", "url": "https://example.test/x"}]


@pytest.fixture
def app_ext(tmp_path, monkeypatch):
    """Приложение с «подключённым» расширением: команды записываются, ответ — как у расширения."""
    from fastapi.testclient import TestClient
    from doctool.web import create_app
    app = create_app(str(tmp_path / "res"))
    calls = []

    def fake(cmd, params, timeout=240, on_progress=None):
        calls.append((cmd, params))
        return {"ok": True, "data": {"mode": "split", "notes": []}}
    monkeypatch.setattr(app.state.bridge, "request_sync", fake)
    app.state.bridge.ws = object()
    return TestClient(app), app, calls


def test_site_api_without_extension(tmp_path):
    from fastapi.testclient import TestClient
    from doctool.web import create_app
    c = TestClient(create_app(str(tmp_path / "res")))
    assert c.post("/api/site/open", json={"url": "javascript:alert(1)"}).status_code == 400
    r = c.post("/api/site/open", json={"url": "https://www.google.com/search?q=x"})
    assert r.status_code == 409 and "не подключено" in r.json()["detail"]
    assert c.post("/api/site/hide", json={}).json()["data"]["mode"] == "none"
    meta = c.get("/api/ws/meta").json()
    assert meta["search"]["services"][0]["title"] == "Google" and meta["search"]["links"]
    st = c.get("/api/manager/status").json()
    assert st["connected"] is False and st["split_api"] is False


def test_site_api_commands(app_ext):
    c, app, calls = app_ext
    r = c.post("/api/site/open", json={"url": "https://egrul.nalog.ru/index.html", "fill": "egrul", "q": "7707083893",
                                       "open_in": "window", "screen": {"left": 0, "top": 0, "width": 1920, "height": 1040}})
    assert r.status_code == 200 and r.json()["ok"]
    cmd, p = calls[-1]
    assert cmd == "site_open" and p["url"].startswith("https://egrul") and p["open_in"] == "window"
    assert p["fill"] == {"kind": "egrul", "q": "7707083893"} and p["screen"]["width"] == 1920 and p["share"] == 40
    # неизвестная форма и способ — без fill, способ из настроек; неполный экран не передаётся
    c.post("/api/site/open", json={"url": "https://www.google.com/search?q=x", "fill": "other", "open_in": "x", "screen": {"width": 5}})
    cmd, p = calls[-1]
    assert "fill" not in p and p["open_in"] == "auto" and p["screen"] is None
    c.post("/api/site/hide", json={"close": True})
    assert calls[-1] == ("site_hide", {"close": True})
    # Госуслуги: страница открывается там же, где сайты строки «Поиск»
    wsm = app.state.workspaces
    ws = workspace.Workspace("abcdef1234", wsm.root / "20261014_120000_abcdef1234")
    ws.dir.mkdir(parents=True)
    ws.status = "ready"
    ws.save()
    r = c.post("/api/ws/abcdef1234/gosuslugi", json={"series": "4012", "number": "654321", "surname": "Тестова",
                                                      "name": "Анна", "site": {"open_in": "split"}})
    assert r.status_code == 200 and r.json()["ok"], r.text
    cmd, p = calls[-1]
    assert cmd == "gosuslugi_fill" and p["series"] == "4012" and p["site"]["open_in"] == "split"
    assert wsm.get("abcdef1234").state["gos"]["status"] == "filled"
    # запросы строки «Поиск» сохраняются в деле
    hist = [{"t": "10:00", "kind": "svc", "title": "Google", "url": "https://www.google.com/search?q=x", "q": "x"}]
    assert c.post("/api/ws/abcdef1234/state", json={"search": {"hist": hist}}).json()["state"]["search"]["hist"] == hist


def _make_ws(wsm, ws_id, status="ready", result_dirs=()):
    ws = workspace.Workspace(ws_id, wsm.root / f"20261014_1200{len(wsm.items):02d}_{ws_id}")
    (ws.dir / "files").mkdir(parents=True)
    (ws.dir / "files" / "a.pdf").write_bytes(b"%PDF")
    ws.status = status
    ws.result_dirs = [str(d) for d in result_dirs]
    ws.save()
    wsm.items[ws_id] = ws
    return ws


def test_delete_cases_safely(tmp_path):
    from fastapi.testclient import TestClient
    from doctool.web import create_app
    app = create_app(str(tmp_path / "res"))
    c = TestClient(app)
    wsm = app.state.workspaces
    res_dir = tmp_path / "res" / "20261014_120000_case1"
    res_dir.mkdir(parents=True)
    (res_dir / "result.json").write_text("{}")
    outside = tmp_path / "outside"
    outside.mkdir()
    other = _make_ws(wsm, "bbbbbb2222")
    ws1 = _make_ws(wsm, "aaaaaa1111", result_dirs=[res_dir, outside, other.dir, tmp_path / "res"])
    busy = _make_ws(wsm, "cccccc3333", status="unpacking")
    assert c.get("/api/ws/recent").json()["total"] == 3
    r = c.delete("/api/ws/aaaaaa1111")
    assert r.status_code == 200, r.text
    assert not ws1.dir.exists() and not res_dir.exists()
    # вне папки результатов, папка другого дела и сама папка результатов — не трогаются
    assert outside.exists() and other.dir.exists() and (tmp_path / "res").exists()
    assert c.delete("/api/ws/aaaaaa1111").status_code == 404
    assert c.delete("/api/ws/cccccc3333").status_code == 409          # ещё разбирается
    r = c.post("/api/ws/recent/clear", json={}).json()
    assert r["deleted"] == ["bbbbbb2222"] and [x["id"] for x in r["skipped"]] == ["cccccc3333"]
    assert not other.dir.exists() and busy.dir.exists()
    assert c.get("/api/ws/recent").json()["total"] == 1
