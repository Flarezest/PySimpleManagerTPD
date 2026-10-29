"""Образцы модулей заявлений (forms/<id>/samples/): каждый образец читается программой, значения полей сверяются
с <имя>.expected.json. Новый модуль с образцами проверяется здесь сам - тест править не нужно.

Формат <имя>.expected.json:
  {"reader": "reference" | null,          # как прочитан: по подписям бланка или регулярками text:
   "fields": {"поле или поле.подполе": значение, …}}   # "" / [] - поле должно быть пустым
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from doctool import application, formspec  # noqa: E402
from doctool.ocr import load_document  # noqa: E402

CASES = [(f["id"], src, exp) for f in formspec.load_forms() for src, exp in formspec.samples(f)]


def test_every_module_has_samples():
    for f in formspec.load_forms():
        if formspec.module_dir(f) is not None:
            assert formspec.samples(f), f"у модуля {f['id']} нет образцов (forms/{f['id']}/samples/)"


@pytest.mark.parametrize("form_id,src,exp", CASES, ids=[f"{c[0]}/{c[2].name.split('.')[0]}" for c in CASES])
def test_sample(form_id, src, exp, tmp_path):
    assert src is not None, f"нет файла образца для {exp.name}"
    want = json.loads(exp.read_text(encoding="utf-8"))
    form = formspec.form_by_id(form_id)
    ex = application.extract_application(load_document(src), tmp_path, forms=[form], mode="electronic",
                                         form_id=form_id)
    assert ex.doc_type == form_id
    assert ex.debug.get("reader") == want.get("reader"), ex.debug.get("reference")
    bad = {}
    for key, value in want["fields"].items():
        got = ex.get(key)
        if value in ("", [], None):
            if got:
                bad[key] = (value, got)
        elif got != value:
            bad[key] = (value, got)
    assert not bad, "ожидалось ≠ прочитано: " + json.dumps(bad, ensure_ascii=False)
