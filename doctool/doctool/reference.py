"""Чтение эталонного заявления по подписям бланка (с 0.7.3) - без регулярных выражений.

Эталон - заявление, заполненное по бланку на компьютере: PDF с текстом (из Word) или DOCX. У такого документа
печатный текст бланка на месте, а значение поля стоит между известными подписями: «от … (ФИО, дата рождения)»,
«… (кем выдан)». Раздел `reference:` в описании бланка перечисляет для полей эти подписи - обычными строками:

    reference:
      min_found: 0.9                 # доля подписей, которые должны найтись по порядку, чтобы документ считался эталоном
      fields:
        applicant_header: {after: [Генеральному директору, от], before: "(ФИО, дата рождения)"}
        issued_by:        {before: "(кем выдан)"}

  before - подпись после значения (обязательно);
  after  - текст перед значением: строка или цепочка строк (ищутся по очереди); без after значение начинается
           сразу после подписи `before` предыдущего поля.
Поля перечисляются в порядке бланка. Регистр, пробелы и вид скобок ( ( [ { ) не важны; подпись, которая начинается
или кончается буквой, ищется целым словом («от» не найдётся внутри «работы»).

Если документ на бланк не похож (нашлось меньше min_found подписей - свободная форма, бланк нотариуса, изменённая
вёрстка) или значение поля не разобралось, поле читается регулярками `text:` бланка, как раньше
(application.extract_from_text). По OCR-тексту сканов подписи не ищутся: это только для документов с текстом.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

DEFAULT_MIN_FOUND = 0.9


@lru_cache(maxsize=512)
def literal_rx(s: str) -> re.Pattern:
    """Подпись бланка → выражение: без учёта регистра, пробелов и вида скобок, с границами слов."""
    s = (s or "").strip()
    out = []
    for ch in s:
        if ch in "([{":
            out.append(r"[(\[{]")
        elif ch in ")]}":
            out.append(r"[)\]}]")
        elif ch.isspace():
            if not out or out[-1] != r"\s*":
                out.append(r"\s*")
        else:
            out.append(re.escape(ch))
    rx = "".join(out)
    if s[:1].isalnum():
        rx = r"(?<!\w)" + rx
    if s[-1:].isalnum():
        rx += r"(?!\w)"
    return re.compile(rx, re.I)


@dataclass
class Reading:
    values: dict = field(default_factory=dict)      # поле → сырое значение (None - подпись не нашлась)
    found: int = 0
    total: int = 0
    missing: list = field(default_factory=list)     # подписи, которые не нашлись

    @property
    def share(self) -> float:
        return self.found / self.total if self.total else 0.0


def _afters(spec: dict) -> list[str]:
    a = spec.get("after")
    if not a:
        return []
    return [a] if isinstance(a, str) else [x for x in a if x]


def read(text: str, form: dict) -> Reading:
    """Значения полей между подписями бланка (сырые: подчёркивания и «|» ячеек таблицы убраны, переводы строк
    оставлены - по ним список доменов видит перенос имени)."""
    ref = (form or {}).get("reference") or {}
    out = Reading()
    text = text or ""
    pos = 0
    for key, spec in (ref.get("fields") or {}).items():
        spec = spec or {}
        start = pos
        for a in _afters(spec):
            out.total += 1
            m = literal_rx(a).search(text, start)
            if m:
                out.found += 1
                start = m.end()
            else:
                out.missing.append(a)
        out.total += 1
        m = literal_rx(spec.get("before", "")).search(text, start) if spec.get("before") else None
        if not m:
            out.values[key] = None
            out.missing.append(spec.get("before", ""))
            continue
        out.found += 1
        raw = re.sub(r"_{2,}", " ", text[start:m.start()])
        raw = re.sub(r"[ \t]*\|[ \t]*", " ", raw)                     # разделители ячеек таблицы DOCX
        raw = re.sub(r"[ \t]+", " ", raw)
        out.values[key] = re.sub(r"\n\s*\n+", "\n", raw).strip(" \n,;:/")
        pos = m.end()
    return out


def min_found(form: dict) -> float:
    return float(((form or {}).get("reference") or {}).get("min_found", DEFAULT_MIN_FOUND))


def matches(reading: Reading, form: dict) -> bool:
    """Документ - эталон этого бланка: подписи нашлись по порядку в нужной доле."""
    return reading.total > 0 and reading.share >= min_found(form)
