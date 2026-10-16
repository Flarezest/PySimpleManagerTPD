"""Разбор страниц manager скриптом расширения (doctool_extension/content/manager.js) на обезличенных копиях
страниц. Нужен Playwright с Chromium (pip install playwright && python -m playwright install chromium);
без него тест пропускается. Если manager поменяет вёрстку — сохраните новую страницу, обезличьте её,
положите в tests/fixtures/manager/ и прогоните тест."""
import json
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")
ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures" / "manager"
JS = (ROOT / "doctool_extension" / "content" / "manager.js").read_text(encoding="utf-8")
BG = (ROOT / "doctool_extension" / "background.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def page():
    with sync_api.sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"Chromium для Playwright не установлен: {e}")
        yield browser.new_page()
        browser.close()


def run(page, fixture, expr):
    page.goto((FIX / fixture).as_uri())
    page.add_script_tag(content=JS)
    return page.evaluate(f"() => {{ const M = window.__doctoolManager; return JSON.stringify({expr}); }}")


def test_bills(page):
    r = json.loads(run(page, "bills_pp.html", 'M.pickService(M.parseBills(document).rows, ["testdomain-pp.ru"])'))
    assert r["service"]["service_id"] == "120862733" and r["service"]["state"] == "A"   # строка SSL пропущена
    r = json.loads(run(page, "bills_notfound.html", "M.parseBills(document)"))
    assert r == {"total": 0, "rows": []}


def test_sd_and_s(page):
    sd = json.loads(run(page, "sd_pp.html", "M.parseSd(document)"))
    assert sd["group"] == "ru_pp" and sd["fields"]["person_r_surname"] == "Тестов"
    assert sd["fields"]["last_admin_change"] == "2025-11-20 10:00:00" and "authinfo" not in sd["fields"]
    assert sd["trustee"] == "Нет" and sd["status"] == "Активна (A)"
    org = json.loads(run(page, "sd_org.html", "M.parseSd(document)"))
    assert org["group"] == "ru_org" and org["fields"]["code"] == "7701234567" and "FOA_token" not in org["fields"]
    s = json.loads(run(page, "s_org.html", "M.parseS(document)"))
    assert s["provider"] == "cctldru" and s["dname"] == "testdomain-org.ru" and s["user_id"] == "2000"
    # (0.7.6) логин аккаунта со страницы S, комментарий администратора — да, комментарий пользователя — нет
    assert s["login"] == "org@example.com" and s["admin_comment"] == ""
    s = json.loads(run(page, "s_pp.html", "M.parseS(document)"))
    assert s["login"] == "person@example.com" and s["user_id"] == "1000"
    assert s["admin_comment"].startswith("Тест: смену администратора") and "comment" not in s
    assert s["changeProhibited"] == "0" and s["state"] == "A"


def test_sd_email_and_intl(page):
    # .RU: e-mail администратора — поле e_mail группы ru_pp/ru_org (с 0.5.0 передаётся в doctool)
    sd = json.loads(run(page, "sd_pp.html", "M.parseSd(document)"))
    assert sd["fields"]["e_mail"].splitlines()[0] == "person@example.com"
    # международная зона: группа владельца o_deti_org (не a_/t_, хотя в них больше строк), e-mail — o_email
    intl = json.loads(run(page, "sd_intl.html", "M.parseSd(document)"))
    assert intl["group"] == "o_deti_org" and intl["status"] == "Активна (A)"
    assert intl["fields"]["o_email"] == "owner@example.com\nsecond@example.org"
    # (0.8.2) телефон администратора передаётся (для доверенности), телефоны контактов a_/t_ — нет
    assert intl["fields"]["o_company"] == "Test Holding LP" and intl["fields"]["o_phone"] == "+70000000000"
    assert "a_phone" not in intl["fields"] and "t_phone" not in intl["fields"]
    assert "authinfo" not in intl["fields"] and "o_addr" not in intl["fields"]


# ------------------------------------------------------------------ 0.6.0: ЕСИА, аккаунт, базовая анкета

def test_sd_links_to_esia(page):
    # ссылка «Идентификация через Госуслуги» берётся со страницы Sd домена: у физлица ?user_id=, у персоны — ?person_id=
    sd = json.loads(run(page, "sd_pp.html", "M.parseSd(document)"))
    assert sd["links"]["esia"] == "https://manager.reg.ru/manager/esia_identifications?user_id=1000"
    assert sd["links"]["person"] == "https://manager.reg.ru/manager/user_details?user_id=1000"
    org = json.loads(run(page, "sd_org.html", "M.parseSd(document)"))
    assert org["links"]["esia"].endswith("esia_identifications?person_id=3000")


def test_esia_list(page):
    r = json.loads(run(page, "esia_list.html", "M.parseEsiaList(document)"))
    assert len(r["rows"]) == 2
    latest = r["latest"]                                    # последняя попытка — по дате создания
    assert latest["state"] == "approved" and latest["creation_date"] == "2026-08-01 18:55:32"
    assert latest["file_url"] == "https://identity.reg.ru/esia/0123456789abcdef0123456789abcdef.json"
    assert latest["action"] == "fill_base_contacts" and "file" not in latest
    old = next(x for x in r["rows"] if x["state"] == "rejected")
    assert old["file_url"] == "" and old["reason"] == "passport_not_verified"


def test_user_details(page):
    r = json.loads(run(page, "user_details_pp.html", "M.parseUserDetails(document)"))
    assert r["user_id"] == "1000" and r["login"] == "person@example.com"
    # (0.8.1) e-mail аккаунта — поле «E-mail» (input name=email), не логин
    assert r["email"] == "owner.mail@example.org"
    assert "Идентификация через Госуслуги пройдена" in r["statuses"]
    assert r["servicing_org"] == 'ООО "РДХ"' and r["servicing_org_id"] == "3"
    assert r["contypes"] == "RUSURF: ru_pp" and r["is_entrepreneur"] is False
    ba = r["ba"]                                             # базовая анкета — скрытый блок страницы
    assert ba["ru_pp.person_r_surname"] == "Тестов" and ba["ru_pp.passport_number"] == "4509123456"
    assert ba["ru_pp.passport_place"] == "ГУ МВД РОССИИ ПО Г. МОСКВЕ" and ba["ru_pp.fax"] == ""
    assert ba["ru_pp.phone"] == "+79990000000" and ba["ru_pp.birth_date"] == "15.03.1990"
    assert r["links"]["runic"].endswith("/user/1000/runic_details")
    assert r["links"]["esia"].endswith("esia_identifications?user_id=1000")


def test_runic_parse(page):
    r = json.loads(run(page, "runic_pp.html", "M.parseRunic(document)"))
    assert r["user_id"] == "1000" and r["type"] == "pp" and r["has_pp_form"]
    v = r["values"]
    assert v["person_r_surname"] == "Тестов" and v["person"] == "Petr Sergeevich Testov"
    assert v["passport_place"] == "ГУ МВД РОССИИ ПО Г. МОСКВЕ" and v["country"] == "RU" and v["p_addr_zip"] == "101000"
    # страница базовой анкеты — «новая» (Bootstrap): без #content, но это страница manager
    assert page.evaluate("() => window.__doctoolManager.isManagerPage(document)")


NEW_OWNER = {"person_r_surname": "Сидорова", "person_r_name": "Анна", "person_r_patronimic": "Викторовна",
             "passport_number": "4012654321", "passport_date": "03.12.2010", "passport_place": "ТП № 5 ОУФМС России",
             "birth_date": "21.11.1990", "p_addr_city": "Санкт-Петербург", "p_addr_addr": "г. Санкт-Петербург, ул. Новая, д. 7",
             "p_addr_recipient": "Сидорова Анна Викторовна", "phone": "+79215551234", "e_mail": "anna@example.com",
             "country": "KZ", "sms_security_number": "+70000000000", "p_addr_area": "Ленинградская"}


def test_fill_runic(page):
    page.goto((FIX / "runic_pp.html").as_uri())
    page.add_script_tag(content=JS)
    r = page.evaluate("f => window.__doctoolManager.fillRunic('1000', f)", NEW_OWNER)
    assert r["ok"], r
    names = {c["name"] for c in r["data"]["changed"]}
    # гражданство, SMS-безопасность, область не заполняются, даже если их передать
    assert names == {"person_r_surname", "person_r_name", "person_r_patronimic", "passport_number", "passport_date",
                     "passport_place", "birth_date", "p_addr_city", "p_addr_addr", "p_addr_recipient", "phone", "e_mail"}
    val = lambda n: page.evaluate(f"() => document.querySelector('#ru_pp_contacts [name={n}]').value")  # noqa: E731
    assert val("person_r_surname") == "Сидорова" and val("passport_place") == "ТП № 5 ОУФМС России"
    assert val("country") == "RU" and val("sms_security_number") == "" and val("p_addr_area") == "Москва"
    assert val("p_addr_zip") == "101000"                    # индекс не передан — остался прежний
    # форма юрлица с теми же именами полей не тронута
    assert page.evaluate("() => document.querySelector('#ru_org_contacts [name=phone]').value") == "+79990000000"
    # события страницы: поле помечено изменённым, English name пересчитан по blur
    assert "unsaved" in page.evaluate("() => document.querySelector('#ru_pp_contacts [name=phone]').className")
    assert val("person") == "Anna Viktorovna Sidorova"
    old = {c["name"]: c["old"] for c in r["data"]["changed"]}
    assert old["person_r_surname"] == "Тестов" and old["p_addr_addr"] == "Тестовая, д 1, кв 1"
    # «Вернуть как было»
    back = page.evaluate("f => window.__doctoolManager.fillRunic('1000', f, {restore: true})", old)
    assert back["ok"] and val("person_r_surname") == "Тестов" and val("phone") == "+79990000000"


def test_fill_runic_guards(page):
    page.goto((FIX / "runic_pp.html").as_uri())
    page.add_script_tag(content=JS)
    r = page.evaluate("f => window.__doctoolManager.fillRunic('2000', f)", NEW_OWNER)
    assert not r["ok"] and r["error"]["code"] == "wrong_account"
    page.evaluate("() => { document.querySelector('input[name=type][value=pp]').checked = false;"
                  " document.querySelector('input[name=type][value=org]').checked = true; }")
    r = page.evaluate("f => window.__doctoolManager.fillRunic('1000', f)", NEW_OWNER)
    assert not r["ok"] and r["error"]["code"] == "not_person" and "юрлиц" in r["error"]["message"]
    assert page.evaluate("() => document.querySelector('#ru_pp_contacts [name=person_r_surname]').value") == "Тестов"


# ------------------------------------------------------------------ 0.7.2: ЕСИА у юрлиц

def test_sd_org_legal_address(page):
    # юридический адрес юрлица — для сверки с ЕСИА; (0.8.4) почтовый адрес и квартира — для сверки MO
    org = json.loads(run(page, "sd_org.html", "M.parseSd(document)"))
    f = org["fields"]
    assert f["address_r_street"] == "ул. Тестовая" and f["address_r_house"] == "1" and f["address_r_zip"] == "140000"
    assert f["address_r_frame"] == "" and f["address_r_building"] == ""
    assert f["address_r_flat"] == "41" and f["p_addr_street"] == "Садовая" and f["p_addr_house"] == "2"
    assert "address_r_district" not in f and "sms_security_number" not in f and "transfer_email" not in f
    assert f["phone"].splitlines()[0].startswith("+7")          # (0.8.2) телефон — для доверенности


def test_esia_fields_org_and_person(page):
    # esiaFields() из background.js: что из файла ЕСИА уходит в программу
    import re
    src = re.search(r"function esiaFields\(j\) \{.*?\n\}\n", BG, re.S).group(0)
    page.goto("about:blank")
    org_json = json.loads((FIX / "esia_org.json").read_text(encoding="utf-8"))
    org = page.evaluate(f"j => {{ {src}; return esiaFields(j); }}", org_json)
    assert org["kind"] == "org" and org["inn"] == "7701234567" and org["kpp"] == "770101001"
    assert org["full_name"].startswith("ОБЩЕСТВО С ОГРАНИЧЕННОЙ") and org["ogrn"] == "1000000000001"
    assert org["is_liquidated"] is False and org["trusted"] is True
    la = org["legal_address"]
    assert la["house"] == "Д. 1" and la["street"] == "УЛ. ТЕСТОВАЯ" and la["zip_code"] == "140000"
    text = json.dumps(org, ensure_ascii=False)
    for secret in ("prs_auth", "Тестов", "+7(999)", "org@example.com", "mail_address", "oid"):
        assert secret not in text, secret
    pp = page.evaluate(f"j => {{ {src}; return esiaFields(j); }}",
                       json.loads((FIX / "esia_pp.json").read_text(encoding="utf-8")))
    assert pp["last_name"] == "Тестов" and "inn" not in pp and "snils" not in pp and "kind" not in pp


# ------------------------------------------------------------------ 0.7.6: смена администратора и Госуслуги

def test_domain_move_fill(page):
    # шаг 1 «Перенос доменов между аккаунтами»: домены, флажки, принимающий аккаунт, документы в поле файлов
    import base64
    files = [{"name": "Заявление.pdf", "type": "application/pdf", "b64": base64.b64encode(b"%PDF-1.4 test").decode()},
             {"name": "Паспорт.pdf", "type": "application/pdf", "b64": base64.b64encode(b"%PDF-1.4 pass").decode()}]
    params = {"domains": ["testdomain-pp.ru", "пример.рф"], "change_admin": True, "change_account": True,
              "login_to": "user_id:2000", "files": files}
    r = json.loads(run(page, "domain_move.html", f"M.domainMoveFill({json.dumps(params, ensure_ascii=False)})"))
    assert r["ok"] and r["data"]["attached"] == ["Заявление.pdf", "Паспорт.pdf"], r
    st = page.evaluate("""() => { const f = document.querySelector('[name=domains]').closest('form');
      return {domains: f.querySelector('[name=domains]').value, admin: f.querySelector('[name=do_change_admin]').checked,
              acc: f.querySelector('[name=do_change_account]').checked, login: f.querySelector('[name=login_to]').value,
              loginShown: getComputedStyle(f.querySelector('[name=login_to]').closest('tr')).display !== 'none',
              files: [...f.querySelector('input[type=file]').files].map(x => x.name + ':' + x.size),
              untouched: ['skip_contacts_check', 'allow_rereg_only_bill', 'keep_partcontrol', 'force_cancel_deal']
                .map(n => f.querySelector('[name=' + n + ']').checked)}; }""")
    assert st["domains"] == "testdomain-pp.ru\nпример.рф" and st["admin"] and st["acc"] and st["loginShown"]
    assert st["login"] == "user_id:2000" and st["files"] == ["Заявление.pdf:13", "Паспорт.pdf:13"]
    assert st["untouched"] == [False, False, False, False]          # остальные флажки страницы не трогаются
    # не та страница — ошибка, ничего не заполняется
    r = json.loads(run(page, "domain_move_contacts.html", f"M.domainMoveFill({json.dumps(params, ensure_ascii=False)})"))
    assert not r["ok"] and r["error"]["code"] == "bad_page"


def test_domain_move_fill_without_domains(page):
    # (0.8.2) список доменов пуст: поле «Домены» не трогается, флажок смены администратора ставится
    page.goto((FIX / "domain_move.html").as_uri())
    page.add_script_tag(content=JS)
    page.evaluate("() => { document.querySelector('[name=domains]').value = 'typed-by-operator.ru'; }")
    r = page.evaluate("() => window.__doctoolManager.domainMoveFill({domains: [], change_admin: true})")
    assert r["ok"] and "Домены" not in r["data"]["changed"] and any("не вписаны" in n for n in r["data"]["notes"]), r
    st = page.evaluate("() => [document.querySelector('[name=domains]').value, document.querySelector('[name=do_change_admin]').checked]")
    assert st == ["typed-by-operator.ru", True]


def test_loa_fill(page):
    # (0.8.2) безбумажные операции: «Добавить новый документ от пользователя» (/userdocs/loa_add, доверенность)
    fields = {"passport_inn": "4000 123456", "fullname": "Тестов Пётр Иванович", "email": "person@example.com",
              "customer_phone": "+79990000001", "doc_start_date": "01.01.2026"}
    r = json.loads(run(page, "loa_add.html", f"M.loaFill({json.dumps(fields, ensure_ascii=False)})"))
    assert r["ok"] and [x["name"] for x in r["data"]["changed"]] == ["passport_inn", "fullname", "email", "customer_phone"], r
    assert r["data"]["missing"] == [] and r["data"]["doc_type"] == ""
    st = page.evaluate("""() => ({vals: ['passport_inn', 'fullname', 'email', 'customer_phone', 'doc_start_date', 'doc_end_date',
        'external_doc_id', 'force_doc_user_login'].map(n => document.querySelector('[name=' + n + ']').value),
        type: document.querySelector('[name=doc_type_and_subtype]').value, url: location.href,
        marked: document.querySelector('[name=fullname]').style.outlineStyle})""")
    assert st["vals"] == ["4000 123456", "Тестов Пётр Иванович", "person@example.com", "+79990000001", "", "", "", ""]
    assert st["type"] == "" and st["url"].endswith("loa_add.html") and st["marked"] == "solid"   # форма не отправлена
    # идентификатор администратора, который страница соберёт для «Показать домены…» (её же функция; $ — замена jQuery)
    rid = page.evaluate("() => { window.$ = s => ({val: () => document.querySelector(s).value}); return generate_registrant_id(); }")
    assert rid == "4000123456:ТестовПётрИванович"
    # юрлицо: код страны и ИНН — в те же поля; пустые значения не трогают поле
    r = json.loads(run(page, "loa_add.html", 'M.loaFill({passport_inn: "RU", fullname: "7701234567", email: ""})'))
    assert r["ok"] and [x["new"] for x in r["data"]["changed"]] == ["RU", "7701234567"]
    # не та страница — ошибка, ничего не заполняется
    r = json.loads(run(page, "domain_move.html", 'M.loaFill({passport_inn: "RU"})'))
    assert not r["ok"] and r["error"]["code"] == "bad_page"


def test_domain_move_step2(page):
    # шаг 2 «данные нового администратора»: чтение блока «данные нового аккаунта», подсветка, «заполнить вручную»
    r = json.loads(run(page, "domain_move_contacts.html", "M.domainMoveRead()"))
    assert r["ok"], r
    d = r["data"]
    assert d["mode"] == "new_owner" and d["domains"] == "testdomain-pp.ru" and d["current_user_id"] == "1000"
    b = d["base"]
    assert b["fio"] == "Тестов Пётр Иванович" and b["fio_en"] == "Testov Petr Ivanovich"
    assert b["passport_series"] == "4000" and b["passport_number"] == "123456" and b["passport_date"] == "01.02.2020"
    assert b["passport_place"] == "ГУ МВД РОССИИ ПО Г. ТЕСТОГРАДУ" and b["birth_date"] == "03.04.1990"
    assert b["email"] == "person@example.com" and b["phone"] == "+79990000000" and b["address"].startswith("190000")
    r = json.loads(page.evaluate("() => JSON.stringify(window.__doctoolManager.domainMoveMark({fio: 'ok', email: 'fail'}))"))
    assert r["ok"] and r["data"]["marked"] >= 2
    colors = page.evaluate("""() => [...document.querySelectorAll('#base-contacts b')].map(b => b.style.outlineColor).filter(Boolean)""")
    assert len(colors) == 2
    fields = {"person_r_surname": "Белов", "person_r_name": "Игорь", "person_r_patronimic": "Сергеевич",
              "phone": "+7 900 000-00-00", "e_mail": "belov@example.ru"}
    r = json.loads(page.evaluate(f"() => JSON.stringify(window.__doctoolManager.domainMoveFillContacts({json.dumps(fields, ensure_ascii=False)}))"))
    assert r["ok"] and len(r["data"]["changed"]) == 5, r
    st = page.evaluate("""() => ({fill: document.querySelector('#fill').checked,
        shown: document.getElementById('fill-contacts').style.display !== 'none',
        sur: document.getElementById('person_r_surname').value, mail: document.getElementById('e_mail').value,
        blank: document.getElementById('person_r_surname').classList.contains('blank'),
        code: document.getElementById('code').value, translit: window.translitCalls || 0})""")
    assert st["fill"] and st["shown"] and st["sur"] == "Белов" and st["mail"] == "belov@example.ru" and not st["blank"]
    assert st["code"] == "ИНН частного лица" and st["translit"] >= 3      # незаполненное поле осталось с подсказкой


def test_gosuslugi_fill(page):
    # fillGosuslugiForm() из background.js на копии формы «Проверка действительности паспорта» (маска серии «00 00»)
    import re
    src = re.search(r"function fillGosuslugiForm\(p\) \{.*?\n\}\n", BG, re.S).group(0)
    page.goto((FIX / "gosuslugi_passport_form.html").as_uri())
    r = page.evaluate(f"p => {{ {src}; return fillGosuslugiForm(p); }}",
                      {"series": "4510", "number": "123456", "surname": "Тестова", "name": "Анна"})
    assert r["ok"] and r["data"]["problems"] == [] and r["data"]["button_enabled"], r
    assert r["data"]["filled"]["series"] == "45 10" and r["data"]["filled"]["surname"] == "Тестова"
    assert page.evaluate("() => document.querySelector('button').disabled") is False    # «Продолжить» не нажимается


def test_lookup_account_email(page):
    """(0.8.1) Загрузка домена: e-mail аккаунта — поле «E-mail» «Данных пользователя» владельца услуги (не логин);
    страница аккаунта читается один раз на несколько доменов."""
    pages = {"/bill/bills": "bills_pp.html", "/tech/srv_details": "sd_pp.html", "/tech/service_details": "s_pp.html",
             "/manager/user_details": "user_details_pp.html"}
    hits = []

    def handle(route):
        from urllib.parse import urlparse
        path = urlparse(route.request.url).path
        hits.append(path)
        name = pages.get(path, "home.html")
        route.fulfill(status=200, content_type="text/html; charset=utf-8", body=(FIX / name).read_text(encoding="utf-8"))

    page.route("https://manager.reg.ru/**", handle)
    try:
        page.goto("https://manager.reg.ru/")
        page.add_script_tag(content=JS)
        r = page.evaluate("""async () => { const M = window.__doctoolManager;
            const a = await M.lookup('t1', 'testdomain-pp.ru', 'testdomain-pp.ru', {esia: false});
            const b = await M.lookup('t2', 'testdomain-pp.ru', 'testdomain-pp.ru', {esia: false});
            return [a, b]; }""")
    finally:
        page.unroute("https://manager.reg.ru/**")
    a, b = r
    assert a["ok"] and a["data"]["login"] == "person@example.com", a
    assert a["data"]["account"] == "1000" and a["data"]["account_email"] == "owner.mail@example.org"
    assert b["data"]["account_email"] == "owner.mail@example.org" and hits.count("/manager/user_details") == 1


def test_lookup_account_owner_for_mo(page):
    """(0.8.4) MO: при загрузке домена — базовая анкета владельца аккаунта (только поля сверки) со страницы «Данные
    пользователя» (той же, что для e-mail, — без лишних запросов); нет блока анкеты — форма /user/N/runic_details."""
    import re as _re
    no_block = _re.sub(r'<div class="user_contacts_block".*?</table>\s*</div>', "",
                       (FIX / "user_details_pp.html").read_text(encoding="utf-8"), flags=_re.S)
    assert "user_contacts_block" not in no_block
    pages = {"/bill/bills": "bills_pp.html", "/tech/srv_details": "sd_pp.html", "/tech/service_details": "s_pp.html",
             "/manager/user_details": "user_details_pp.html", "/user/1000/runic_details": "runic_pp.html"}
    hits, mode = [], {"no_block": False}

    def handle(route):
        from urllib.parse import urlparse
        path = urlparse(route.request.url).path
        hits.append(path)
        if path == "/manager/user_details" and mode["no_block"]:
            body = no_block
        else:
            body = (FIX / pages.get(path, "home.html")).read_text(encoding="utf-8")
        route.fulfill(status=200, content_type="text/html; charset=utf-8", body=body)

    page.route("https://manager.reg.ru/**", handle)
    try:
        page.goto("https://manager.reg.ru/")
        page.add_script_tag(content=JS)
        a = page.evaluate("async () => window.__doctoolManager.lookup('t1', 'testdomain-pp.ru', 'testdomain-pp.ru', {esia: false})")
        runic_a = hits.count("/user/1000/runic_details")
        mode["no_block"] = True
        page.add_script_tag(content=JS.replace("const VERSION = '", "const VERSION = 'x"))   # новый кэш аккаунтов
        b = page.evaluate("async () => window.__doctoolManager.lookup('t2', 'testdomain-pp.ru', 'testdomain-pp.ru', {esia: false})")
    finally:
        page.unroute("https://manager.reg.ru/**")
    o = a["data"]["account_owner"]
    assert o["user_id"] == "1000" and o["source"] == "user_details" and o["urls"]["runic"].endswith("/user/1000/runic_details")
    assert o["ba"]["ru_pp.person_r_surname"] == "Тестов" and o["ba"]["ru_pp.p_addr_addr"] == "Тестовая, д 1, кв 1"
    assert "ru_pp.sms_security_number" not in o["ba"] and "ru_pp.oid_esia" not in o["ba"] and "ru_pp.person" not in o["ba"]
    assert a["data"]["account_email"] == "owner.mail@example.org"
    assert a["data"]["sd"]["fields"]["p_addr_street"] == "Тестовая" and a["data"]["sd"]["fields"]["p_addr_flat"] == "579"
    assert runic_a == 0                                         # блок анкеты есть — форма не открывается
    ob = b["data"]["account_owner"]
    assert ob["source"] == "runic" and ob["type"] == "pp" and ob["ba"]["ru_pp.passport_number"]
    assert hits.count("/user/1000/runic_details") == 1
    # программа: ответ расширения → MO
    from doctool import domains
    d = domains.from_extension("testdomain-pp.ru", a).to_dict()
    assert d["mo_status"] in ("match", "mismatch", "warn") and d["mo_rows"], d["mo_ru"]
