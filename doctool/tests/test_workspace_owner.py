"""(0.8.5) Операция «Смена владельца ЛК» (бывшая «Смена владельца аккаунта»), окно «Смена владельца ЛК» рабочего места,
тема оформления, окна без прилипания - то, что видно без браузера."""
import json
from pathlib import Path

from doctool import workspace, wsconfig

STATIC = Path(__file__).resolve().parent.parent / "doctool" / "static"


def test_owner_operation_renamed(tmp_path):
    for cfg in (wsconfig.load(), wsconfig.load(Path("/nonexistent/workspace.yaml"))):   # встроенный файл и DEFAULT
        titles = [o["title"] for o in cfg["table"]["operations"]]
        assert "Смена владельца аккаунта" not in titles
        op = next(o for o in cfg["table"]["operations"] if o["title"] == "Смена владельца ЛК")
        assert op["column4"] == "contract" and op["manager_page"] == "runic"
    assert wsconfig.validate() == []
    assert wsconfig.op_title("Смена владельца аккаунта") == "Смена владельца ЛК"
    assert wsconfig.op_title("Смена администратора") == "Смена администратора"
    # свой workspace.yaml со старым названием - остаётся как есть
    own = tmp_path / "workspace.yaml"
    own.write_text("table:\n  operations:\n    - {title: Смена владельца аккаунта, column4: contract}\n", encoding="utf-8")
    assert wsconfig.op_title("Смена владельца аккаунта", wsconfig.load(own)) == "Смена владельца аккаунта"


def test_old_case_gets_new_operation_title(tmp_path):
    folder = tmp_path / "20261029_120000_abcdef1234"
    (folder / "files").mkdir(parents=True)
    ws = workspace.Workspace("abcdef1234", folder)
    ws.state["operation"] = "Смена владельца аккаунта"
    ws.save()
    loaded = workspace.Workspace.load(folder)
    assert loaded.state["operation"] == "Смена владельца ЛК"
    assert loaded.operation()["column4"] == "contract"
    loaded.set_state({"operation": "Смена владельца аккаунта"})
    assert json.loads((folder / "ws.json").read_text(encoding="utf-8"))["state"]["operation"] == "Смена владельца ЛК"


def test_page_has_owner_window_theme_and_no_snap():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "workspace.js").read_text(encoding="utf-8")
    assert 'id="wsOpenOwn"' in html and 'id="ownBox"' in html and "function ownHome" in html
    assert "function openOwn" in js and "{id: 'own', t: 'Смена владельца ЛК'" in js
    assert 'id="themeSel"' in html and "pysm.theme" in html and ":root[data-theme=dark]" in html
    assert "wsSnapOn" not in html + js and "magnet(" not in js and "snapZone" not in js and "ws-snap" not in html
    assert "#esiaDlg" in html and "dl.close()" in html       # ESIA и MO закрываются щелчком по фону


def test_rules_accept_old_dash_in_check_names():
    """(0.8.5) Названия проверок - с дефисом; правило из своего case_types.yaml с длинным тире срабатывает."""
    from doctool.compare import Check
    from doctool.verdict import decide
    ct = {"rules": {"reject": [], "review": [{"when": {"check": "Заявитель \u2014 администратор доменов (Sd)", "status": ["review"]},
                                              "reason": "проверить"}]}}
    d = decide(ct, [Check("Заявитель - администратор доменов (Sd)", "review", "", "", "")], set(), {})
    assert d.code == "review" and d.reasons == ["проверить"]
