"""Модули заявлений (forms/): загрузка с кэшем, роли полей, подписи бланка, хуки бланка, проверка конфигов.

Модуль заявления (с 0.7.3) — папка forms/<id>/:
  form.yaml   описание бланка: поля (type, role), подписи бланка для чтения эталона (reference:), регулярки (text:),
              области на скане (scan:), detect, labels_re, strip_prefixes, rows;
  hooks.py    (необязательно) особая логика бланка — функции с известными именами (HOOKS ниже), например раздел
              «новому Администратору» у REG.RU. Ядро вызывает их через call_hook(); без hooks.py ничего не вызывается;
  samples/    (необязательно) образцы заявлений (DOCX, PDF с текстом) и рядом <имя>.expected.json — ожидаемые
              значения полей. tests/test_form_samples.py проверяет образцы всех модулей сам.
Описание в одном файле forms/<id>.yaml (как до 0.7.3) тоже читается. Если для одного id есть и папка, и файл,
берётся папка (файл — устаревшая копия; validate напомнит его удалить).

Роль поля — его смысл для проверок: «ФИО в тексте», «домены», «новый администратор» (словарь ROLES ниже).
Проверки (compare.py, domains.py), таблица интерфейса, выгрузка и отчёт обращаются к полю заявления
по роли, а не по имени поля в YAML. Поэтому новый бланк может называть поля как угодно: роль задаётся
ключом `role:` у поля (без него роль = имя поля). Для бланка regru_transfer_person имена полей и роли
совпадают.

Модуль без тяжёлых зависимостей: его используют и разбор заявлений, и проверки, и интерфейсы.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml

FORMS_DIR = Path(__file__).resolve().parent.parent / "forms"

# Роли полей заявления, на которые опираются проверки, таблица и выгрузка. Новый бланк сопоставляет свои
# поля с этими ролями; новая роль добавляется сюда вместе с проверкой, которая её использует.
ROLES = {
    "applicant_header": "ФИО заявителя в шапке «от …» и дата рождения (подполя birth_date, raw_fio)",
    "applicant_fio": "ФИО заявителя в тексте «Я, …»",
    "signature_fio": "ФИО у подписи",
    "inn": "ИНН заявителя-ИП",
    "passport": "серия и номер паспорта в заявлении (подполе issue_date)",
    "issued_by": "кем выдан паспорт",
    "address": "адрес регистрации",
    "domains": "домены (подполя list, punycode, joined, fragments, mixed, stated_count, uncertain)",
    "services": "дополнительные услуги",
    "new_admin_inline": "новый администратор в тексте («новому Администратору: …»)",
    "new_admin_org": "новый администратор — юрлицо",
    "new_admin_org_contact": "контакты нового администратора-юрлица",
    "new_admin_fio": "новый администратор — физлицо / ИП",
    "new_admin_contact": "контакты нового администратора",
    "contract": "договор / аккаунт нового администратора",
    "application_date": "дата заявления",
}

_cache: dict[str, tuple] = {}
MODULE_FILE = "form.yaml"


def _form_files(d: Path) -> list[Path]:
    """Описания бланков: forms/<id>/form.yaml (модули) и forms/<id>.yaml (одним файлом, как до 0.7.3)."""
    return sorted(d.glob(f"*/{MODULE_FILE}")) + sorted(d.glob("*.yaml"))


def forms_dirs() -> list[Path]:
    """Папки модулей заявлений: свои из папки данных пользователя (если есть, 0.7.5), затем встроенные forms/."""
    from . import paths
    return paths.forms_dirs(FORMS_DIR)


def load_forms(forms_dir: Path | str | None = None) -> list[dict]:
    """Все бланки из папки (по умолчанию — из forms_dirs(): свой модуль с тем же id заменяет встроенный).
    Перечитываются, только если файлы изменились. Результат не изменять.
    У бланка-модуля есть ключ `_module` — путь к его папке (hooks.py, samples/)."""
    if forms_dir is not None:
        return _load_dir(Path(forms_dir))[1]
    dirs = forms_dirs()
    if len(dirs) == 1:
        return _load_dir(dirs[0])[1]
    merged, seen = [], set()
    for d in dirs:
        for f in _load_dir(d)[1]:
            if f.get("id") not in seen:
                seen.add(f.get("id"))
                merged.append(f)
    return merged


def _load_dir(d: Path) -> tuple:
    files = _form_files(d)
    key = tuple((str(p.relative_to(d)), p.stat().st_mtime_ns, p.stat().st_size) for p in files)
    hit = _cache.get(str(d))
    if hit is None or hit[0] != key:
        forms, seen, dups = [], set(), []
        for p in files:
            f = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            if p.name == MODULE_FILE:
                f["_module"] = str(p.parent)
            elif f.get("id") in seen:          # папка-модуль с тем же id уже есть: файл — устаревшая копия
                dups.append(str(p.relative_to(d)))
                continue
            seen.add(f.get("id"))
            forms.append(f)
        hit = (key, forms, dups)
        _cache[str(d)] = hit
    return hit


def stale_files(forms_dir: Path | str | None = None) -> list[str]:
    """forms/<id>.yaml, у которых есть папка-модуль с тем же id (файлы не читаются). Для своих модулей из папки
    данных пользователя — с полным путём."""
    if forms_dir is not None:
        return list(_load_dir(Path(forms_dir))[2])
    out = []
    for d in forms_dirs():
        out += [n if d == FORMS_DIR else str(d / n) for n in _load_dir(d)[2]]
    return out


def module_dir(form: dict | None) -> Path | None:
    """Папка модуля заявления (forms/<id>/) или None, если бланк описан одним файлом."""
    m = (form or {}).get("_module")
    return Path(m) if m else None


# ------------------------------------------------------------------ хуки бланка (forms/<id>/hooks.py)

# Точки, в которых ядро вызывает особую логику бланка. Хук — функция с этим именем в hooks.py модуля.
HOOKS = {
    "after_text": "after_text(ex, text, form, conf, source) -> bool — после чтения полей из текста (текстовый слой "
                  "или OCR страницы); True — хук что-то нашёл",
    "scan_page": "scan_page(page, lines, page_text, form, prefer) — скан: после чтения полей из текста страницы "
                 "(можно перечитать часть страницы)",
}
_hooks_cache: dict[str, tuple] = {}


def hooks(form: dict | None):
    """Модуль hooks.py бланка (загружается по пути, перечитывается при изменении файла) или None."""
    d = module_dir(form)
    p = d / "hooks.py" if d else None
    if p is None or not p.exists():
        return None
    mtime = p.stat().st_mtime_ns
    hit = _hooks_cache.get(str(p))
    if hit is None or hit[0] != mtime:
        import importlib.util
        name = f"doctool_form_{re.sub(r'[^0-9A-Za-z_]', '_', form.get('id') or d.name)}"
        spec = importlib.util.spec_from_file_location(name, p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        hit = (mtime, mod)
        _hooks_cache[str(p)] = hit
    return hit[1]


SAMPLE_EXT = (".docx", ".pdf")


def samples(form: dict | None) -> list[tuple[Path, Path]]:
    """Образцы модуля: [(файл образца, <имя>.expected.json)] из forms/<id>/samples/."""
    d = module_dir(form)
    if d is None or not (d / "samples").is_dir():
        return []
    out = []
    for e in sorted((d / "samples").glob("*.expected.json")):
        stem = e.name[:-len(".expected.json")]
        src = next((e.with_name(stem + x) for x in SAMPLE_EXT if e.with_name(stem + x).exists()), None)
        out.append((src, e))
    return out


def call_hook(form: dict | None, name: str, *args, default=None, **kw):
    """Вызвать хук бланка, если он есть; иначе вернуть default."""
    if name not in HOOKS:
        raise KeyError(f"неизвестный хук {name!r}")
    fn = getattr(hooks(form), name, None) if form else None
    return fn(*args, **kw) if callable(fn) else default


def form_by_id(form_id: str | None, forms_dir: Path | str | None = None) -> dict | None:
    if not form_id:
        return None
    return next((f for f in load_forms(forms_dir) if f.get("id") == form_id), None)


# ------------------------------------------------------------------ роли

def role_map(form: dict | None) -> dict[str, str]:
    """{роль: имя поля в бланке}."""
    if not form:
        return {}
    return {(spec or {}).get("role", key): key for key, spec in form.get("fields", {}).items()}


class Fields:
    """Поля заявления по ролям: Fields(app).get("applicant_header.birth_date") — значение подполя birth_date
    поля с ролью applicant_header, как бы это поле ни называлось в бланке."""

    def __init__(self, ex, form: dict | None = None):
        self.ex = ex
        if form is None and ex is not None:
            form = form_by_id(getattr(ex, "doc_type", None))
        self.form = form
        self.map = role_map(form)

    def key(self, role: str) -> str:
        base, dot, sub = role.partition(".")
        return self.map.get(base, base) + dot + sub

    def get(self, role: str, default=None):
        return self.ex.get(self.key(role), default) if self.ex is not None else default

    def field(self, role: str):
        return self.ex.fields.get(self.key(role)) if self.ex is not None else None


# ------------------------------------------------------------------ подписи бланка и префиксы значений

# До 0.4.0 подписи и префиксы были зашиты в application.py. Бланк без ключа labels_re / strip_prefixes
# (например, свой YAML от 0.3.x) получает прежний список; пустой список в YAML — «ничего не вырезать».
LEGACY_LABELS = ["ФИО", "кем выдан", "адрес регистрации", "серия, номер паспорта, когда (?:и кем )?выдан",
                 r"ИНН ИП \(при наличии статуса ИП\)", r"наименование домена\(ов\)?", "Primary, Secondary DNS",
                 r"номер договора с компанией РЕГ\.РУ", "фамилия, имя, отчество", "подпись", "дата прописью"]
LEGACY_PREFIXES = [r"от\b", "я,", r"профиля\)\s*:"]


def _form_list(form: dict, key: str) -> list[str]:
    v = form.get(key)
    if v is None:
        return {"labels_re": LEGACY_LABELS, "strip_prefixes": LEGACY_PREFIXES}[key]
    return list(v)


def _all_forms_list(key: str) -> list[str]:
    out: list[str] = []
    for f in load_forms() or [{}]:
        for x in _form_list(f, key):
            if x not in out:
                out.append(x)
    return out


@lru_cache(maxsize=64)
def _labels_rx(patterns: tuple[str, ...]) -> re.Pattern | None:
    if not patterns:
        return None
    # скобка — любая: OCR читает «(» как «{» или «[» («{кем выдан)»)
    return re.compile(r"[({\[]\s*(?:" + "|".join(patterns) + r")[^)}\]]*[)}\]]?", re.I)


def labels_regex(form: dict | None = None) -> re.Pattern | None:
    """Печатные подписи бланка в скобках («(кем выдан)», «(ФИО, дата рождения)»), которые вычищаются из
    значений. Берутся из `labels_re` бланка; без бланка — из всех бланков."""
    pats = _form_list(form, "labels_re") if form else _all_forms_list("labels_re")
    return _labels_rx(tuple(pats or ()))


@lru_cache(maxsize=64)
def _prefix_rx(patterns: tuple[str, ...]) -> re.Pattern | None:
    if not patterns:
        return None
    return re.compile(r"^(" + "|".join(patterns) + r")\s*", re.I)


def prefix_regex(form: dict | None = None) -> re.Pattern | None:
    """Печатные слова перед значением на скане («от», «Я,», «профиля):») — `strip_prefixes` бланка."""
    pats = _form_list(form, "strip_prefixes") if form else _all_forms_list("strip_prefixes")
    return _prefix_rx(tuple(pats or ()))


def extra_rows(form: dict | None) -> list[dict]:
    """Дополнительные строки таблицы интерфейса из бланка (`rows:`)."""
    return list((form or {}).get("rows") or [])


def field_title(form: dict | None, key: str) -> str | None:
    spec = (form or {}).get("fields", {}).get(key)
    return spec.get("title") if spec else None


# ------------------------------------------------------------------ проверка конфигов

def _validate_module(f: dict, fid: str) -> list[str]:
    """Модуль заявления: раздел reference:, hooks.py, образцы; подписи reference: — по пустому бланку blank.docx."""
    import json
    out: list[str] = []
    fields = f.get("fields") or {}
    ref = f.get("reference")
    if ref is not None:
        where = f"бланк {fid}, reference"
        mf = ref.get("min_found", 0.9) if isinstance(ref, dict) else None
        if not isinstance(ref, dict) or not isinstance(ref.get("fields"), dict) or not ref["fields"]:
            out.append(f"{where}: нужен словарь fields: {{поле: {{before: …, after: …}}}}")
        else:
            if not isinstance(mf, (int, float)) or not 0 < mf <= 1:
                out.append(f"{where}: min_found — доля от 0 до 1")
            for key, spec in ref["fields"].items():
                if key not in fields:
                    out.append(f"{where}: поля {key!r} нет в fields бланка")
                if not isinstance(spec, dict) or not isinstance(spec.get("before"), str) or not spec["before"].strip():
                    out.append(f"{where}, поле {key}: нужна подпись before (строка)")
                    continue
                a = spec.get("after")
                if a is not None and not (isinstance(a, str) or (isinstance(a, list) and all(isinstance(x, str) for x in a))):
                    out.append(f"{where}, поле {key}: after — строка или список строк")
    d = module_dir(f)
    if d is None:
        return out
    if (d / "hooks.py").exists():
        try:
            mod = hooks(f)
            if not any(callable(getattr(mod, h, None)) for h in HOOKS):
                out.append(f"бланк {fid}: в hooks.py нет ни одного хука ({', '.join(HOOKS)})")
        except Exception as e:  # noqa: BLE001
            out.append(f"бланк {fid}: hooks.py не загружается: {type(e).__name__}: {e}")
    for src, exp in samples(f):
        where = f"бланк {fid}, образец {exp.name}"
        if src is None:
            out.append(f"{where}: нет файла образца ({' / '.join(SAMPLE_EXT)} с тем же именем)")
        try:
            data = json.loads(exp.read_text(encoding="utf-8"))
        except ValueError as e:
            out.append(f"{where}: не JSON: {e}")
            continue
        for key in (data.get("fields") or {}):
            base = key.split(".")[0]
            if base not in fields and base not in ROLES:
                out.append(f"{where}: поле {key!r} не найдено в бланке")
    blank = d / "blank.docx"
    if blank.exists() and isinstance(ref, dict) and isinstance(ref.get("fields"), dict):
        from . import reference as reflib
        from .ocr import docx_text
        rd = reflib.read(docx_text(blank), f)
        if rd.missing:
            out.append(f"бланк {fid}: в пустом бланке blank.docx не нашлись по порядку подписи reference: "
                       + "; ".join(f"«{m}»" for m in rd.missing[:8]))
    return out


STATUSES = {"ok", "warn", "fail", "review", "info"}
REGION_KEYS = {"dy0", "dy1", "x0", "x1"}


def validate_config(forms_dir: Path | str | None = None, case_types: dict | None = None) -> list[str]:
    """Проблемы в модулях заявлений forms/ и config/case_types.yaml: неизвестные типы полей, роли, проверки, бланки,
    флаги, ошибки в регулярных выражениях. Пустой список — всё в порядке.
    Запуск: python -m doctool validate; в тестах — tests/test_config.py."""
    from . import checks as checklib
    from .application import FIELD_PARSERS
    from .verdict import load_case_types
    from .service import BASE_ROWS

    problems: list[str] = []
    forms = load_forms(forms_dir)
    ids = [f.get("id") for f in forms]
    for i in {x for x in ids if ids.count(x) > 1}:
        problems.append(f"бланк {i}: id встречается несколько раз")
    row_ids = {r["id"] for r in BASE_ROWS}

    def rx(where: str, pat: str, need_group: bool = False):
        try:
            c = re.compile(pat)
        except re.error as e:
            problems.append(f"{where}: ошибка в регулярном выражении {pat!r}: {e}")
            return
        if need_group and c.groups < 1:
            problems.append(f"{where}: в выражении {pat!r} нет группы (значение — группа 1)")

    for f in forms:
        fid = f.get("id", "?")
        if not (f.get("detect") or {}).get("keywords"):
            problems.append(f"бланк {fid}: нет detect.keywords — бланк не будет узнаваться")
        roles_seen: dict[str, str] = {}
        for key, spec in (f.get("fields") or {}).items():
            spec = spec or {}
            where = f"бланк {fid}, поле {key}"
            if spec.get("type", "text") not in FIELD_PARSERS:
                problems.append(f"{where}: неизвестный type {spec.get('type')!r} (есть: {', '.join(FIELD_PARSERS)})")
            role = spec.get("role", key)
            if "role" in spec and role not in ROLES:
                problems.append(f"{where}: неизвестная роль {role!r} (есть: {', '.join(ROLES)})")
            if role in roles_seen:
                problems.append(f"{where}: роль {role!r} уже у поля {roles_seen[role]}")
            roles_seen[role] = key
            for pat in spec.get("text") or []:
                rx(where, pat, need_group=True)
            # (0.7.0) нейросеть: llm — описание поля (строка) или false; digits — допустимое число цифр
            if "llm" in spec and not (isinstance(spec["llm"], str) and spec["llm"].strip() or spec["llm"] is False):
                problems.append(f"{where}: llm — описание поля строкой или false")
            dg = spec.get("digits")
            if dg is not None and not all(isinstance(x, int) and x > 0 for x in (dg if isinstance(dg, list) else [dg])):
                problems.append(f"{where}: digits — число или список чисел (сколько цифр в значении)")
            if not spec.get("text") and spec.get("llm") is False and not spec.get("scan"):
                problems.append(f"{where}: поле нечем читать — нет text:, scan:, а llm: false")
            sc = spec.get("scan")
            if sc:
                if not sc.get("anchor"):
                    problems.append(f"{where}: scan без anchor")
                miss = REGION_KEYS - set((sc.get("region") or {}))
                if miss:
                    problems.append(f"{where}: в scan.region нет {', '.join(sorted(miss))}")
                if sc.get("block_start") and spec.get("type") != "domains":
                    problems.append(f"{where}: scan.block_start поддерживается только у type: domains")
        problems += _validate_module(f, fid)
        for pat in f.get("labels_re") or []:
            rx(f"бланк {fid}, labels_re", pat)
        for pat in f.get("strip_prefixes") or []:
            rx(f"бланк {fid}, strip_prefixes", pat)
        for r in f.get("rows") or []:
            where = f"бланк {fid}, строка таблицы {r.get('id')}"
            if not r.get("id") or not r.get("title"):
                problems.append(f"{where}: нужны id и title")
            if r.get("id") in row_ids:
                problems.append(f"{where}: id совпадает со стандартной строкой")
            for role in r.get("app") or []:
                if role.split(".")[0] not in ROLES and role.split(".")[0] not in (f.get("fields") or {}):
                    problems.append(f"{where}: неизвестная роль/поле {role!r}")
            row_ids.add(r.get("id"))

    for name, rows in checklib.all_names().items():
        for r in rows:
            if r and r not in row_ids:
                problems.append(f"проверка «{name}»: строка таблицы {r!r} не существует")

    types = case_types if case_types is not None else load_case_types()
    names = checklib.all_names()
    flags = checklib.all_flags()
    for tid, ct in types.items():
        where = f"тип заявления {tid}"
        form = None
        if ct.get("form"):
            form = next((f for f in forms if f.get("id") == ct["form"]), None)
            if form is None:
                problems.append(f"{where}: бланк {ct['form']!r} не найден в forms/")
        for cid in ct.get("checks") or []:
            if cid not in checklib.REGISTRY:
                problems.append(f"{where}: неизвестная проверка {cid!r} (есть: {', '.join(checklib.REGISTRY)})")
        if ct.get("enabled", True) and not ct.get("form") and not ct.get("checks"):
            problems.append(f"{where}: тип без бланка должен перечислить проверки (checks:)")
        produced = {n for cid in checklib.ids_for(ct) if cid in checklib.REGISTRY for n in checklib.REGISTRY[cid].names}
        for kind in ("reject", "review"):
            for rule in ((ct.get("rules") or {}).get(kind) or []):
                cond = rule.get("when") or {}
                if "check" in cond:
                    if cond["check"] not in names:
                        problems.append(f"{where}, правило «{rule.get('reason')}»: проверки «{cond['check']}» нет")
                    elif cond["check"] not in produced:
                        problems.append(f"{where}, правило «{rule.get('reason')}»: проверка «{cond['check']}» "
                                        "не включена в checks этого типа — правило не сработает")
                for st in (cond.get("status") or []) + (cond.get("any_status") or []):
                    if st not in STATUSES:
                        problems.append(f"{where}, правило «{rule.get('reason')}»: неизвестный статус {st!r}")
                if "flag" in cond and cond["flag"] not in flags:
                    problems.append(f"{where}, правило «{rule.get('reason')}»: флаг {cond['flag']!r} никто не ставит "
                                    f"(есть: {', '.join(sorted(flags))})")
                if "missing" in cond:
                    keys = set((form or {}).get("fields", {})) | set(ROLES)
                    if cond["missing"].split(".")[0] not in keys:
                        problems.append(f"{where}, правило «{rule.get('reason')}»: поле {cond['missing']!r} не найдено")
    return problems
