"""0.7.3: «Смена владельца ЛК» для ИП и юрлиц (сверка базовой анкеты, автозаполнение с переключением типа анкеты)
и ручной ввод данных заявления со сверкой с данными доменов из manager. Страницы manager - обезличенные фикстуры
(вымышленные данные), структура - как на страницах, присланных пользователем 11.10.2026."""
import json
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from doctool import account, fieldnorm  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures" / "manager"
JS = (ROOT / "doctool_extension" / "content" / "manager.js").read_text(encoding="utf-8")

BA_ORG = {"org_r": "ООО «Тест»", "code": "7701234567", "kpp": "770101001", "e_mail": "Org@Example.org",
          "address_r_addr": "ул. Тестовая, д. 1, офис 5", "p_addr_addr": "ул. Тестовая, д. 1, офис 5",
          "phone": "+79990001122\n+74950000000"}


# ------------------------------------------------------------------ сравнение адресов и e-mail

def test_address_and_email_kinds():
    same = fieldnorm.same
    assert same("address", "г. Москва, ул. Ленина, д. 5, кв. 1", "Москва город, улица Ленина, дом 5, квартира 1")[0] == "ok"
    assert same("address", "г.Фрязино, ул. Мира, д.19", "г. Фрязино, улица Мира, дом 19")[0] == "ok"
    st, det = same("address", "350087, Краснодарский край, г. Краснодар, ул. Красная, д. 3", "г Краснодар, ул Красная, 3")
    assert st == "warn" and "350087" in det                         # с одной стороны есть индекс и регион
    st, det = same("address", "ул. Мира, д. 19, кв. 187", "ул. Мира, д. 19, кв. 188")
    assert st == "fail" and "187" in det                            # другая квартира - другой адрес
    assert same("address", "Москва, ул. Тверская, д 1", "Санкт-Петербург, Невский пр-кт, д. 1")[0] == "fail"
    assert same("address", "", "ул. Мира")[0] == ""
    assert same("email", " Info@Example.RU", "info@example.ru")[0] == "ok"
    assert same("email", "a@example.ru", "b@example.ru")[0] == "fail"


# ------------------------------------------------------------------ текущий владелец: юрлицо и ИП

def test_compare_owner_org():
    app = {"org_r": "Общество с ограниченной ответственностью «ТЕСТ»", "code": "77 0123 4567", "kpp": "770101001",
           "e_mail": "org@example.org", "address_r_addr": "улица Тестовая, дом 1, оф. 5",
           "p_addr_addr": "140000, Московская обл., ул. Тестовая, д. 1, офис 5", "phone": "8 (495) 000-00-00"}
    r = account.compare_owner(BA_ORG, app, "org")
    assert r["kind"] == "org" and r["group"] == "ru_org"
    assert [x["field"] for x in r["rows"]] == ["org_r", "code", "kpp", "address_r_addr", "p_addr_addr", "phone", "e_mail"]
    st = {x["field"]: x["status"] for x in r["rows"]}
    assert st["org_r"] == "ok"                                        # ООО = Общество с ограниченной ответственностью
    assert st["code"] == st["kpp"] == st["e_mail"] == st["address_r_addr"] == "ok"
    assert st["p_addr_addr"] == "warn"                                # в заявлении ещё индекс и область
    assert st["phone"] == "ok"                                        # второй телефон анкеты
    assert r["ok"] == 6 and r["total"] == 7 and not r["mismatch"]
    bad = account.compare_owner(BA_ORG, {"org_r": "ООО «Тест-2»", "kpp": "770101002"}, "org")
    assert bad["mismatch"] == ["Название организации", "КПП"]


def test_compare_owner_ip():
    ba = {"person_r_surname": "Предпринимателева", "person_r_name": "Ирина", "code": "500100732259",
          "passport_number": "6010 123456"}
    r = account.compare_owner(ba, {"code": "500100732259", "passport_number": "6010123456",
                                   "person_r_surname": "предпринимателева"}, "ip")
    rows = {x["field"]: x for x in r["rows"]}
    assert r["group"] == "ru_pp" and rows["code"]["title"] == "ИНН ИП" and rows["code"]["status"] == "ok"
    assert r["ok"] == 3 and not r["mismatch"]
    assert "code" not in {x["field"] for x in account.compare_owner(ba, {}, "pp")["rows"]}   # у физлица ИНН не сверяется
    assert account.compare_owner(ba, {"code": "500100732250"}, "ip")["mismatch"] == ["ИНН ИП"]


# ------------------------------------------------------------------ новый владелец: ИП и юрлицо

def test_prepare_fill_ip():
    r = account.prepare_fill({"surname": "ПРЕДПРИНИМАТЕЛЕВА", "name": "Ирина", "inn": "5001 0073 2259",
                              "address": "г. Тестоград, ул. Садовая, д. 3, кв. 4", "zip": "347 000",
                              "passport": "6010 123456"}, "ip")
    f = r["fields"]
    assert r["kind"] == "ip" and f["code"] == "500100732259"
    assert f["p_addr_r_addr"] == f["p_addr_addr"] == "г. Тестоград, ул. Садовая, д. 3, кв. 4"   # адрес регистрации = почтовый
    assert f["p_addr_r_city"] == f["p_addr_city"] == "Тестоград" and f["p_addr_r_zip"] == f["p_addr_zip"] == "347000"
    assert [x["field"] for x in r["preview"]][6] == "code"           # порядок - как на странице
    assert set(f) <= {k for k, _ in account.FILL_FIELDS_IP}
    assert account.prepare_fill({"inn": "123"}, "ip")["notes"][0].startswith("ИНН ИП: 3 цифр")
    # физлицу ИНН и адрес регистрации ИП не передаются, даже если введены
    assert not {"code", "p_addr_r_addr"} & set(account.prepare_fill({"inn": "500100732259", "address": "ул. Мира"}, "pp")["fields"])


def test_prepare_fill_org():
    r = account.prepare_fill({"org_name": "ООО  «Ромашка»", "inn": "7701 234567", "kpp": "770101001",
                              "legal_address": "г. Москва, ул. Тверская, д. 1, офис 5", "legal_zip": "125009",
                              "phone": "8 495 000 00 00", "email": "Info@Romashka.ru", "acct_email": "Buh@Romashka.ru"},
                             "org")
    f = r["fields"]
    assert r["kind"] == "org" and f["org_r"] == "ООО «Ромашка»" and f["code"] == "7701234567" and f["kpp"] == "770101001"
    assert f["address_r_addr"] == f["p_addr_addr"] == "г. Москва, ул. Тверская, д. 1, офис 5"   # почтовый пуст → физический
    assert f["address_r_city"] == f["p_addr_city"] == "Москва" and f["address_r_zip"] == f["p_addr_zip"] == "125009"
    assert f["p_addr_recipient"] == "ООО «Ромашка»"                  # получатель по умолчанию - организация
    assert f["phone"] == "+74950000000" and f["e_mail"] == "info@romashka.ru" and f["actt_e_mail"] == "buh@romashka.ru"
    assert any("Почтовый адрес не указан" in n for n in r["notes"])
    assert "Страна регистрации" in r["manual"] and "Название родительской организации" in r["untouched"]
    assert set(f) <= {k for k, _ in account.FILL_FIELDS_ORG}
    assert not {"person_r_surname", "passport_number"} & set(account.prepare_fill({"surname": "Иванов"}, "org")["fields"])
    r2 = account.prepare_fill({"org_name": "АО «Тест»", "legal_address": "г. Тула, ул. Ленина, 1",
                               "postal_address": "а/я 15, г. Тула", "zip": "300000", "recipient": "Иванов И. И."}, "org")
    f2 = r2["fields"]
    assert f2["p_addr_addr"] == "а/я 15, г. Тула" and f2["p_addr_zip"] == "300000" and "address_r_zip" not in f2
    assert f2["p_addr_recipient"] == "Иванов И. И."


def test_verify_saved_org():
    v = account.verify_saved({"org_r": "ООО «Ромашка»", "code": "7701234567", "e_mail": "info@romashka.ru"},
                             {"org_r": "ООО «Ромашка»", "code": "7701 234567", "e_mail": "Info@Romashka.ru", "kpp": "770101001"})
    assert not v["ok"] and v["mismatch"] == ["КПП"]


# ------------------------------------------------------------------ страницы manager (Playwright)

try:                                     # без Playwright тесты страниц пропускаются, остальные - нет
    from playwright import sync_api
except ImportError:  # pragma: no cover
    sync_api = None


@pytest.fixture(scope="module")
def browser():
    if sync_api is None:
        pytest.skip("нет Playwright")
    with sync_api.sync_playwright() as pw:
        try:
            b = pw.chromium.launch()
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"Chromium для Playwright не установлен: {e}")
        yield b
        b.close()


@pytest.fixture(scope="module")
def page(browser):
    return browser.new_page()


def run(page, fixture, expr):
    page.goto((FIX / fixture).as_uri())
    page.add_script_tag(content=JS)
    return json.loads(page.evaluate(f"() => {{ const M = window.__doctoolManager; return JSON.stringify({expr}); }}"))


def test_user_details_org_and_ip(page):
    org = run(page, "user_details_org.html", "M.parseUserDetails(document)")
    assert org["user_id"] == "2000" and org["contypes"] == "RUSURF: ru_org" and org["is_entrepreneur"] is False
    assert org["ba"]["ru_org.org_r"] == "ООО «Тест»" and org["ba"]["ru_org.kpp"] == "770101001"
    a = account.account_from_extension({**org, "runic": {}})
    assert a["type"] == "org" and a["ba_group"] == "ru_org" and a["ba"]["code"] == "7701234567"
    cmp_ = account.compare_owner(a["ba"], {"org_r": "ООО \"ТЕСТ\"", "e_mail": "org@example.org"}, a["type"])
    assert {x["field"]: x["status"] for x in cmp_["rows"]}["org_r"] == "ok" and cmp_["ok"] == 2
    ip = run(page, "user_details_ip.html", "M.parseUserDetails(document)")
    assert ip["is_entrepreneur"] is True and ip["ba"]["ru_pp.code"] == "500100732259"
    b = account.account_from_extension({**ip, "runic": {}})
    assert b["type"] == "ip" and b["ba"]["p_addr_r_city"] == "Тестоград"


def test_runic_parse_ip_and_org(page):
    ip = run(page, "runic_ip.html", "M.parseRunic(document)")
    assert ip["user_id"] == "3000" and ip["type"] == "ip" and ip["values"]["code"] == "500100732259"
    assert ip["values"]["p_addr_r_addr"] == "ул. Садовая, д. 3, кв. 4" and ip["values_org"]["org_r"] == ""
    org = run(page, "runic_org.html", "M.parseRunic(document)")
    assert org["type"] == "org" and org["values"]["org_r"] == "ООО «Тест»" and org["values"]["kpp"] == "770101001"
    assert org["values"] is not None and org["values_pp"]["person_r_surname"] == "Тестов"
    pp = run(page, "runic_pp.html", "M.parseRunic(document)")
    assert pp["type"] == "pp" and "code" not in pp["values"] and "p_addr_r_addr" not in pp["values"]   # строки ИП пусты


def _val(page, form, name):
    return page.evaluate(f"() => document.querySelector('#{form} [name={name}]').value")


def test_fill_org_switches_type_and_back(page):
    """Аккаунт физлица, новый владелец - юрлицо: тип «Физик» → «Юрик», заполняется форма юрлица; «Вернуть как было»
    возвращает и значения, и тип."""
    page.goto((FIX / "runic_pp.html").as_uri())
    page.add_script_tag(content=JS)
    fields = account.prepare_fill({"org_name": "ООО «Ромашка»", "inn": "7701234567", "kpp": "770101001",
                                   "legal_address": "г. Москва, ул. Тверская, д. 1", "phone": "+74950000000",
                                   "email": "info@romashka.ru"}, "org")["fields"]
    r = page.evaluate("f => window.__doctoolManager.fillRunic('1000', f, {type: 'org'})", fields)
    assert r["ok"], r
    d = r["data"]
    assert d["type_switched"] and d["type_old"] == "pp" and d["type"] == "org" and d["type_page"] == "Юрик"
    assert page.evaluate("() => document.querySelector('input[name=type]:checked').value") == "org"
    assert "hidden" in page.evaluate("() => document.getElementById('ru_pp_contacts').className")
    assert _val(page, "ru_org_contacts", "org_r") == "ООО «Ромашка»" and _val(page, "ru_org_contacts", "kpp") == "770101001"
    assert _val(page, "ru_org_contacts", "address_r_city") == "Москва"
    assert _val(page, "ru_org_contacts", "org") == "OOO «Romashka»"                       # English name - страница (blur)
    assert _val(page, "ru_pp_contacts", "person_r_surname") == "Тестов"                   # форма физлица не тронута
    assert d["values"]["org_r"] == "ООО «Ромашка»"
    old = {c["name"]: c["old"] for c in d["changed"]}
    back = page.evaluate("f => window.__doctoolManager.fillRunic('1000', f, {type: 'org', restore: true, back_type: 'pp'})", old)
    assert back["ok"] and back["data"]["type_back"] == "pp"
    assert page.evaluate("() => document.querySelector('input[name=type]:checked').value") == "pp"
    assert _val(page, "ru_org_contacts", "org_r") == "" and "hidden" in page.evaluate(
        "() => document.getElementById('ru_org_contacts').className")


def test_fill_ip_from_person(page):
    page.goto((FIX / "runic_pp.html").as_uri())
    page.add_script_tag(content=JS)
    fields = account.prepare_fill({"surname": "Предпринимателева", "name": "Ирина", "inn": "500100732259",
                                   "address": "г. Тестоград, ул. Садовая, д. 3", "zip": "347000"}, "ip")["fields"]
    r = page.evaluate("f => window.__doctoolManager.fillRunic('1000', f, {type: 'ip'})", fields)
    assert r["ok"] and r["data"]["type_switched"] and r["data"]["type_page"] == "ИП"
    assert _val(page, "ru_pp_contacts", "is_entrepreneur") == "1"
    assert _val(page, "ru_pp_contacts", "code") == "500100732259" and _val(page, "ru_pp_contacts", "p_addr_r_zip") == "347000"
    assert _val(page, "ru_pp_contacts", "p_addr_r_addr") == _val(page, "ru_pp_contacts", "p_addr_addr")
    assert _val(page, "ru_org_contacts", "code") == ""                                    # одноимённое поле юрлица не тронуто
    names = [c["name"] for c in r["data"]["changed"]]
    assert names.index("code") < names.index("p_addr_r_zip") < names.index("p_addr_zip")   # порядок страницы


def test_fill_same_type_and_guards(page):
    page.goto((FIX / "runic_org.html").as_uri())
    page.add_script_tag(content=JS)
    r = page.evaluate("f => window.__doctoolManager.fillRunic('2000', f, {type: 'org'})",
                      {"phone": "+74951112233", "person_r_surname": "Иванов"})
    assert r["ok"] and not r["data"]["type_switched"] and [c["name"] for c in r["data"]["changed"]] == ["phone"]
    # без типа (как вызывала программа 0.7.2) - по-прежнему только физлицо
    r = page.evaluate("f => window.__doctoolManager.fillRunic('2000', f)", {"phone": "+74951112233"})
    assert not r["ok"] and r["error"]["code"] == "not_person"
    # страница не показала форму после переключения - ошибка, ничего не заполняется
    page.goto((FIX / "runic_pp.html").as_uri())
    page.evaluate("() => document.querySelectorAll('input[name=type]').forEach(r => r.replaceWith(r.cloneNode(true)))")
    page.add_script_tag(content=JS)
    r = page.evaluate("f => window.__doctoolManager.fillRunic('1000', f, {type: 'org'})", {"org_r": "ООО «Х»"})
    assert not r["ok"] and r["error"]["code"] == "type_switch" and _val(page, "ru_org_contacts", "org_r") == ""


# ------------------------------------------------------------------ ручной ввод данных заявления

def test_manual_case_recompute(tmp_path):
    from doctool.service import CaseInput, manual_case, recompute, table_rows
    res = manual_case(CaseInput(case_type="admin_change_person", out_root=str(tmp_path)))
    assert res.manual and res.app.doc_type == "regru_transfer_person" and not res.registered
    rows = {r["id"]: r for r in table_rows(res)}
    # все строки с данными заявления доступны для ввода; строк только паспорта нет; паспорт не правится
    assert {"fio", "birth_date", "passport", "issued_by", "domains", "new_admin", "contract"} <= set(rows)
    assert "department_code" not in rows and not any(r["editable_passport"] for r in rows.values())
    recompute(res, {"fio": {"app": "Тестов Пётр Сергеевич"}, "birth_date": {"app": "15.03.1990"},
                    "domains": {"app": "testdomain-pp.ru, пример.рф"}}, [])
    assert res.app.get("domains.list") == ["testdomain-pp.ru", "пример.рф"]
    assert res.app.get("applicant_header.birth_date") == "15.03.1990" and res.registered
    assert (Path(tmp_path) / "реестр_проверок.xlsx").exists()
    assert res.record["application_fields_reader"] == "manual"
    # стёртое значение - пустое (раньше правка на пустое не применялась)
    recompute(res, {"fio": {"app": ""}}, [])
    assert not res.app.get("applicant_fio") and res.app.get("applicant_header.birth_date") == "15.03.1990"


def test_manual_case_web_and_manager(tmp_path):
    """Ручной ввод через веб: дело без файлов → «Пересчитать» → домены уходят на поиск в manager (как делает
    страница) → данные Sd сверяются с введёнными данными."""
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from doctool.web import create_app
    from test_domains import S_PP, SD_PP, reply
    ORIGIN = {"origin": "chrome-extension://abcdefghijklmnop"}

    app = create_app(out_root=str(tmp_path))
    app.state.manager.use_whois = False
    client = TestClient(app)
    assert client.post("/api/jobs/manual", json={"case_type": "нет_такого"}).status_code == 400
    j = client.post("/api/jobs/manual", json={"case_type": "admin_change_person"}).json()
    job_id, res = j["job_id"], j["result"]
    assert res["manual"] and not res["has_passport"] and res["domains_pending"] == []
    assert {r["id"] for r in res["rows"]} >= {"fio", "domains"}
    res = client.post(f"/api/jobs/{job_id}/recompute", json={"overrides": {
        "fio": {"app": "Тестов Пётр Сергеевич"}, "birth_date": {"app": "15.03.1990"},
        "passport": {"app": "4509 123456"}, "domains": {"app": "testdomain-pp.ru"}}, "confirmed": []}).json()
    assert res["domains_pending"] == ["testdomain-pp.ru"] and res["domains_from_app"] == ["testdomain-pp.ru"]
    with client.websocket_connect("/ext", headers=ORIGIN) as ws:
        ws.receive_json()
        t = client.post("/api/manager/lookup", json={"domains": "\n".join(res["domains_from_app"]), "job_id": job_id}).json()
        req = ws.receive_json()
        assert req["params"]["domain"] == "testdomain-pp.ru"
        ws.send_json({"type": "result", "id": req["id"], **reply(SD_PP, S_PP, "testdomain-pp.ru")})
        for _ in range(100):
            out = client.get(f"/api/manager/tasks/{t['task_id']}").json()
            if out["status"] != "running":
                break
            time.sleep(0.05)
    r = out["result"]
    assert r["domains_pending"] == [] and r["domains"][0]["holder"] == "Тестов Пётр Сергеевич"
    cmp_ = {x["field"]: x["status"] for x in r["domains"][0]["compare"]}
    assert cmp_["fio"] == "ok" and cmp_["birth_date"] == "ok" and cmp_["passport"] == "ok"   # Sd = введённые данные
    chk = {c["name"]: c["status"] for c in r["checks"]}
    assert chk["Заявитель - администратор доменов (Sd)"] == "ok" and chk["Паспорт в Sd"] == "ok"
    # ввели другие данные - Sd расходится с заявлением
    r2 = client.post(f"/api/jobs/{job_id}/recompute", json={"overrides": {"fio": {"app": "Иванов Иван Иванович"},
                                                                           "passport": {"app": "4510 654321"}},
                                                            "confirmed": []}).json()
    assert r2["domains"][0]["verdict"] == "fail" and r2["decision"]["code"] == "reject"


# ------------------------------------------------------------------ веб-интерфейс в браузере

def _serve(tmp_path):
    import socket

    import uvicorn

    from doctool.web import create_app
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(create_app(out_root=str(tmp_path)), host="127.0.0.1", port=port,
                                           log_level="warning"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    return server, f"http://127.0.0.1:{port}"


def test_web_manual_entry_owner_kinds_and_domains_width(tmp_path, browser):
    pytest.importorskip("uvicorn")
    server, url = _serve(tmp_path)
    errors = []
    try:
        if True:
            pg = browser.new_page(viewport={"width": 1366, "height": 900})
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.goto(url)
            pg.click("nav.tabs button[data-tab=tabCheck]")      # (0.7.6) по умолчанию открыто рабочее место
            # 1) (0.8.1) вкладка - только «Домены в manager»: «Новой проверки» заявления нет
            assert pg.text_content("nav.tabs button[data-tab=tabCheck]").strip() == "Домены в manager"
            assert pg.locator("#formCard, #resultCard, #progressCard, #caseType").count() == 0
            pg.fill("#domList", "testdomain-pp.ru")
            pg.click("#domLoad")                                  # расширения нет - сообщение
            pg.wait_for_function("() => document.querySelector('#domHint').textContent.includes('не подключено')")
            # 2) окно «Домены в manager» без прокрутки вбок при длинных значениях; логин и e-mail аккаунта - раздельно
            long = {"domain": "ochen-dlinnoe-imya-domena-dlya-proverki-shiriny.ru", "status": "found", "kind": "person",
                    "fio": "Константинопольский Константин Константинович", "passport": "4509 123456",
                    "sd": {"birth_date": "15.03.1990", "passport_date": "20.04.2010",
                           "passport_place": "ГУ МВД РОССИИ ПО Г. МОСКВЕ И МОСКОВСКОЙ ОБЛАСТИ (ОТДЕЛ ПО ВОПРОСАМ МИГРАЦИИ)"},
                    "emails": ["very.long.administrator.email.address@example-company-domain.ru"],
                    "provider": "cctldru", "account": "1000", "login": "person-login", "account_email": "owner.mail@example.org",
                    "verdict": "review", "esia_status": "match", "mo_status": "mismatch",
                    "mo_ru": "Sd ≠ анкета: Телефон, E-mail, Почтовый адрес, Кем выдан",
                    "esia_state": "approved", "esia_ru": "approved · Sd = ЕСИА (9 из 9)",
                    "flags": [{"level": "warn", "text": "Смена администратора меньше 30 дней назад"}],
                    "last_admin_change": "2025-11-20 10:00:00", "service_status": "Активна (A)", "problem": "review",
                    "problem_ru": "Нужна ручная проверка", "problem_rank": 4, "urls": {"sd": "https://manager.reg.ru/x"}}
            pg.evaluate("items => renderDomains(items)", [long, {**long, "domain": "vtoroy-domen.рф"}])
            w = pg.evaluate("() => { const w = document.querySelector('#domCard .wrap'); return [w.scrollWidth, w.clientWidth,"
                            " document.documentElement.scrollWidth, window.innerWidth]; }")
            assert w[0] <= w[1] + 1 and w[2] <= w[3], w                                  # нет прокрутки вбок
            cell = pg.text_content("#domTable tbody tr:nth-child(2) td:nth-child(10)")
            assert "логин: person-login" in cell and "e-mail: owner.mail@example.org" in cell, cell
            assert "Смена администратора меньше 30 дней" in pg.text_content("#domTable tbody tr:nth-child(2) td:nth-child(11)")
            mo = pg.text_content("#domTable tbody tr:nth-child(2) td:nth-child(6)")        # (0.8.4) колонка MO
            assert "не совпадает" in mo and "Телефон" in mo, mo
            # 3) смена владельца: поля по типу нового владельца, сверка юрлица - поля ru_org
            pg.click("nav.tabs button[data-tab=tabOwner]")
            pg.check("input[name=ownKind][value=org]")
            assert pg.is_visible("#ownForm [data-k=org_name]") and not pg.is_visible("#ownForm [data-k=surname]")
            assert pg.text_content("#ownInnTitle") == "ИНН организации"
            pg.fill("#ownForm [data-k=org_name]", "ООО «Ромашка»")
            pg.wait_for_function("() => document.querySelector('#ownPrev').textContent.includes('Юр. адрес')")
            pg.check("input[name=ownKind][value=ip]")
            assert pg.is_visible("#ownForm [data-k=inn]") and pg.is_visible("#ownForm [data-k=surname]")
            assert not pg.is_visible("#ownForm [data-k=org_name]")
            pg.wait_for_function("() => document.querySelector('#ownPrev').textContent.includes('Адрес регистр.')")
            pg.check("input[name=ownCmpKind][value=org]")
            pg.wait_for_function("() => document.querySelector('#ownCmp').textContent.includes('ru_org.kpp')")
            assert "Физический адрес" in pg.text_content("#ownCmp")
            pg.check("input[name=ownCmpKind][value=ip]")
            pg.wait_for_function("() => document.querySelector('#ownCmp').textContent.includes('ru_pp.code')")
            pg.close()
    finally:
        server.should_exit = True
    assert not errors, errors
