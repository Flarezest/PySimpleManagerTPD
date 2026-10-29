"""Рабочее место (0.8.1): дело без файлов, добавление файлов в открытое дело, текстовый слой страниц PDF.
Без нейросети и без расширения."""
import json
import time
from pathlib import Path

import cv2
import pytest

from doctool import workspace

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "forms" / "regru_transfer_person" / "samples"


def _wait(c, ws_id, job=True, timeout=120):
    t0 = time.time()
    while True:
        d = c.get(f"/api/ws/{ws_id}").json()
        if d["status"] in ("ready", "error") and (not job or (d["job"] and d["job"]["status"] in ("done", "error"))):
            return d
        assert time.time() - t0 < timeout, d
        time.sleep(0.3)


def test_empty_case_then_add_files(tmp_path):
    from fastapi.testclient import TestClient
    from doctool.web import create_app
    c = TestClient(create_app(str(tmp_path / "res")))
    # дело без файлов - сразу готово, проверки нет
    r = c.post("/api/ws", data={"state": json.dumps({"ticket": "#2026101400000002", "operation": "Смена администратора"})})
    assert r.status_code == 200, r.text
    ws_id = r.json()["id"]
    d = _wait(c, ws_id, job=False)
    assert d["status"] == "ready" and d["files"] == [] and d["pages"] == [] and d["job"] is None
    assert c.get("/api/ws/recent").json()["items"][0]["files"] == []
    # файлы в открытое дело: разбор новых страниц и проверка
    with (SAMPLES / "pdf_reference.pdf").open("rb") as f:
        r = c.post(f"/api/ws/{ws_id}/files", files=[("files", ("Заявление.pdf", f, "application/pdf"))])
    assert r.status_code == 200 and r.json()["files"] == ["Заявление.pdf"], r.text
    d = _wait(c, ws_id)
    assert d["job"]["status"] == "done" and [p["cls"] for p in d["pages"]] == ["application", "application"]
    first_job = d["job"]["id"]
    # оператор переложил страницу - при следующем добавлении её группа не меняется, разбираются только новые
    c.post(f"/api/ws/{ws_id}/pages", json={"action": "move", "pid": "p2", "cls": "other"})
    with (SAMPLES / "docx_reference.docx").open("rb") as f:
        r = c.post(f"/api/ws/{ws_id}/files", files=[("files", ("Заявление.docx", f, "application/octet-stream"))])
    assert r.status_code == 200, r.text
    t0 = time.time()
    while True:
        d = _wait(c, ws_id)
        if d["job"]["id"] != first_job:
            break
        assert time.time() - t0 < 60
        time.sleep(0.3)
    by_id = {p["id"]: p for p in d["pages"]}
    assert by_id["p2"]["cls"] == "other" and by_id["p2"]["manual"]
    assert by_id["p3"]["kind"] == "docx" and by_id["p3"]["cls"] == "application"
    assert [f["name"] for f in d["files"]][:2] == ["Заявление.pdf", "Заявление.docx"]
    assert c.post("/api/ws/0123456789/files", files=[("files", ("a.pdf", b"x", "application/pdf"))]).status_code == 404
    # текстовый слой: слова страницы PDF с местами на превью; у DOCX - пусто
    t = c.get(f"/api/ws/{ws_id}/text/p1").json()
    words = [w for line in t["lines"] for w in line]
    assert t["rot"] == 0 and len(words) > 100 and words[0][4] == "Заявление"
    pv = by_id["p1"]["pv"]
    assert all(0 <= w[0] <= pv[0] and 0 <= w[1] <= pv[1] and w[2] > 0 and w[3] > 0 for w in words)
    assert c.get(f"/api/ws/{ws_id}/text/p3").json()["lines"] == []
    assert c.get(f"/api/ws/{ws_id}/text/p99").status_code == 404


def test_add_refused_while_busy(tmp_path):
    wsm = workspace.WorkspaceManager(str(tmp_path))
    ws = wsm.create([])
    for _ in range(100):
        if ws.status == "ready":
            break
        time.sleep(0.05)
    ws.status = "classifying"
    with pytest.raises(workspace.WsError):
        wsm.add(ws, [])


def test_text_layer_rotated_pages(tmp_path):
    """Страницы с /Rotate: слово на превью там, где его нарисовал PyMuPDF (строка идёт под углом поворота)."""
    pymupdf = pytest.importorskip("pymupdf")
    pdf = tmp_path / "rot.pdf"
    doc = pymupdf.open()
    for rot in (0, 90, 180, 270):
        pg = doc.new_page(width=595, height=842)
        pg.insert_text((100, 150), "CHECK rotation", fontsize=24, fontname="helv")
        pg.insert_text((100, 400), "text layer of the workspace viewer, page with /Rotate", fontsize=12, fontname="helv")
        pg.set_rotation(rot)
    doc.save(pdf)
    ws = workspace.Workspace("abcdef1234", tmp_path / "ws")
    ws.files_dir.mkdir(parents=True)
    ws._add_file("rot.pdf", pdf)
    ws.build_pages()
    for p, rot in zip(ws.pages, (0, 90, 180, 270)):
        t = ws.text_layer(p.id)
        x, y, w, h, word = t["lines"][0][0]
        assert t["rot"] == rot and word == "CHECK"
        # прямоугольник слова на превью: от (x, y) вдоль строки на w, поперёк - на h (как поворачивает CSS)
        corners = {0: [(x, y), (x + w, y + h)], 90: [(x - h, y), (x, y + w)],
                   180: [(x - w, y - h), (x, y)], 270: [(x, y - w), (x + h, y)]}[rot]
        (x0, y0), (x1, y1) = corners
        img = cv2.imread(str(ws.pages_dir / f"{p.id}.jpg"), cv2.IMREAD_GRAYSCALE)
        box = img[int(y0):int(y1), int(x0):int(x1)]
        assert box.size and (box < 128).mean() > 0.1, (rot, corners)
