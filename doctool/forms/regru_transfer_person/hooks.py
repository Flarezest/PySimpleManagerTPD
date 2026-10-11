"""Модуль заявления REG.RU «передача права администрирования домена (физлицо)»: особая логика бланка.

Раздел «новому Администратору» (с 0.5.0; до 0.7.3 был в ядре, application.py). Таблица нового администратора на
скане часто теряет подписи («Для физ.лиц и ИП:», «ФИО»), а в свободной форме её нет вовсе — поэтому раздел разбирается
целиком: организация (строка с ООО/АО/…), ФИО (2–4 слова, есть словарное имя или отчество), e-mail и телефоны.
Границы раздела — ключ `new_admin_block` в form.yaml.

Хуки (formspec.HOOKS):
  after_text — после чтения полей из текста: неправдоподобные значения полей нового администратора убираются,
               пустые заполняются из раздела;
  scan_page  — скан: если раздел не дал ни организации, ни ФИО, полоса раздела перечитывается отдельно (psm 6);
               в режиме нейросети её читает модель (только поля нового администратора).
"""
from __future__ import annotations

import re

import cv2

from doctool import llm_extract, names, parsers
from doctool.application import ORG_RE, _find_line, _store, _usable
from doctool.models import Extraction
from doctool.ocr import Line, Page, remove_lines_soft, tesseract_lines
from doctool.progress import log


# ------------------------------------------------------------------ хуки

def after_text(ex: Extraction, text: str, form: dict, conf: float, source: str) -> bool:
    return apply_new_admin_block(ex, text, form, conf, source)


def scan_page(page: Page, lines: list[Line], page_text: str, form: dict, prefer: Extraction) -> None:
    if prefer.debug.get("mode") == "llm":
        _llm_new_admin(page, lines, form, prefer)
    elif not apply_new_admin_block(prefer, page_text, form, 0.85, "ocr-text"):
        _reread_new_admin(page, lines, form, prefer)


# ------------------------------------------------------------------ раздел «новому Администратору»

def _block_text(text: str, cfg: dict) -> str | None:
    """Текст раздела от `start` до первого из `end` (не дальше max_lines строк)."""
    m = None
    for pat in cfg.get("start") or []:
        m = re.search(pat, text)
        if m:
            break
    if m is None:
        return None
    rest = text[m.end():]
    ends = [e.start() for pat in cfg.get("end") or [] for e in [re.search(pat, rest)] if e]
    if ends:
        rest = rest[:min(ends)]
    return "\n".join(rest.splitlines()[:int(cfg.get("max_lines", 14))])


_CUT_ORG = re.compile(r"[,.;]?\s*(?:\bИНН\b|\bОГРН|\bКПП\b|юридический\s+адрес|\bАдрес\b).*$", re.I)
_CELL_LABELS = re.compile(r"(?i)\bНаименование(?:\s+юр\.?\s*лица)?|\bюр\.?\s*лица|\bФИО\b|[|]")


def _person_line(line: str) -> str | None:
    """Строка похожа на ФИО (2–4 слова с заглавной, хотя бы одно — словарное имя или отчество)?"""
    if "@" in line or ORG_RE.search(line):
        return None
    toks = names.extract_fio(_CELL_LABELS.sub(" ", line))
    if len(toks) < 2:
        return None
    if not any(names.is_patronymic(t) or names.is_known(t, "Name") for t in toks):
        return None
    return line


def parse_new_admin_block(block: str) -> dict[str, str]:
    """Раздел «новому Администратору» целиком → {org, person, org_contact, contact} (сырые строки).
    Работает и для таблицы бланка, у которой OCR потерял подписи («Для физ.лиц и ИП:», «ФИО»), и для
    заявлений в свободной форме («новому Администратору: ООО “Ветстем”, ОГРН …, +7 (495) …»)."""
    lines = [l.strip() for l in block.splitlines() if l.strip()]
    org_i = person_i = None
    out: dict[str, str] = {}
    for i, l in enumerate(lines):
        if org_i is None and ORG_RE.search(l):
            v = _CUT_ORG.sub("", _CELL_LABELS.sub(" ", l)).strip(" ,.;:")
            if len(v) >= 4:
                org_i, out["org"] = i, re.sub(r"\s+", " ", v)
                continue
        if person_i is None and _person_line(l):
            v = _CUT_ORG.sub("", _CELL_LABELS.sub(" ", l)).strip(" ,.;:")
            person_i, out["person"] = i, re.sub(r"\s+", " ", v)
    for i, l in enumerate(lines):
        if not (parsers.EMAIL_RE.search(l) or parsers.PHONE_RE.search(l)):
            continue
        key = "contact" if (person_i is not None and i > person_i) or org_i is None else "org_contact"
        if person_i is not None and org_i is not None and person_i < org_i:
            key = "org_contact" if i > org_i else "contact"
        v = re.sub(r"(?i)^[^@\d+]*?(?=[\w.+\-]+@|\+?\d)", "", l, count=1)   # подпись строки («e-mail/тел.»)
        out[key] = (out[key] + "; " if key in out else "") + v.strip(" |")
    return out


_BLOCK_FIELDS = {"org": "new_admin_org", "person": "new_admin_fio", "org_contact": "new_admin_org_contact",
                 "contact": "new_admin_contact"}


def apply_new_admin_block(ex: Extraction, text: str, form: dict, conf: float, source: str) -> bool:
    """Разбор раздела «новому Администратору» (ключ `new_admin_block` бланка): неправдоподобные значения
    полей нового администратора убираются, пустые заполняются из раздела. True — раздел что-то дал."""
    cfg = form.get("new_admin_block")
    if not cfg:
        return False
    block = _block_text(text, cfg)
    found = parse_new_admin_block(block) if block else {}
    roles = cfg.get("fields") or _BLOCK_FIELDS
    for part, key in roles.items():
        if key not in form["fields"]:
            continue
        cur = ex.fields.get(key)
        if cur is not None and cur.source == "manual":
            continue
        if cur is not None and not _usable(key, cur.value):
            for k in [k for k in ex.fields if k == key or k.startswith(key + ".")]:
                del ex.fields[k]
            cur = None
        if part in found and (cur is None or not cur.value):
            for k in [k for k in ex.fields if k == key or k.startswith(key + ".")]:
                del ex.fields[k]
            _store(ex, key, form["fields"][key]["type"], found[part], conf, source)
    # контакты «юрлица», когда юрлица нет, — это контакты нового администратора-физлица
    org_c, org, fio_c = (ex.fields.get(roles.get(k, "")) for k in ("org_contact", "org", "contact"))
    if org_c is not None and org_c.source != "manual" and not (org is not None and org.value):
        okey, ckey = roles.get("org_contact"), roles.get("contact")
        if ckey in form["fields"] and not (fio_c is not None and fio_c.value):
            _store(ex, ckey, form["fields"][ckey]["type"], org_c.raw or str(org_c.value), org_c.confidence,
                   org_c.source)
        for k in [k for k in ex.fields if k == okey or k.startswith(okey + ".")]:
            del ex.fields[k]
    return bool(set(found) & {"org", "person"})



# ------------------------------------------------------------------ скан: полоса раздела

def _new_admin_band_text(page: Page, lines: list[Line]) -> str | None:
    """Полоса от «новому Администратору» до «Номер договора», распознанная отдельно как блок (psm 6)."""
    start = _find_line(lines, ["новому Администратору"])
    if start is None:
        return None
    end = _find_line(lines, ["Номер договора", "(номер договора", "профиля):", "(подпись)"], below=start.box[3])
    H = page.image.shape[0]
    y1 = end.box[1] if end is not None else min(H, start.box[3] + 12 * (start.box[3] - start.box[1]))
    if y1 - start.box[3] < 10:
        return None
    band = page.image[max(0, start.box[1] - 4):min(H, y1 + 4)]
    log("Заявление: перечитываю раздел «новому Администратору» отдельно…")
    return "\n".join(l.text for l in tesseract_lines(remove_lines_soft(cv2.cvtColor(band, cv2.COLOR_BGR2GRAY)),
                                                      lang="rus+eng", psm=6))


NEW_ADMIN_KEYS = ("new_admin_inline", "new_admin_org", "new_admin_org_contact", "new_admin_fio", "new_admin_contact")


def _llm_new_admin(page: Page, lines: list[Line], form: dict, prefer: Extraction) -> None:
    """Режим модели: нового администратора не нашли в тексте страницы — полоса раздела перечитывается отдельно
    (psm 6), и модель читает только поля нового администратора."""
    keys = [k for k in NEW_ADMIN_KEYS if k in form["fields"]]
    if not keys or any(prefer.get(k) for k in ("new_admin_org", "new_admin_fio") if k in keys):
        return
    txt = _new_admin_band_text(page, lines)
    if not txt:
        return
    try:
        sub = llm_extract.extract("новому Администратору:\n" + txt, form, page.source, conf=0.8, source="ocr-text",
                                  only=keys)
    except llm_extract.LLMError as e:
        prefer.notes.append(f"Раздел «новому Администратору»: модель не ответила ({e}).")
        return
    got = False
    empty = {k for k in keys if not prefer.get(k)}           # до копирования: иначе подполя (.emails …) пропадут
    for k, f in sub.fields.items():
        if k.split(".")[0] in empty:
            prefer.fields[k] = f
            got = got or ("." not in k and bool(f.value))
    prefer.notes.extend(n for n in sub.notes if n not in prefer.notes)
    prefer.debug.setdefault("llm", []).extend(sub.debug.get("llm", []))
    if got:
        prefer.notes.append("Раздел «новому Администратору» перечитан отдельно (на странице целиком его не видно).")


def _reread_new_admin(page: Page, lines: list[Line], form: dict, prefer: Extraction) -> None:
    """Таблица нового администратора, которую OCR всей страницы (psm 4) пропустил: полоса от «новому
    Администратору» до «Номер договора» распознаётся ещё раз как блок (psm 6)."""
    txt = _new_admin_band_text(page, lines)
    if not txt:
        return
    if apply_new_admin_block(prefer, txt, form, 0.8, "ocr-text"):
        prefer.notes.append("Раздел «новому Администратору» перечитан отдельно (на странице целиком его не видно).")
