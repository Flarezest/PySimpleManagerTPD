"""Настройки рабочего места (0.7.6): config/workspace.yaml — строка таблицы и проверки доменов.

Файл ищется как правила вердикта: свой в папке данных пользователя (config/workspace.yaml), иначе встроенный
(paths.workspace_config_file). Читается с кэшем по времени изменения файла. Если файла нет или он пустой,
действуют значения по умолчанию ниже (DEFAULT) — программа работает и без него.
"""
from __future__ import annotations

from pathlib import Path

import yaml

LEVELS = ("stop", "warn", "info", "off")
COLUMN4 = ("domain", "contract")
MANAGER_PAGES = ("domain_move",)       # страницы manager, которые расширение умеет заполнять для операции

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
           "domain_checks": {**DEFAULT["domain_checks"]}}
    for k, v in (data.get("domain_checks") or {}).items():
        cfg["domain_checks"][k] = {**DEFAULT["domain_checks"].get(k, {}), **(v or {})}
    cfg["table"]["operators"] = {str(k).lower(): str(v) for k, v in (cfg["table"].get("operators") or {}).items()}
    _cache[key] = (mtime, cfg)
    return cfg


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
    return errors
