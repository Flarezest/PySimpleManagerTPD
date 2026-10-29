"""(0.8.5) Сохранённый результат загрузки доменов: после перезагрузки страницы и перезапуска программы он на месте,
при новой загрузке из manager запрашиваются только новые домены, «Сбросить результат» стирает его."""
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from doctool.domain_store import DomainStore, key, lookup_cached  # noqa: E402
from doctool.domains import DomainInfo, from_extension  # noqa: E402
from test_domains import S_ORG, S_PP, SD_ORG, SD_PP, reply  # noqa: E402

REPLIES = {"testdomain-pp.ru": reply(SD_PP, S_PP, "testdomain-pp.ru", "1"),
           "testdomain-org.ru": reply(SD_ORG, S_ORG, "testdomain-org.ru", "2")}


def fake_lookup(calls):
    def lookup(domains, log=print, cancel=None):
        calls.append(list(domains))
        out = []
        for d in domains:
            r = REPLIES.get(d) or {"ok": False, "error": {"code": "not_found", "message": "нет в manager"}}
            out.append(from_extension(d, r))
        return out
    return lookup


def test_store_reuses_found_and_not_found(tmp_path):
    st = DomainStore(tmp_path / "d.json")
    calls = []
    a = lookup_cached(fake_lookup(calls), ["testdomain-pp.ru", "free-domain.ru"], st)
    assert calls == [["testdomain-pp.ru", "free-domain.ru"]] and [x.status for x in a] == ["found", "not_found"]
    b = lookup_cached(fake_lookup(calls), ["testdomain-pp.ru", "free-domain.ru", "testdomain-org.ru"], st)
    assert calls[-1] == ["testdomain-org.ru"]                     # (d1, d2) уже есть - только d3
    assert [x.domain for x in b] == ["testdomain-pp.ru", "free-domain.ru", "testdomain-org.ru"]
    assert b[0].fio == "Тестов Пётр Сергеевич" and b[2].holder == 'ООО "Тест"'
    n = len(calls)
    c = lookup_cached(fake_lookup(calls), ["testdomain-org.ru"], st)   # подмножество - из manager ничего
    assert len(calls) == n and c[0].status == "found"
    lst, items, updated = st.current()
    assert lst == ["testdomain-org.ru"] and len(items) == 1 and updated
    assert st.missing(["testdomain-pp.ru", "new.ru"]) == ["new.ru"]   # убранный из списка домен остаётся в файле
    st.reset()
    assert st.current()[0] == [] and st.missing(["testdomain-pp.ru"]) == ["testdomain-pp.ru"]


def test_store_retries_errors_and_clears_compare(tmp_path):
    st = DomainStore(tmp_path / "d.json")
    err = DomainInfo(domain="a.ru", status="error", error_code="timeout", error="расширение не ответило вовремя")
    ok = from_extension("testdomain-pp.ru", REPLIES["testdomain-pp.ru"])
    ok.compare, ok.verdict = [{"field": "fio", "status": "fail"}], "fail"
    st.save(["a.ru", "testdomain-pp.ru"], [err, ok])
    assert st.missing(["a.ru", "testdomain-pp.ru"]) == ["a.ru"]       # ошибка загрузки - загрузить снова
    info = st.known()["testdomain-pp.ru"]
    assert info.compare == [] and info.verdict == ""                  # сверка с документами - заново в деле


def test_store_key_idn():
    assert key("xn--e1afmkfd.xn--p1ai") == key("ПРИМЕР.РФ") == "пример.рф"


# ------------------------------------------------------------------ API (с поддельным расширением)
pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from doctool.web import create_app  # noqa: E402

ORIGIN = {"origin": "chrome-extension://abcdefghijklmnop"}


def _run(client, ws, body, asked):
    """Загрузка с ответами «расширения»; asked - домены, которые расширение спросили."""
    r = client.post("/api/manager/lookup", json=body)
    assert r.status_code == 200, r.text
    t = r.json()
    for _ in t["todo"]:
        req = ws.receive_json()
        asked.append(req["params"]["domain"])
        ws.send_json({"type": "result", "id": req["id"], **REPLIES[req["params"]["domain"]]})
    for _ in range(200):
        j = client.get(f"/api/manager/tasks/{t['task_id']}").json()
        if j["status"] != "running":
            return t, j
        time.sleep(0.02)
    raise AssertionError("загрузка не закончилась")


def test_tab_result_survives_restart_and_loads_only_new(tmp_path):
    app = create_app(out_root=str(tmp_path))
    app.state.manager.use_whois = False
    client = TestClient(app)
    asked = []
    with client.websocket_connect("/ext", headers=ORIGIN) as ws:
        ws.receive_json()
        ws.send_json({"type": "hello", "version": "0.8.4"})
        t, j = _run(client, ws, {"domains": "testdomain-pp.ru"}, asked)
        assert t["todo"] == ["testdomain-pp.ru"] and j["status"] == "done"
        t, j = _run(client, ws, {"domains": "testdomain-pp.ru\ntestdomain-org.ru"}, asked)
        assert t["todo"] == ["testdomain-org.ru"] and len(j["items"]) == 2
    assert asked == ["testdomain-pp.ru", "testdomain-org.ru"]
    # программа перезапущена (новое приложение на той же папке результатов), расширения нет
    client2 = TestClient(create_app(out_root=str(tmp_path)))
    saved = client2.get("/api/manager/domains").json()
    assert saved["list"] == ["testdomain-pp.ru", "testdomain-org.ru"] and len(saved["items"]) == 2
    assert client2.get("/api/manager/domains/report").status_code == 200
    # все домены уже загружены - расширение не нужно
    r = client2.post("/api/manager/lookup", json={"domains": "testdomain-org.ru"})
    assert r.status_code == 200 and r.json()["todo"] == []
    r = client2.post("/api/manager/lookup", json={"domains": "testdomain-org.ru new-domain.ru"})
    assert r.status_code == 409                                      # новый домен - нужно расширение
    assert client2.post("/api/manager/domains/reset").json()["ok"]
    assert client2.get("/api/manager/domains").json()["items"] == []
    assert client2.post("/api/manager/lookup", json={"domains": "testdomain-org.ru"}).status_code == 409


def test_workspace_domains_saved_in_case(tmp_path):
    app = create_app(out_root=str(tmp_path))
    app.state.manager.use_whois = False
    client = TestClient(app)
    ws_id = client.post("/api/ws", data={"state": '{"operation": "Корректировка данных домена"}'}).json()["id"]
    for _ in range(100):
        if client.get(f"/api/ws/{ws_id}").json()["status"] == "ready":
            break
        time.sleep(0.05)
    asked = []
    with client.websocket_connect("/ext", headers=ORIGIN) as ws:
        ws.receive_json()
        _run(client, ws, {"domains": "testdomain-pp.ru", "ws_id": ws_id}, asked)
        t, j = _run(client, ws, {"domains": "testdomain-pp.ru testdomain-org.ru", "ws_id": ws_id}, asked)
    assert asked == ["testdomain-pp.ru", "testdomain-org.ru"] and len(j["items"]) == 2
    # вкладка «Домены в manager» - свой результат, дело его не трогает
    assert client.get("/api/manager/domains").json()["items"] == []
    client2 = TestClient(create_app(out_root=str(tmp_path)))
    saved = client2.get(f"/api/ws/{ws_id}/domains").json()
    assert [d["domain"] for d in saved["items"]] and set(saved["list"]) == {"testdomain-pp.ru", "testdomain-org.ru"}
    assert client2.post(f"/api/ws/{ws_id}/domains/reset").json()["ok"]
    assert client2.get(f"/api/ws/{ws_id}/domains").json()["items"] == []


def test_job_autoload_uses_case_store(tmp_path, monkeypatch):
    """Проверка дела рабочего места (и «Перепроверить»): домены, загруженные раньше в этом деле, не запрашиваются;
    дописанные оператором домены остаются в списке."""
    import doctool.jobs as jobs_mod
    from doctool.models import Extraction
    from doctool.service import CaseInput, CaseResult, _evaluate
    from doctool.verdict import load_case_types
    from test_domains import passport

    def fake_run_case(inp):
        res = CaseResult(inp=inp, case_type=load_case_types()["passport_only"], case_id="t", case_dir=tmp_path)
        res.app, res.pas = Extraction("none", ""), passport()
        res.app.set("domains.list", ["testdomain-pp.ru"], 1.0, "text")
        _evaluate(res)
        return res

    monkeypatch.setattr(jobs_mod, "run_case", fake_run_case)
    app = create_app(out_root=str(tmp_path))
    app.state.manager.use_whois = False
    jm, client = app.state.jobs, TestClient(app)
    st = DomainStore(tmp_path / "case" / "domains.json")
    st.save(["testdomain-org.ru"], [from_extension("testdomain-org.ru", REPLIES["testdomain-org.ru"])])
    asked = []
    with client.websocket_connect("/ext", headers=ORIGIN) as ws:
        ws.receive_json()
        job = jm.submit(CaseInput(case_type="passport_only", out_root=str(tmp_path), manager_autoload=True), store=st)
        req = ws.receive_json()
        asked.append(req["params"]["domain"])
        ws.send_json({"type": "result", "id": req["id"], **REPLIES[req["params"]["domain"]]})
        for _ in range(200):
            if job.status in ("done", "error"):
                break
            time.sleep(0.02)
        assert job.status == "done", job.log
        assert asked == ["testdomain-pp.ru"]                          # org - из сохранённого результата
        assert [d.domain for d in job.result.domains_sd] == ["testdomain-org.ru", "testdomain-pp.ru"]
        job2 = jm.submit(CaseInput(case_type="passport_only", out_root=str(tmp_path), manager_autoload=True), store=st)
        for _ in range(200):
            if job2.status in ("done", "error"):
                break
            time.sleep(0.02)
    assert job2.status == "done" and asked == ["testdomain-pp.ru"] and len(job2.result.domains_sd) == 2
    assert job2.result.domains_sd[1].verdict == "ok"                  # сверка Sd с паспортом дела - заново
