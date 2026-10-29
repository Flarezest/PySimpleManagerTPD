"""Рабочее место (0.8.4): ручное выделение текста - текст в рамке на странице. PDF с текстовым слоем - слова слоя,
скан и фото - печатный текст (Tesseract). Без нейросети и без браузера."""
import shutil
import time
from pathlib import Path

import cv2
import numpy as np
import pymupdf
import pytest

from doctool import workspace
from doctool.ocr import check_tesseract, read_printed

ROOT = Path(__file__).resolve().parent.parent
PDF = ROOT / "forms" / "regru_transfer_person" / "samples" / "pdf_reference.pdf"
needs_ocr = pytest.mark.skipif(check_tesseract() is not None, reason="нет Tesseract с языком rus")


def _line(pg, needle):
    """Строка текстового слоя со словом needle: (текст, x0, y0, x1, y1) в пунктах страницы."""
    words = pg.get_text("words")
    w = next(x for x in words if needle in x[4])
    line = [x for x in words if x[5] == w[5] and x[6] == w[6]]
    return (" ".join(x[4] for x in line), min(x[0] for x in line), min(x[1] for x in line),
            max(x[2] for x in line), max(x[3] for x in line))


def _rect(p, page_w, box, pad=4):
    """Рамка на показанном превью страницы p по прямоугольнику box (пункты страницы, страница без поворота)."""
    k = (p.pv[1] if p.rot % 180 else p.pv[0]) / page_w
    _, x0, y0, x1, y1 = box
    return [x0 * k - pad, y0 * k - pad, (x1 - x0) * k + 2 * pad, (y1 - y0) * k + 2 * pad]


@pytest.fixture()
def case(tmp_path):
    files = tmp_path / "files"
    files.mkdir()
    shutil.copy(PDF, files / "app.pdf")
    with pymupdf.open(PDF) as doc:
        pg = doc[0]
        pix = pg.get_pixmap(dpi=300)
        img = np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w, pix.n)[:, :, :3][:, :, ::-1].copy()
        line = _line(pg, "@")
        width = pg.rect.width
        rd = pymupdf.open(PDF)
        rd[0].set_rotation(90)
        rd.save(files / "rot.pdf")
    cv2.imwrite(str(files / "scan.png"), cv2.GaussianBlur(img, (3, 3), 0))
    cv2.imwrite(str(files / "scan_ccw.png"), cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE))
    ws = workspace.Workspace("zone000001", tmp_path)
    for n in ("app.pdf", "scan.png", "scan_ccw.png", "rot.pdf"):
        ws._add_file(n, files / n)
    ws.build_pages()
    return ws, line, width


def test_zone_pdf_text_layer(case):
    ws, line, width = case
    p1 = ws.page("p1")
    r = ws.read_zone("p1", _rect(p1, width, line))
    assert r["source"] == "pdf" and r["text"] == line[0]
    # страница с /Rotate 90, оператор повернул её обратно (270) - рамка там же, где на исходной
    rot = next(p for p in ws.pages if ws.file(p.file).name == "rot.pdf" and p.index == 0)
    rot.rot = 270
    assert ws.read_zone(rot.id, _rect(rot, width, line))["text"] == line[0]
    # рамка на пустом месте PDF - слов нет, текста нет
    assert ws.read_zone("p1", [5, 5, 40, 20])["text"] == ""
    with pytest.raises(workspace.WsError):
        ws.read_zone("p1", [5, 5, 2, 2])
    with pytest.raises(workspace.WsError):
        ws.read_zone("p1", ["x"])


@needs_ocr
def test_zone_scan_rotated_and_crop(case):
    ws, line, width = case
    scan = next(p for p in ws.pages if ws.file(p.file).name == "scan.png")
    r = ws.read_zone(scan.id, _rect(scan, width, line))
    assert r["source"] == "ocr" and r["text"] == line[0] and r["conf"] > 70, r
    ccw = next(p for p in ws.pages if ws.file(p.file).name == "scan_ccw.png")
    ccw.rot = 90                                     # оператор повернул страницу - рамка по показанной странице
    assert ws.read_zone(ccw.id, _rect(ccw, width, line))["text"] == line[0]
    # вырезка со страницы: рамка - в координатах вырезки
    k = scan.pv[0] / width
    crop = ws.add_crop(scan.id, [line[1] * k - 50, line[2] * k - 50, (line[3] - line[1]) * k + 250, (line[4] - line[2]) * k + 150])
    assert ws.read_zone(crop.id, [50 - 4, 50 - 4, (line[3] - line[1]) * k + 8, (line[4] - line[2]) * k + 8])["text"] == line[0]
    with pytest.raises(workspace.WsError):
        ws.read_zone(scan.id, [5000, 5000, 100, 100])


FONT = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")


@needs_ocr
@pytest.mark.skipif(not FONT.exists(), reason="нет шрифта DejaVuSans")
def test_read_printed_pen_lines_and_vertical():
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.truetype(str(FONT), 34)
    im = Image.new("RGB", (1200, 120), "white")
    d = ImageDraw.Draw(im)
    d.text((20, 30), "Паспорт: 4512 345678, выдан 12.03.2015", font=font, fill=(20, 20, 20))
    d.line((10, 82, 1190, 82), fill=0, width=2)                       # подчёркивание
    d.rectangle((3, 3, 1196, 116), outline=0, width=3)                # линии таблицы
    pen = Image.new("RGB", im.size, "white")                          # синяя ручка поверх текста (чернила
    dp = ImageDraw.Draw(pen)                                          # просвечивают: умножение, как на бумаге)
    dp.line((250, 20, 800, 100), fill=(30, 60, 200), width=12)
    dp.ellipse((600, 15, 900, 105), outline=(40, 70, 210), width=4)
    img = (np.asarray(im, np.float32) * np.asarray(pen, np.float32) / 255).astype(np.uint8)
    r = read_printed(cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
    assert r["text"] == "Паспорт: 4512 345678, выдан 12.03.2015" and r["variant"] == "blue", r
    # номер на полях - вертикально, рамка узкая и высокая
    im = Image.new("RGB", (480, 90), (250, 225, 230))
    ImageDraw.Draw(im).text((10, 15), "45 12 345678", font=ImageFont.truetype(str(FONT), 52), fill=(200, 30, 40))
    r = read_printed(cv2.rotate(cv2.cvtColor(np.asarray(im), cv2.COLOR_RGB2BGR), cv2.ROTATE_90_CLOCKWISE))
    assert r["text"] == "45 12 345678" and r["rot"] in (90, 270), r
    assert read_printed(np.full((100, 300, 3), 250, np.uint8))["text"] == ""


@needs_ocr
def test_ocr_api(tmp_path):
    from fastapi.testclient import TestClient
    from doctool.web import create_app
    c = TestClient(create_app(str(tmp_path / "res")))
    with PDF.open("rb") as f:
        ws_id = c.post("/api/ws", files=[("files", ("Заявление.pdf", f, "application/pdf"))]).json()["id"]
    t0 = time.time()
    while c.get(f"/api/ws/{ws_id}").json()["status"] not in ("ready", "error"):
        assert time.time() - t0 < 120
        time.sleep(0.3)
    with pymupdf.open(PDF) as doc:
        line, width = _line(doc[0], "@"), doc[0].rect.width
    pv = c.get(f"/api/ws/{ws_id}").json()["pages"][0]["pv"]
    k = pv[0] / width
    r = c.post(f"/api/ws/{ws_id}/ocr", json={"pid": "p1", "rect": [line[1] * k - 4, line[2] * k - 4,
                                                                      (line[3] - line[1]) * k + 8, (line[4] - line[2]) * k + 8]})
    assert r.status_code == 200 and r.json()["text"] == line[0] and r.json()["source"] == "pdf", r.text
    assert c.post(f"/api/ws/{ws_id}/ocr", json={"pid": "p1", "rect": [1, 1, 2, 2]}).status_code == 400
    assert c.post(f"/api/ws/{ws_id}/ocr", json={"pid": "p99", "rect": [1, 1, 50, 50]}).status_code == 400
