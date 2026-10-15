"""Настройки рабочего места (0.7.6): config/workspace.yaml — строка таблицы и проверки доменов;
(0.8.0) строка «Поиск»: кнопки сервисов, быстрые ссылки и где открывается сайт.

Файл ищется как правила вердикта: свой в папке данных пользователя (config/workspace.yaml), иначе встроенный
(paths.workspace_config_file). Читается с кэшем по времени изменения файла. Если файла нет или он пустой,
действуют значения по умолчанию ниже (DEFAULT) — программа работает и без него.
"""
from __future__ import annotations

from pathlib import Path

import yaml

LEVELS = ("stop", "warn", "info", "off")
COLUMN4 = ("domain", "contract")
MANAGER_PAGES = ("domain_move", "loa_add")   # страницы manager, которые расширение заполняет (шаг в шапке дела)
OPEN_IN = ("auto", "split", "window", "tab")   # (0.8.0) где открывается сайт из строки «Поиск»
FILLERS = ("egrul",)                   # (0.8.0) формы поиска, в которые расширение вписывает запрос

# Значения по умолчанию — те же, что во встроенном config/workspace.yaml
DEFAULT = {
    "table": {
        "status_default": "готово",
        "receipt_methods": ["отрс", "отрс пр-ва", "офис", "почта", "курьер"],
        "operations": [
            {"title": "Смена администратора", "column4": "domain", "case_type": "admin_change_person",
             "manager_page": "domain_move"},
            {"title": "Корректировка данных домена", "column4": "domain"},
            {"title": "Смена владельца аккаунта", "column4": "contract"},
            {"title": "Восстановление доступа", "column4": "contract"},
            {"title": "Деактивация номера", "column4": "contract"},
            {"title": "Безбумажные операции", "column4": "domain", "manager_page": "loa_add"},
        ],
        "operators": {},
    },
    "domain_checks": {
        "admin_comment": {"level": "warn", "title": "В S есть комментарий администратора («К»)"},
        "recent_admin_change": {"level": "warn", "days": 30, "title": "Недавняя смена администратора"},
        "s_prohibited": {"level": "warn", "title": "В S стоят запреты",
                         "fields": ["changeProhibited", "clientUpdateProhibited", "serverUpdateProhibited",
                                    "clientHold", "serverHold"]},
        "frozen": {"level": "warn", "title": "Услуга заморожена (freeze_reason / freeze_date в S)"},
        "operation_blocks": {"level": "warn", "title": "В S заданы блокировки операций (operation_blocks)"},
        "transfer_lock": {"level": "info", "title": "Перенос к другому регистратору запрещён до даты (lock_transfer_till)"},
        "not_active": {"level": "warn", "title": "Услуга не активна (state в S — не A)"},
        "expires_soon": {"level": "info", "days": 30, "title": "Срок регистрации скоро заканчивается"},
        "trustee": {"level": "info", "title": "Подключена услуга Trustee"},
        "esia_none": {"level": "info", "title": "Администратор не проходил идентификацию через Госуслуги"},
        "bill_owner_differs": {"level": "info", "title": "Владелец счёта — другой аккаунт"},
    },
    # (0.8.0) строка «Поиск» рабочего места
    "search": {
        "open_in": "auto",
        "window_share": 40,
        "services": [
            {"id": "google", "title": "Google", "url": "https://www.google.com/search?q={q}"},
            {"id": "egrul", "title": "ЕГРЮЛ", "url": "https://egrul.nalog.ru/index.html", "fill": "egrul"},
        ],
        "links": [
            {"title": "Nextcloud", "url": "https://nextcloud-ms.runity.ru/index.php/apps/dashboard/"},
            {"title": "Проверка ЭП · Госуслуги", "url": "https://e-trust.gosuslugi.ru/check/sign#/portal/sig-check"},
            {"title": "Контур.Крипто", "url": "https://crypto.kontur.ru/decrypt"},
        ],
    },
}

_cache: dict = {}


def config_path() -> Path:
    from .paths import workspace_config_file
    return workspace_config_file()


def load(path: Path | None = None) -> dict:
    """Настройки: файл поверх значений по умолчанию (разделы table и domain_checks дополняются по ключам)."""
    path = Path(path) if path else config_path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = None
    key = str(path)
    hit = _cache.get(key)
    if hit and hit[0] == mtime:
        return hit[1]
    data = {}
    if mtime is not None:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cfg = {"table": {**DEFAULT["table"], **(data.get("table") or {})},
           "domain_checks": {**DEFAULT["domain_checks"]},
           "search": {**DEFAULT["search"], **(data.get("search") or {})}}
    for k, v in (data.get("domain_checks") or {}).items():
        cfg["domain_checks"][k] = {**DEFAULT["domain_checks"].get(k, {}), **(v or {})}
    cfg["table"]["operators"] = {str(k).lower(): str(v) for k, v in (cfg["table"].get("operators") or {}).items()}
    cfg["search"] = _search_clean(cfg["search"])
    _cache[key] = (mtime, cfg)
    return cfg


def _url_ok(u) -> bool:
    return isinstance(u, str) and u.startswith(("https://", "http://"))


def _search_clean(sec: dict) -> dict:
    """Раздел search без ошибочных записей (ошибки покажет validate): сервисы с id/title/url, ссылки с title/url."""
    out = dict(sec)
    if out.get("open_in") not in OPEN_IN:
        out["open_in"] = "auto"
    try:
        share = int(out.get("window_share") or 40)
    except (TypeError, ValueError):
        share = 40
    out["window_share"] = min(70, max(20, share))
    svcs = []
    for i, s in enumerate(out.get("services") or []):
        if isinstance(s, dict) and s.get("title") and _url_ok(s.get("url")):
            fill = s.get("fill") if s.get("fill") in FILLERS else None
            svcs.append({"id": str(s.get("id") or f"s{i + 1}"), "title": str(s["title"]), "url": s["url"], "fill": fill})
    out["services"] = svcs
    out["links"] = [{"title": str(x["title"]), "url": x["url"]} for x in (out.get("links") or [])
                    if isinstance(x, dict) and x.get("title") and _url_ok(x.get("url"))]
    return out


def validate(path: Path | None = None) -> list[str]:
    """Ошибки в config/workspace.yaml (для python -m doctool validate)."""
    path = Path(path) if path else config_path()
    errors: list[str] = []
    if not path.is_file():
        return errors
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        return [f"{path.name}: ошибка YAML: {e}"]
    table = data.get("table") or {}
    ops = table.get("operations") or []
    if not isinstance(ops, list):
        errors.append(f"{path.name}: table.operations — должен быть список")
        ops = []
    from .verdict import load_case_types
    try:
        types = load_case_types()
    except Exception:  # noqa: BLE001 — ошибки case_types.yaml покажет основная проверка
        types = {}
    for i, op in enumerate(ops, 1):
        if not isinstance(op, dict) or not op.get("title"):
            errors.append(f"{path.name}: table.operations[{i}] — нет title")
            continue
        if op.get("column4", "domain") not in COLUMN4:
            errors.append(f"{path.name}: операция «{op['title']}»: column4 — domain или contract, а не «{op.get('column4')}»")
        if op.get("manager_page") and op["manager_page"] not in MANAGER_PAGES:
            errors.append(f"{path.name}: операция «{op['title']}»: manager_page — {' / '.join(MANAGER_PAGES)} или не указывать, "
                          f"а не «{op['manager_page']}»")
        if op.get("case_type") and types and op["case_type"] not in types:
            errors.append(f"{path.name}: операция «{op['title']}»: нет типа заявления «{op['case_type']}» в case_types.yaml")
    for k, v in (data.get("domain_checks") or {}).items():
        if k not in DEFAULT["domain_checks"]:
            errors.append(f"{path.name}: неизвестная проверка домена «{k}» (есть: {', '.join(DEFAULT['domain_checks'])})")
            continue
        lvl = (v or {}).get("level", DEFAULT["domain_checks"][k]["level"])
        if lvl not in LEVELS:
            errors.append(f"{path.name}: проверка «{k}»: level — {' / '.join(LEVELS)}, а не «{lvl}»")
        days = (v or {}).get("days")
        if days is not None and (not isinstance(days, int) or days < 0):
            errors.append(f"{path.name}: проверка «{k}»: days — целое число дней")
    sec = data.get("search") or {}
    if not isinstance(sec, dict):
        errors.append(f"{path.name}: search — должен быть разделом (ключ: значение)")
        sec = {}
    if "open_in" in sec and sec["open_in"] not in OPEN_IN:
        errors.append(f"{path.name}: search.open_in — {' / '.join(OPEN_IN)}, а не «{sec['open_in']}»")
    share = sec.get("window_share")
    if share is not None and (not isinstance(share, int) or not 20 <= share <= 70):
        errors.append(f"{path.name}: search.window_share — целое число от 20 до 70 (процент ширины экрана)")
    for key, what in (("services", "сервис"), ("links", "ссылка")):
        items = sec.get(key)
        if items is None:
            continue
        if not isinstance(items, list):
            errors.append(f"{path.name}: search.{key} — должен быть список")
            continue
        for i, x in enumerate(items, 1):
            if not isinstance(x, dict) or not x.get("title"):
                errors.append(f"{path.name}: search.{key}[{i}] — нет title")
                continue
            if not _url_ok(x.get("url")):
                errors.append(f"{path.name}: {what} «{x['title']}»: url должен начинаться с https:// или http://")
            if key == "services" and x.get("fill") and x["fill"] not in FILLERS:
                errors.append(f"{path.name}: сервис «{x['title']}»: fill — {' / '.join(FILLERS)} или не указывать, "
                              f"а не «{x['fill']}»")
    return errors
