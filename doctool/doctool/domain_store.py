"""(0.8.5) Сохранённые результаты загрузки доменов из manager.

Где хранятся: вкладка «Домены в manager» - results/_domains/manager_tab.json, дело рабочего места - domains.json
в папке дела. После перезагрузки страницы (и перезапуска программы) результат на месте; при новой загрузке домены,
которые уже есть в сохранённом результате, из manager не запрашиваются - загружаются только новые. Домены с ошибкой
загрузки (нет связи, не ответило расширение, остановлено) загружаются снова. Кнопка «Сбросить результат» стирает файл.
"""
from __future__ import annotations

import json
import threading
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from .domains import ERROR, FOUND, NOT_FOUND, DomainInfo, unicode_name

REUSE = (FOUND, NOT_FOUND)          # такие результаты повторно не загружаются
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def key(domain: str) -> str:
    """Ключ домена: юникодное имя в нижнем регистре (пример.рф и xn--e1afmkfd.xn--p1ai - один домен)."""
    return unicode_name(str(domain or "").strip()).lower()


def _lock(path: Path) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(str(path.resolve()), threading.Lock())


class DomainStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    # ---- файл
    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"list": [], "items": {}, "updated": ""}
        if not isinstance(data, dict):
            return {"list": [], "items": {}, "updated": ""}
        data.setdefault("list", [])
        data.setdefault("items", {})
        data.setdefault("updated", "")
        return data

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    @staticmethod
    def _info(d: dict) -> DomainInfo:
        info = DomainInfo.from_dict(d)
        info.compare, info.verdict = [], ""      # сверка с документами - заново в каждом деле
        return info

    # ---- чтение
    def known(self) -> dict[str, DomainInfo]:
        """{ключ: DomainInfo} доменов, которые не нужно загружать снова (найден / не найден в manager)."""
        with _lock(self.path):
            data = self._read()
        return {k: self._info(d) for k, d in data["items"].items() if isinstance(d, dict) and d.get("status") in REUSE}

    def missing(self, domains: list[str]) -> list[str]:
        """Домены списка, которых нет в сохранённом результате (их надо загрузить из manager)."""
        kn = self.known()
        return [d for d in domains if key(d) not in kn]

    def current(self) -> tuple[list[str], list[DomainInfo], str]:
        """(список доменов последней загрузки, их DomainInfo в порядке списка, когда обновлено)."""
        with _lock(self.path):
            data = self._read()
        items = data["items"]
        out = [self._info(items[key(d)]) for d in data["list"] if isinstance(items.get(key(d)), dict)]
        return list(data["list"]), out, data["updated"]

    # ---- запись
    def save(self, domains: list[str], items: list[DomainInfo]) -> None:
        """Результат загрузки: список (в порядке ввода) и данные доменов. Данные доменов, которых нет в списке,
        остаются в файле: вернули домен в список - он снова не загружается."""
        with _lock(self.path):
            data = self._read()
            for i in items:
                d = asdict(i)
                d["compare"], d["verdict"] = [], ""
                data["items"][key(i.domain)] = d
            data["list"] = list(domains)
            data["updated"] = datetime.now().isoformat(timespec="seconds")
            self._write(data)

    def reset(self) -> None:
        with _lock(self.path):
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass


def lookup_cached(lookup, domains: list[str], store: DomainStore | None, log=print, cancel=None) -> list[DomainInfo]:
    """Загрузка доменов с учётом сохранённого результата: lookup(domains, log=, cancel=) вызывается только для
    доменов, которых в нём нет. Возвращает DomainInfo в порядке domains; результат сохраняется в store."""
    known = store.known() if store is not None else {}
    todo = [d for d in domains if key(d) not in known]
    reused = len(domains) - len(todo)
    if reused:
        log(f"Сохранённый результат: {reused} из {len(domains)} - не загружаются повторно"
            + ("" if todo else "; новых доменов нет"))
    fresh = {key(i.domain): i for i in (lookup(todo, log=log, cancel=cancel) if todo else [])}
    out = []
    for d in domains:
        k = key(d)
        info = fresh.get(k) or known.get(k)
        if info is None:                         # загрузка вернула меньше доменов (не должно быть)
            info = DomainInfo(domain=d, status=ERROR, error_code="skipped", error="не загружался")
        out.append(info)
    if store is not None:
        store.save(domains, out)
    return out
