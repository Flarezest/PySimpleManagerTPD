"""Рабочее место оператора (0.7.6): дело из архива тикетницы — страницы, их разбор по документам, сверка.

Порядок работы (веб-интерфейс, вкладка «Рабочее место», API /api/ws/*):

1. Приём: архив (zip, rar, 7z) или файлы → распаковка (archive.py) → документы: PDF, изображения, DOCX
   (и картинки, вставленные в DOCX), подписи .sig (только список — программа их не проверяет), прочие файлы.
2. Страницы: каждая страница PDF / изображение — отдельная страница с превью (JPEG) и миниатюрой.
3. Разбор страниц (classify_page): заявление (бланк, продолжение), паспорт (разворот, прописка, ранее выданные),
   прочее (конверт, опись, нотариус), не определено, скрытые (пустые страницы и повторы). Без нейросети:
   текстовый слой PDF / быстрый OCR, заголовок бланка, слова паспорта, строки MRZ, поворот страницы.
   Оператор может перенести страницу в другую группу, повернуть её или вырезать рамкой паспорт со страницы
   заявления (crop) — после этого проверка запускается заново.
4. Проверка: обычный run_case по выбранным страницам (CaseInput.app_sources / pas_sources) + загрузка Sd, S и ЕСИА
   доменов из manager (JobManager, manager_autoload).
5. Сверка (matrix): строки ФИО, дата рождения, паспорт, … — значения Sd, ЕСИА, заявления и паспорта рядом,
   с подсветкой совпадений, и места полей на страницах (boxes) для подсветки в просмотре.

Состояние дела (номер тикета, тип операции, способ получения, отметки оператора, строка таблицы, результат
проверки на Госуслугах, с 0.8.0 — запросы строки «Поиск») хранится в ws.json в папке дела рабочего места:
results/_workspace/<время>_<id>/. С 0.8.0 дело можно удалить из «Недавних дел» (WorkspaceManager.delete): папка дела
и папки результатов его проверок (result_dirs) стираются с диска. С 0.8.1 дело можно начать без файлов и добавлять
файлы в открытое дело (WorkspaceManager.add — разбираются только новые страницы); у страниц PDF с текстовым слоем —
слова с координатами для выделения текста в просмотре (Workspace.text_layer).
"""
from __future__ import annotations

import json
import re
import shutil
import threading
import traceback
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from . import archive, fieldnorm, wsconfig
from .application import detect_form, page_kind
from .formspec import Fields, load_forms
from .ocr import IMAGE_EXT, best_orientation, docx_text, downscale, keyword_score, rotate, tesseract_text
from .passport_rf import KEYWORDS as PASSPORT_KW

DOC_EXT = IMAGE_EXT | {".pdf", ".docx"}
SIG_EXT = {".sig", ".p7s", ".sgn", ".p7m", ".p7b"}
CLASSES = {"application": "Заявление", "passport": "Паспорт", "other": "Прочее", "unknown": "Не определено",
           "hidden": "Скрытые"}
PREVIEW_DPI = 170                  # превью страницы PDF для просмотра (извлечение полей — 300 dpi)
PREVIEW_MAX = 2400                 # превью изображения: длинная сторона, px
THUMB_W = 120
EXTRACT_DPI = 300                  # ocr.load_document

REG_WORDS = ["МЕСТО ЖИТЕЛЬСТВА", "ЗАРЕГИСТРИРОВАН", "РЕГИСТРАЦИ", "СНЯТ С РЕГИСТРАЦИОННОГО"]
PREV_WORDS = ["РАНЕЕ ВЫДАНН"]
OTHER_WORDS = {"опись": "опись вложения", "нотариус": "нотариальная страница", "конверт": "конверт",
               "почта россии": "конверт / почтовый бланк", "уведомление о вручении": "почтовое уведомление"}


# ------------------------------------------------------------------ модель

@dataclass
class WsFile:
    id: str
    name: str                      # имя в архиве (с папками), как видит оператор
    path: str                      # путь на диске
    kind: str                      # pdf | image | docx | sig | other
    size: int = 0
    pages: int = 0
    note: str = ""


@dataclass
class WsPage:
    id: str
    file: str                      # WsFile.id
    index: int                     # № страницы в файле (с 0)
    kind: str                      # pdf | image | docx | crop
    cls: str = "unknown"           # application | passport | other | unknown | hidden
    sub: str = ""                  # бланк, продолжение, разворот с фото, прописка, пустая, повтор стр. N …
    conf: float = 0.0
    why: str = ""
    rot: int = 0                   # поворот для показа и проверки (по часовой стрелке)
    auto_rot: bool = False         # поворот определила программа
    manual: bool = False           # группу выбрал оператор
    pv: list = field(default_factory=lambda: [0, 0])      # превью: ширина, высота (без поворота)
    sx: float = 1.0                # px превью на px страницы для проверки (одинаково по осям)
    text: bool = False             # есть текстовый слой (PDF / DOCX)
    crop: list | None = None       # вырезка: [x, y, w, h] в px страницы для проверки (после поворота src)
    crop_rot: int = 0              # вырезка: поворот исходной страницы в момент выделения
    src: str = ""                  # вырезка: id исходной страницы
    dup_of: str = ""


class WsError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _imread(path: str | Path) -> np.ndarray | None:
    return cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)


def _imwrite(path: Path, img: np.ndarray, q: int = 85) -> None:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, q])
    if ok:
        buf.tofile(str(path))


def rot_box(box: list, w: float, h: float, rot: int) -> list:
    """Прямоугольник [x0, y0, x1, y1] на изображении w×h → тот же прямоугольник после поворота изображения
    на rot градусов по часовой стрелке."""
    x0, y0, x1, y1 = box
    rot %= 360
    if rot == 90:
        return [h - y1, x0, h - y0, x1]
    if rot == 180:
        return [w - x1, h - y1, w - x0, h - y0]
    if rot == 270:
        return [y0, w - x1, y1, w - x0]
    return [x0, y0, x1, y1]


def _dhash(gray: np.ndarray) -> int:
    small = cv2.resize(gray, (17, 16), interpolation=cv2.INTER_AREA)
    bits = (small[:, 1:] > small[:, :-1]).flatten()
    return int("".join("1" if b else "0" for b in bits), 2)


def _hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


# ------------------------------------------------------------------ разбор одной страницы

def classify_text(text: str, forms: list[dict]) -> tuple[str, str, float, str] | None:
    """Страница с текстовым слоем (PDF из Word, DOCX): (группа, что это, уверенность, почему) или None."""
    if len(re.sub(r"\s", "", text or "")) < 40:
        return None
    form = detect_form(text, forms)
    if form:
        kind = page_kind(text, form)
        if kind == "cont":
            return "application", "продолжение заявления", 0.95, f"продолжение бланка «{form.get('title', form['id'])}»"
        return "application", "бланк заявления", 0.97, f"бланк «{form.get('title', form['id'])}» (текстовый слой)"
    for f in forms:
        if page_kind(text, f) == "cont":
            return "application", "продолжение заявления", 0.8, "продолжение бланка (текстовый слой)"
    up = text.upper()
    if keyword_score(up, PASSPORT_KW) >= 4:
        return "passport", "паспорт", 0.75, "слова паспорта в текстовом слое"
    low = text.lower()
    for w, what in OTHER_WORDS.items():
        if w in low:
            return "other", what, 0.6, f"в тексте «{w}»"
    return "other", "документ с текстом", 0.5, "текст есть, но это не бланк заявления"


def _scan_kind(txt: str, forms: list[dict]) -> tuple[str, str, float, str] | None:
    for f in forms:
        k = page_kind(txt, f)
        if k == "start":
            return "application", "бланк заявления", 0.9, "заголовок бланка (OCR)"
        if k == "cont":
            return "application", "продолжение заявления", 0.8, "продолжение бланка (OCR)"
    up = txt.upper()
    lines = [l.strip() for l in txt.splitlines() if l.strip()]
    mrz_like = any(l.count("<") >= 8 for l in lines)
    sc = keyword_score(up, PASSPORT_KW)
    if mrz_like and sc >= 2:
        return "passport", "разворот с фото", 0.95, "строки MRZ и слова паспорта"
    if any(w in up for w in PREV_WORDS):
        return "passport", "ранее выданные паспорта", 0.85, "«Сведения о ранее выданных паспортах»"
    if any(w in up for w in REG_WORDS) and sc < 3:
        return "passport", "прописка", 0.8, "«Место жительства» / штамп регистрации"
    if sc >= 4 or (mrz_like and sc >= 1):
        return "passport", "разворот с фото" if ("ФАМИЛИЯ" in up or mrz_like) else "паспорт", 0.85, \
            f"слова паспорта ({sc})"
    app_kw = {k for f in forms for k in f["detect"]["keywords"]}
    sc_app = sum(1 for k in app_kw if k.lower() in txt.lower())
    if sc_app >= 3:
        return "application", "похоже на заявление", 0.65, f"слова бланка ({sc_app})"
    low = txt.lower()
    for w, what in OTHER_WORDS.items():
        if w in low:
            return "other", what, 0.6, f"в тексте «{w}»"
    if sc >= 2:
        return "passport", "страница паспорта?", 0.5, f"мало слов паспорта ({sc})"
    return None


def classify_image(img: np.ndarray, forms: list[dict]) -> tuple[str, str, float, str, int]:
    """Скан / фото: (группа, что это, уверенность, почему, поворот). Поворот ищется, только если страница
    не узнана как есть (это несколько проходов OCR)."""
    gray = cv2.cvtColor(downscale(img, 1600), cv2.COLOR_BGR2GRAY)
    if float((gray < 140).mean()) < 0.002:
        return "hidden", "пустая страница", 0.99, "почти нет чернил", 0
    txt, _ = tesseract_text(gray, psm=3)
    r = _scan_kind(txt, forms)
    if r and r[2] >= 0.8:
        return (*r, 0)
    kws = PASSPORT_KW + sorted({k.upper() for f in forms for k in f["detect"]["keywords"]})
    try:
        angle, score = best_orientation(img, kws)
    except Exception:  # noqa: BLE001 — OSD Tesseract может не сработать на странной картинке
        angle, score = 0, 0
    if angle and score >= 3:
        txt2, _ = tesseract_text(rotate(gray, angle), psm=3)
        r2 = _scan_kind(txt2, forms)
        if r2:
            return r2[0], r2[1], r2[2], r2[3] + f"; страница повёрнута на {angle}°", angle
    if r:
        return (*r, 0)
    if len(re.sub(r"\W", "", txt)) < 30:
        return "unknown", "мало текста", 0.3, "мало текста — фото, штамп или рукопись", 0
    return "unknown", "?", 0.35, "не похоже ни на бланк, ни на паспорт", 0


# ------------------------------------------------------------------ дело рабочего места

class Workspace:
    def __init__(self, ws_id: str, folder: Path):
        self.id = ws_id
        self.dir = Path(folder)
        self.files: list[WsFile] = []
        self.pages: list[WsPage] = []
        self.status = "new"            # new | unpacking | pages | classifying | ready | error
        self.error = ""
        self.log: list[str] = []
        self.notes: list[str] = []
        self.job_id = ""
        self.job_ids: list[str] = []
        self.result_dirs: list[str] = []   # (0.8.0) папки результатов проверок дела (results/<время>_<дело>) — для удаления
        self.created = _now()
        self.state: dict = {"ticket": "", "operation": "", "via": "", "marks": {}, "gos": {}, "row": {}, "mgr": {},
                            "search": {}, "loa": {}}
        self.lock = threading.RLock()
        self._text: dict[str, dict] = {}   # (0.8.1) текстовый слой страниц PDF (text_layer)
        self.version = 0               # растёт при каждом изменении — интерфейс перечитывает состояние

    # ---- хранение
    @property
    def pages_dir(self) -> Path:
        return self.dir / "pages"

    @property
    def files_dir(self) -> Path:
        return self.dir / "files"

    def to_json(self) -> dict:
        return {"id": self.id, "created": self.created, "status": self.status, "error": self.error,
                "files": [asdict(f) for f in self.files], "pages": [asdict(p) for p in self.pages],
                "notes": self.notes, "job_id": self.job_id, "job_ids": self.job_ids, "result_dirs": self.result_dirs,
                "state": self.state}

    def save(self) -> None:
        with self.lock:
            self.version += 1
            data = self.to_json()
        tmp = self.dir / "ws.json.tmp"
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.dir / "ws.json")

    @classmethod
    def load(cls, folder: Path) -> "Workspace":
        data = json.loads((Path(folder) / "ws.json").read_text(encoding="utf-8"))
        ws = cls(data["id"], folder)
        ws.created = data.get("created", ws.created)
        ws.status = data.get("status", "ready")
        if ws.status not in ("ready", "error"):
            ws.status = "ready"            # разбор прервался (программу закрыли) — показываем, что есть
        ws.error = data.get("error", "")
        ws.files = [WsFile(**f) for f in data.get("files", [])]
        keys = WsPage.__dataclass_fields__.keys()
        ws.pages = [WsPage(**{k: v for k, v in p.items() if k in keys}) for p in data.get("pages", [])]
        ws.notes = data.get("notes", [])
        ws.job_id = data.get("job_id", "")
        ws.job_ids = data.get("job_ids", [])
        ws.result_dirs = data.get("result_dirs", [])
        ws.state = {**ws.state, **(data.get("state") or {})}
        return ws

    def logmsg(self, msg: str) -> None:
        with self.lock:
            self.log.append(f"{datetime.now():%H:%M:%S}  {msg}")
            self.version += 1

    def file(self, fid: str) -> WsFile:
        f = next((x for x in self.files if x.id == fid), None)
        if f is None:
            raise WsError(f"нет файла {fid}")
        return f

    def page(self, pid: str) -> WsPage:
        p = next((x for x in self.pages if x.id == pid), None)
        if p is None:
            raise WsError(f"нет страницы {pid}")
        return p

    # ---- приём файлов
    def add_upload(self, name: str, path: Path) -> None:
        """Загруженный файл: архив распаковывается, остальное — как есть."""
        name = Path(name).name
        if archive.is_archive(name):
            self.logmsg(f"Распаковываю архив {name}…")
            dest = self.files_dir / (Path(name).stem + "_архив")
            try:
                files, notes = archive.unpack(path, dest)
            except archive.ArchiveError as e:
                self.notes.append(str(e))
                self.logmsg(f"[!] {e}")
                self._add_file(name, path)
                return
            self.notes += notes
            for f in files:
                self._add_file(f"{name}/{f.relative_to(dest).as_posix()}", f)
            self.logmsg(f"{name}: файлов в архиве — {len(files)}")
        else:
            self._add_file(name, path)

    def _add_file(self, name: str, path: Path) -> None:
        ext = Path(path).suffix.lower()
        kind = ("pdf" if ext == ".pdf" else "image" if ext in IMAGE_EXT else "docx" if ext == ".docx"
                else "sig" if ext in SIG_EXT else "archive" if archive.is_archive(path) else "other")
        f = WsFile(id=f"f{len(self.files) + 1}", name=name, path=str(path), kind=kind,
                   size=Path(path).stat().st_size if Path(path).exists() else 0)
        if kind == "other":
            f.note = "формат не поддерживается — только в списке файлов"
        if kind == "sig":
            f.note = "подпись: проверяется вручную, программа её не трогает"
        self.files.append(f)

    # ---- страницы и превью
    def _new_page(self, f: WsFile, index: int, kind: str) -> WsPage:
        p = WsPage(id=f"p{len(self.pages) + 1}", file=f.id, index=index, kind=kind)
        self.pages.append(p)
        return p

    def build_pages(self, files: list[WsFile] | None = None) -> None:
        """Превью страниц файлов (по умолчанию — всех; при добавлении файлов в дело — только новых)."""
        import pymupdf
        self.pages_dir.mkdir(parents=True, exist_ok=True)
        for f in list(self.files if files is None else files):
            try:
                if f.kind == "pdf":
                    with pymupdf.open(f.path) as doc:
                        if doc.needs_pass:
                            f.note = "PDF защищён паролем — откройте и сохраните без пароля"
                            self.notes.append(f"{f.name}: {f.note}")
                            continue
                        f.pages = doc.page_count
                        for i, pg in enumerate(doc):
                            p = self._new_page(f, i, "pdf")
                            pix = pg.get_pixmap(dpi=PREVIEW_DPI)
                            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
                            img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR if pix.n == 3 else cv2.COLOR_RGBA2BGR)
                            p.text = len(re.sub(r"\s", "", pg.get_text())) > 40
                            p.sx = PREVIEW_DPI / EXTRACT_DPI
                            self._write_preview(p, img)
                elif f.kind == "image":
                    frames = self._image_frames(f.path)
                    if not frames:
                        f.note = "изображение не открылось"
                        self.notes.append(f"{f.name}: {f.note}")
                        continue
                    f.pages = len(frames)
                    if len(frames) > 1:          # многостраничный TIFF → отдельные файлы-кадры для проверки
                        for i, fr in enumerate(frames):
                            fp = Path(f.path).with_name(f"{Path(f.path).stem}_кадр{i + 1}.png")
                            cv2.imencode(".png", fr)[1].tofile(str(fp))
                            sub = WsFile(id=f"f{len(self.files) + 1}", name=f"{f.name} · кадр {i + 1}", path=str(fp),
                                         kind="image", size=fp.stat().st_size, pages=1, note="кадр многостраничного TIFF")
                            self.files.append(sub)
                            p = self._new_page(sub, 0, "image")
                            self._image_preview(p, fr)
                        f.kind, f.note = "other", f"многостраничный TIFF: кадров {len(frames)}"
                        continue
                    p = self._new_page(f, 0, "image")
                    self._image_preview(p, frames[0])
                elif f.kind == "docx":
                    f.pages = 1
                    p = self._new_page(f, 0, "docx")
                    p.text = True
                    self._docx_images(f)
            except Exception as e:  # noqa: BLE001 — один плохой файл не должен ронять разбор остальных
                f.note = f"не открылся: {e}"
                self.notes.append(f"{f.name}: {f.note}")
                traceback.print_exc()

    @staticmethod
    def _image_frames(path: str) -> list[np.ndarray]:
        if Path(path).suffix.lower() in (".tif", ".tiff"):
            ok, frames = cv2.imdecodemulti(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
            if ok and frames:
                return list(frames)
        img = _imread(path)
        return [img] if img is not None else []

    def _image_preview(self, p: WsPage, img: np.ndarray) -> None:
        h, w = img.shape[:2]
        pv = downscale(img, PREVIEW_MAX)
        p.sx = pv.shape[1] / w
        self._write_preview(p, pv)

    def _write_preview(self, p: WsPage, img: np.ndarray) -> None:
        h, w = img.shape[:2]
        _imwrite(self.pages_dir / f"{p.id}.jpg", img, 85)
        th = cv2.resize(img, (THUMB_W, max(1, round(h * THUMB_W / w))), interpolation=cv2.INTER_AREA)
        _imwrite(self.pages_dir / f"{p.id}_t.jpg", th, 80)
        p.pv = [w, h]                  # после записи файлов: to_api отдаёт только страницы с готовым превью

    def _docx_images(self, f: WsFile) -> None:
        """Фото, вставленные в Word (часто — паспорт), — отдельные страницы-изображения."""
        try:
            with zipfile.ZipFile(f.path) as z:
                media = [n for n in z.namelist() if n.startswith("word/media/")
                         and Path(n).suffix.lower() in (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff")]
                for n in sorted(media, key=archive.natural_key):
                    data = z.read(n)
                    img = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
                    if img is None or min(img.shape[:2]) < 300:      # значки, подписи, логотипы — пропускаем
                        continue
                    fp = Path(f.path).with_name(f"{Path(f.path).stem}_{Path(n).name}")
                    fp.write_bytes(data)
                    sub = WsFile(id=f"f{len(self.files) + 1}", name=f"{f.name} · {Path(n).name}", path=str(fp),
                                 kind="image", size=len(data), pages=1, note="картинка из документа Word")
                    self.files.append(sub)
                    p = self._new_page(sub, 0, "image")
                    self._image_preview(p, img)
        except zipfile.BadZipFile:
            pass

    def docx_html(self, pid: str) -> str:
        """Текст страницы DOCX для просмотра: абзацы и таблицы в порядке документа (ocr.docx_text)."""
        p = self.page(pid)
        f = self.file(p.file)
        txt = docx_text(f.path)
        import html as h
        out = []
        for line in txt.splitlines():
            if " | " in line:
                out.append("<table class='dx'><tr>" + "".join(f"<td>{h.escape(c)}</td>" for c in line.split(" | "))
                           + "</tr></table>")
            elif line.strip():
                out.append(f"<p>{h.escape(line)}</p>")
        return "\n".join(out)

    # ---- текст страницы PDF для выделения в просмотре (0.8.1)
    def text_layer(self, pid: str) -> dict:
        """Слова текстового слоя страницы PDF по строкам в px превью: {"rot": поворот страницы (/Rotate),
        "lines": [[[x, y, w, h, "слово"], …], …]} — (x, y) — левый верхний угол слова на превью, w×h — размер вдоль
        строки и поперёк; при rot ≠ 0 строка на превью идёт под этим углом. У сканов и не-PDF — пусто."""
        p = self.page(pid)
        if p.kind != "pdf" or not p.text or not (p.pv and p.pv[0]):
            return {"rot": 0, "lines": []}
        cached = self._text.get(pid)
        if cached is not None:
            return cached
        import pymupdf
        f = self.file(p.file)
        lines: dict[tuple, list] = {}
        with pymupdf.open(f.path) as doc:
            pg = doc[p.index]
            sx, sy = p.pv[0] / pg.rect.width, p.pv[1] / pg.rect.height
            s = (sx + sy) / 2
            m = pg.rotation_matrix                       # слова — в координатах страницы без поворота
            rot = pg.rotation % 360
            for x0, y0, x1, y1, word, b, ln, _ in pg.get_text("words", sort=False):
                pt = pymupdf.Point(x0, y0) * m
                lines.setdefault((b, ln), []).append([round(pt.x * sx, 1), round(pt.y * sy, 1),
                                                      round((x1 - x0) * s, 1), round((y1 - y0) * s, 1), word])
        res = {"rot": rot, "lines": list(lines.values())}
        self._text[pid] = res
        return res

    # ---- разбор страниц
    def classify(self, start: int = 0) -> None:
        """Группы страниц. start — с какой страницы разбирать (при добавлении файлов — только новые; повторы
        ищутся и среди прежних страниц)."""
        forms = load_forms()
        hashes: list[tuple[str, int]] = []
        total = len(self.pages)
        for n, p in enumerate(self.pages, 1):
            if n <= start:
                if p.kind not in ("docx", "crop") and p.cls != "hidden":
                    img = _imread(self.pages_dir / f"{p.id}.jpg")
                    if img is not None:
                        hashes.append((p.id, _dhash(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))))
                continue
            if p.manual or p.kind == "crop":
                continue
            f = self.file(p.file)
            self.logmsg(f"Определяю страницу {n} из {total}: {f.name}" + (f", стр. {p.index + 1}" if f.pages > 1 else ""))
            try:
                if p.kind == "docx":
                    r = classify_text(docx_text(f.path), forms) or ("other", "документ Word", 0.5, "без бланка")
                    p.cls, p.sub, p.conf, p.why = r
                else:
                    img = _imread(self.pages_dir / f"{p.id}.jpg")
                    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                    h = _dhash(gray)
                    dup = next((pid for pid, hh in hashes if _hamming(h, hh) <= 6), None)
                    hashes.append((p.id, h))
                    if dup and self.page(dup).cls != "hidden" and float((gray < 140).mean()) >= 0.002:
                        # повтор уже разобранной страницы — без OCR (быстрее; и не зависит от ошибок Tesseract)
                        orig = self.page(dup)
                        p.cls, p.sub, p.conf, p.why, p.dup_of = ("hidden", f"повтор: {self._label(dup)}", 0.95,
                                                                "та же страница уже есть", dup)
                        p.rot, p.auto_rot = orig.rot, orig.auto_rot      # перенесут из «Скрытых» — уже повёрнута
                        self.save()
                        continue
                    r = None
                    if p.kind == "pdf" and p.text:
                        import pymupdf
                        with pymupdf.open(f.path) as doc:
                            r = classify_text(doc[p.index].get_text(), forms)
                    if r is not None:
                        p.cls, p.sub, p.conf, p.why = r
                    else:
                        big = self._extract_image(p)
                        cls, sub, conf, why, angle = classify_image(big, forms)
                        p.cls, p.sub, p.conf, p.why = cls, sub, conf, why
                        if angle:
                            p.rot, p.auto_rot = angle, True
            except Exception as e:  # noqa: BLE001
                p.cls, p.sub, p.conf, p.why = "unknown", "?", 0.0, f"ошибка разбора: {e}"
                traceback.print_exc()
            self.save()
        self._fix_order()
        self.save()

    def _label(self, pid: str) -> str:
        p = self.page(pid)
        f = self.file(p.file)
        return f.name.split("/")[-1] + (f", стр. {p.index + 1}" if f.pages > 1 else "")

    def _fix_order(self) -> None:
        """Не узнанные страницы после страницы паспорта в том же файле — тоже паспорт (прописка, ранее выданные…);
        имя файла как подсказка: «паспорт…» / «заявление…»."""
        seen: dict[str, bool] = {}
        for p in self.pages:
            if p.manual:
                continue
            name = self.file(p.file).name.lower()
            if p.cls == "passport":
                seen[p.file] = True
            elif p.cls == "unknown":
                if seen.get(p.file):
                    p.cls, p.sub, p.conf, p.why = "passport", "страница паспорта?", 0.55, "после страницы паспорта в том же файле"
                elif re.search(r"паспорт|passport|pasport", name):
                    p.cls, p.sub, p.conf, p.why = "passport", "страница паспорта?", 0.5, "по имени файла"
                elif re.search(r"заявлен|application|zayavl", name):
                    p.cls, p.sub, p.conf, p.why = "application", "страница заявления?", 0.5, "по имени файла"

    def _extract_image(self, p: WsPage) -> np.ndarray:
        """Изображение страницы в том разрешении, в каком её читает проверка (PDF — 300 dpi)."""
        f = self.file(p.file)
        if p.kind == "pdf":
            import pymupdf
            with pymupdf.open(f.path) as doc:
                pix = doc[p.index].get_pixmap(dpi=EXTRACT_DPI)
                img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
                return cv2.cvtColor(img, cv2.COLOR_RGB2BGR if pix.n == 3 else cv2.COLOR_RGBA2BGR)
        return _imread(f.path)

    # ---- действия оператора
    def move(self, pid: str, cls: str) -> WsPage:
        if cls not in CLASSES:
            raise WsError(f"нет группы {cls}")
        p = self.page(pid)
        p.cls, p.manual = cls, True
        if p.sub in ("?", "мало текста") or cls != "hidden" and p.dup_of:
            p.sub = {"application": "страница заявления", "passport": "страница паспорта", "other": "прочее"}.get(cls, p.sub)
        p.why = "группу выбрал оператор"
        self.save()
        return p

    def set_rotation(self, pid: str, rot: int) -> WsPage:
        p = self.page(pid)
        if p.kind == "crop":
            raise WsError("вырезку не повернуть — поверните исходную страницу и выделите заново")
        p.rot, p.auto_rot = int(rot) % 360, False
        self.save()
        return p

    def add_crop(self, pid: str, rect: list, cls: str = "passport") -> WsPage:
        """Вырезка рамкой на странице (rect — [x, y, w, h] в px показанного превью, с учётом поворота)."""
        src = self.page(pid)
        if src.kind == "docx":
            raise WsError("в документе Word рамкой не выделить — у него нет изображения страницы")
        x, y, w, h = (float(v) for v in rect)
        if w < 20 or h < 20:
            raise WsError("рамка слишком маленькая")
        pv = _imread(self.pages_dir / f"{src.id}.jpg")
        if src.rot:
            pv = rotate(pv, src.rot)
        H, W = pv.shape[:2]
        x0, y0 = max(0, int(x)), max(0, int(y))
        x1, y1 = min(W, int(x + w)), min(H, int(y + h))
        if x1 - x0 < 20 or y1 - y0 < 20:
            raise WsError("рамка за пределами страницы")
        piece = pv[y0:y1, x0:x1].copy()
        f = self.file(src.file)
        p = self._new_page(f, src.index, "crop")
        p.src, p.cls, p.manual, p.conf = src.id, cls, True, 1.0
        p.sub, p.why = f"вырезка со страницы: {self._label(src.id)}", "выделено рамкой оператором"
        s = src.sx or 1.0
        p.crop = [round(x0 / s), round(y0 / s), round((x1 - x0) / s), round((y1 - y0) / s)]
        p.rot, p.crop_rot = 0, src.rot
        self._write_preview(p, piece)
        p.sx = s
        self.save()
        return p

    def set_state(self, data: dict) -> dict:
        with self.lock:
            for k in ("ticket", "operation", "via"):
                if k in data:
                    self.state[k] = str(data[k] or "")
            for k in ("marks", "gos", "row", "mgr", "search", "loa"):   # loa — (0.8.2) доверенность
                if isinstance(data.get(k), dict):
                    self.state[k] = {**(self.state.get(k) or {}), **data[k]}
        self.save()
        return self.state

    # ---- проверка
    def source_of(self, p: WsPage) -> dict:
        if p.kind == "crop":
            src = self.page(p.src)
            f = self.file(src.file)
            return {"path": f.path, "index": src.index, "rotate": p.crop_rot, "crop": p.crop, "page": p.id}
        f = self.file(p.file)
        return {"path": f.path, "index": p.index, "rotate": p.rot, "crop": None, "page": p.id}

    def sources(self) -> tuple[list[dict], list[dict]]:
        app = [self.source_of(p) for p in self.pages if p.cls == "application"]
        # паспорт: сначала разворот с фото (по нему читаются поля), затем остальные страницы по порядку
        pas = [p for p in self.pages if p.cls == "passport"]
        pas.sort(key=lambda p: 0 if "разворот" in p.sub or p.kind == "crop" else 1)
        return app, [self.source_of(p) for p in pas]

    def operation(self) -> dict:
        ops = wsconfig.load()["table"]["operations"]
        title = self.state.get("operation") or (ops[0]["title"] if ops else "")
        return next((o for o in ops if o.get("title") == title), {"title": title, "column4": "domain"})

    # ---- для интерфейса
    def to_api(self, page_url) -> dict:
        with self.lock:
            files = [asdict(f) for f in self.files]
            pages = []
            for p in self.pages:
                if not (p.pv and p.pv[0]) and p.kind != "docx":     # превью ещё пишется — страница появится в следующем опросе
                    continue
                d = asdict(p)
                d["url"], d["thumb"] = page_url(p.id, False), page_url(p.id, True)
                d["file_name"] = self.file(p.file).name
                d["file_pages"] = self.file(p.file).pages
                d["label"] = self._label(p.id) if p.kind != "crop" else p.sub
                pages.append(d)
            return {"id": self.id, "status": self.status, "error": self.error, "files": files, "pages": pages,
                    "notes": list(self.notes), "job_id": self.job_id, "state": self.state, "version": self.version,
                    "classes": CLASSES, "dir": str(self.dir.resolve())}


# ------------------------------------------------------------------ сверка: Sd, ЕСИА, заявление, паспорт

# (строка, название, поле Sd (физлицо), путь в данных ЕСИА, вид значения для fieldnorm.same)
MATRIX = (
    ("fio", "ФИО", "@fio", "@fio", "text"),
    ("birth_date", "Дата рождения", "birth_date", "birth_date", "date"),
    ("passport", "Серия и номер паспорта", "@passport", "@passport", "digits"),
    ("issue_date", "Дата выдачи", "passport_date", "rf_passport.issue_date", "date"),
    ("issued_by", "Кем выдан", "passport_place", "rf_passport.issued_by", "issuer"),
    ("department_code", "Код подразделения", "passport_place_id", "rf_passport.issue_id", "digits"),
    ("birth_place", "Место рождения", "", "", "text"),
    ("address", "Адрес регистрации", "", "", "address"),
    ("inn", "ИНН ИП", "code", "", "digits"),
    ("domains", "Домен(ы)", "", "", ""),
    ("new_admin", "Новый администратор", "", "", ""),
    ("new_admin_contact", "Контакты нового администратора", "", "", ""),
    ("contract", "Договор / аккаунт нового администратора", "", "", ""),
    ("application_date", "Дата заявления", "", "", ""),
)


def _sd_value(d: dict, key: str) -> str:
    sd = d.get("sd") or {}
    if key == "@fio":
        return d.get("fio") or ""
    if key == "@passport":
        return d.get("passport") or ""
    return sd.get(key, "") if key else ""


def _esia_value(d: dict, path: str) -> str:
    data = ((d.get("esia") or {}).get("data") or {})
    if not data or not path:
        return ""
    if path == "@fio":
        return " ".join(x for x in (data.get("last_name"), data.get("first_name"), data.get("middle_name")) if x)
    if path == "@passport":
        p = data.get("rf_passport") or {}
        return f"{p.get('series', '')} {p.get('number', '')}".strip()
    cur = data
    for part in path.split("."):
        cur = (cur or {}).get(part) if isinstance(cur, dict) else None
    return str(cur or "")


def reference_domain(domains: list[dict], name: str | None = None) -> dict | None:
    """Домен, с Sd которого сверяется дело: выбранный оператором или первый найденный (физлицо — в приоритете)."""
    found = [d for d in domains if d.get("status") == "found"]
    if name:
        hit = next((d for d in found if d.get("domain") == name), None)
        if hit:
            return hit
    return next((d for d in found if d.get("kind") == "person"), found[0] if found else None)


def matrix(rows: list[dict], domains: list[dict], domain: str | None = None) -> dict:
    """Строки сверки для рабочего места. rows — service.table_rows (+ crop_url), domains — DomainInfo.to_dict()."""
    ref = reference_domain(domains, domain)
    by_id = {r["id"]: r for r in rows}
    out = []
    found = [d for d in domains if d.get("status") == "found"]
    for rid, title, sd_key, esia_key, kind in MATRIX:
        r = by_id.get(rid)
        sd = _sd_value(ref, sd_key) if ref and ref.get("kind") == "person" else ""
        es = _esia_value(ref, esia_key) if ref and ref.get("kind") == "person" else ""
        app = (r or {}).get("app", "")
        pas = (r or {}).get("passport", "")
        if rid == "domains" and domains:
            sd = f"в manager найдено {len(found)} из {len(domains)}"
            admins = {(d.get("fio") or d.get("holder") or "").lower() for d in found}
            if len(admins) > 1:
                sd += " · администраторы разные"
        if rid == "fio" and ref and ref.get("kind") != "person":
            sd = f"{ref.get('holder') or ''} ({ref.get('kind_ru') or 'не физлицо'}) — сверка вручную"
        if not (r or sd or es):
            continue
        base_name, base = next(((n, v) for n, v in (("sd", sd), ("pas", pas), ("esia", es)) if v), ("", ""))
        cells = {}
        for name, val in (("sd", sd), ("esia", es), ("app", app), ("pas", pas)):
            st, det = (fieldnorm.same(kind, val, base) if kind and val and base and name != base_name else ("", ""))
            cells[name] = {"value": val, "status": st, "detail": det}
        if kind and sd:
            cells["sd"]["status"] = "ref"           # с ним сверяются остальные
        src = (r or {}).get("app_source", "")
        hand = (r is not None and r.get("editable_app") and
                (not app or src in ("scan", "vlm", "ocr", "llm-ocr-text") or "?" in (src or "")))
        out.append({"id": rid, "title": (r or {}).get("title") or title, "kind": kind, "cells": cells,
                    "app_source": src, "pas_source": (r or {}).get("passport_source", ""),
                    "crop_url": (r or {}).get("crop_url"), "status": (r or {}).get("status", ""),
                    "confirmed": bool((r or {}).get("confirmed")), "editable": bool((r or {}).get("editable_app")),
                    "editable_passport": bool((r or {}).get("editable_passport")),
                    "look": bool(hand), "details": (r or {}).get("details") or []})
    return {"rows": out, "domain": ref.get("domain") if ref else "", "domain_kind": ref.get("kind") if ref else "",
            "domains": [d.get("domain") for d in found]}


def field_boxes(ws: Workspace, res) -> dict:
    """Где поля заявления на страницах: {строка: [{page, box: [x0, y0, x1, y1]} | {page, text}]}.
    box — в px показанного превью (с учётом поворота страницы). Скан — область поля на бланке (application.debug
    boxes), PDF с текстом — поиск значения на странице, DOCX — текст для подсветки."""
    from .service import rows_for
    out: dict[str, list] = {}
    if res is None or res.app is None:
        return out
    app_src = res.inp.app_sources or []
    F = Fields(res.app)
    boxes = res.app.debug.get("boxes") or {}
    rows = rows_for(F.form)
    for r in rows:
        hits = []
        for role in r["app"]:
            key = F.key(role.split(".")[0])
            b = boxes.get(key)
            if b and 0 <= b["page"] < len(app_src):
                pid = app_src[b["page"]].get("page")
                p = next((x for x in ws.pages if x.id == pid), None)
                if p is not None:
                    hits.append({"page": pid, "box": [round(v * p.sx, 1) for v in b["box"]]})
                    break
        if not hits:
            val = None
            for role in r["app"]:
                f = F.field(role)
                if f is not None and f.value:
                    val = ", ".join(f.value) if isinstance(f.value, list) else str(f.value)
                    break
            if val:
                hits = _text_hits(ws, app_src, val)
        if hits:
            out[r["id"]] = hits
    return out


def _needles(val: str) -> list[str]:
    val = re.sub(r"\s+", " ", val).strip()
    out = [val[:60]] if len(val) >= 3 else []
    words = sorted({w for w in re.split(r"[\s,;]+", val) if len(w) >= 4}, key=len, reverse=True)
    return out + words[:2]


def _text_hits(ws: Workspace, app_src: list, val: str) -> list:
    import pymupdf
    hits = []
    for s in app_src:
        p = next((x for x in ws.pages if x.id == s.get("page")), None)
        if p is None or not p.text:
            continue
        if p.kind == "docx":
            hits.append({"page": p.id, "text": _needles(val)[0] if _needles(val) else val})
            break
        try:
            with pymupdf.open(ws.file(p.file).path) as doc:
                pg = doc[p.index]
                k = PREVIEW_DPI / 72
                W, H = p.pv
                for needle in _needles(val):
                    rects = pg.search_for(needle)
                    if rects:
                        for rc in rects[:3]:
                            box = [rc.x0 * k - 3, rc.y0 * k - 3, rc.x1 * k + 3, rc.y1 * k + 3]
                            hits.append({"page": p.id, "box": [round(v, 1) for v in rot_box(box, W, H, p.rot)]})
                        break
        except Exception:  # noqa: BLE001
            continue
        if hits:
            break
    return hits


# ------------------------------------------------------------------ данные для Госуслуг и manager

def gosuslugi_data(res) -> dict:
    """Серия, номер, фамилия, имя для «Проверки действительности паспорта»: из паспорта, иначе из заявления."""
    out = {"series": "", "number": "", "surname": "", "name": ""}
    if res is None:
        return out
    sn = ""
    if res.pas is not None:
        sn = res.pas.get("series_number") or ""
        out["surname"] = (res.pas.get("surname") or "").strip()
        out["name"] = (res.pas.get("given_name") or "").strip()
    F = Fields(res.app) if res.app is not None else None
    if not sn and F is not None:
        sn = F.get("passport") or ""
    d = re.sub(r"\D", "", sn)
    if len(d) == 10:
        out["series"], out["number"] = d[:4], d[4:]
    if (not out["surname"] or not out["name"]) and F is not None:
        fio = (F.get("applicant_fio") or F.get("applicant_header") or "").split()
        if len(fio) >= 2:
            out["surname"] = out["surname"] or fio[0]
            out["name"] = out["name"] or fio[1]
    out["surname"], out["name"] = out["surname"].capitalize(), out["name"].capitalize()
    return out


# (0.8.2) Безбумажные операции: поля формы «Добавить новый документ от пользователя» (manager /userdocs/loa_add,
# доверенность), которые вписывает расширение. passport_inn — «Номер паспорта (физ.лицо) / Код cтраны регистрации
# компании (RU, ...)», fullname — «ФИО Администратора / ИНН компании», email — «Высылать письма об окончании срока
# доверенности на указанный email», customer_phone — «Телефон (опционально)».
LOA_FIELDS = ("passport_inn", "fullname", "email", "customer_phone")


def loa_doc(res) -> dict:
    """(0.8.2) Паспорт и ФИО из документов дела (паспорт, иначе заявление) — запасной источник для доверенности,
    если у доменов нет Sd: {"passport": "4012 654321", "fio": "Иванов Иван Иванович"}."""
    out = {"passport": "", "fio": ""}
    if res is None:
        return out
    g = gosuslugi_data(res)
    if g["series"] and g["number"]:
        out["passport"] = f"{g['series']} {g['number']}"
    if res.pas is not None:
        cap = lambda w: "-".join(x[:1].upper() + x[1:].lower() for x in w.split("-"))   # noqa: E731
        out["fio"] = " ".join(cap(str(res.pas.get(k)).strip())
                              for k in ("surname", "given_name", "patronymic") if res.pas.get(k))
    if not out["fio"] and res.app is not None:
        F = Fields(res.app)
        out["fio"] = re.sub(r"\s+", " ", str(F.get("applicant_fio") or F.get("applicant_header") or "")).strip()
    return out


def new_admin(res) -> dict:
    """Новый администратор из заявления: ФИО или организация, e-mail, телефон, договор (аккаунт)."""
    out = {"fio": "", "org": "", "email": "", "phone": "", "contract": ""}
    if res is None or res.app is None:
        return out
    F = Fields(res.app)
    out["fio"] = F.get("new_admin_fio") or ""
    out["org"] = F.get("new_admin_org") or ""
    if not out["fio"] and not out["org"]:
        inline = F.get("new_admin_inline") or ""
        out["org" if re.search(r"\b(ООО|АО|ПАО|ЗАО|ИП|общество)\b", inline, re.I) else "fio"] = inline
    contact = " ".join(str(x) for x in (F.get("new_admin_contact"), F.get("new_admin_org_contact")) if x)
    m = re.search(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", contact)
    out["email"] = m.group(0) if m else ""
    ph = re.search(r"(?:\+7|8)[\s(-]*\d{3}[\s)-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}", contact)
    out["phone"] = ph.group(0) if ph else ""
    out["contract"] = re.sub(r"\s+", " ", str(F.get("contract") or "")).strip()
    return out


def contract_login(contract: str) -> str:
    """«Номер договора» из заявления → значение для поля «Принимающий аккаунт» manager: номер аккаунта
    (user_id:N), e-mail или логин как есть."""
    c = (contract or "").strip()
    d = re.sub(r"\D", "", c)
    if c and re.fullmatch(r"[\d\s№#-]+", c) and 5 <= len(d) <= 12:
        return f"user_id:{d}"
    m = re.search(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", c)
    if m:
        return m.group(0)
    return c.split()[0] if c else ""


def compare_new_admin(app_admin: dict, page: dict) -> list[dict]:
    """Шаг 2 смены администратора в manager: «данные нового аккаунта» ↔ новый администратор из заявления."""
    base = page.get("base") or {}
    rows = []
    for key, title, kind, app_v, mgr_v in (
            ("fio", "ФИО", "text", app_admin.get("fio") or app_admin.get("org"), base.get("fio")),
            ("email", "E-mail", "email", app_admin.get("email"), base.get("email")),
            ("phone", "Телефон", "phone", app_admin.get("phone"), base.get("phone")),
            ("passport", "Паспорт", "", "", base.get("passport")),
            ("birth_date", "Дата рождения", "", "", base.get("birth_date")),
            ("address", "Почтовый адрес", "", "", base.get("address"))):
        if not app_v and not mgr_v:
            continue
        if kind == "phone" and app_v and mgr_v:
            # в manager телефонов может быть несколько (по строке): совпадение с любым
            st = next((s for s in (fieldnorm.same("phone", app_v, x)[0] for x in re.split(r"[\n,;]+", mgr_v)) if s == "ok"),
                      fieldnorm.same("phone", app_v, mgr_v)[0])
        elif kind == "email" and app_v and mgr_v:
            st = "ok" if any(fieldnorm.same("email", app_v, x)[0] == "ok" for x in re.split(r"[\s,;]+", mgr_v) if x) else "fail"
        else:
            st = fieldnorm.same(kind, app_v, mgr_v)[0] if kind else ""
        rows.append({"key": key, "title": title, "app": app_v or "", "mgr": mgr_v or "",
                     "status": st or ("info" if mgr_v and not app_v else "")})
    return rows


# ------------------------------------------------------------------ все дела рабочего места

class WorkspaceManager:
    """Дела рабочего места: создание, фоновый разбор, поиск по id (в памяти или в папке results/_workspace)."""

    def __init__(self, out_root: str, on_ready=None):
        self.root = Path(out_root) / "_workspace"
        self.items: dict[str, Workspace] = {}
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.on_ready = on_ready          # (ws) → запустить проверку
        self.lock = threading.Lock()

    def create(self, uploads: list[tuple[str, Path]], state: dict | None = None) -> Workspace:
        """Новое дело. uploads может быть пустым (0.8.1): окна рабочего места — сразу, файлы — потом (add)."""
        ws_id = uuid.uuid4().hex[:10]
        folder = self.root / f"{datetime.now():%Y%m%d_%H%M%S}_{ws_id}"
        (folder / "files").mkdir(parents=True, exist_ok=True)
        ws = Workspace(ws_id, folder)
        ws.state.update({k: v for k, v in (state or {}).items() if k in ("ticket", "operation", "via")})
        moved = self._move_in(ws, uploads)
        ws.status = "unpacking"
        ws.save()
        with self.lock:
            self.items[ws_id] = ws
        self.pool.submit(self._intake, ws, moved)
        return ws

    def add(self, ws: Workspace, uploads: list[tuple[str, Path]]) -> Workspace:
        """(0.8.1) Файлы в открытое дело: разбираются только новые страницы, затем проверка запускается заново."""
        if self.busy(ws):
            raise WsError("дело ещё разбирается — добавьте файлы, когда разбор закончится")
        moved = self._move_in(ws, uploads)
        ws.status, ws.error = "unpacking", ""
        ws.save()
        self.pool.submit(self._intake, ws, moved)
        return ws

    @staticmethod
    def _move_in(ws: Workspace, uploads: list[tuple[str, Path]]) -> list[tuple[str, Path]]:
        moved = []
        for name, tmp in uploads:
            dst = ws.files_dir / Path(name).name
            n = 1
            while dst.exists():
                dst = ws.files_dir / f"{Path(name).stem}_{n}{Path(name).suffix}"
                n += 1
            shutil.move(str(tmp), dst)
            moved.append((Path(name).name, dst))
        return moved

    def _intake(self, ws: Workspace, uploads: list) -> None:
        try:
            nf, npg = len(ws.files), len(ws.pages)          # при добавлении в дело разбираются только новые
            for name, path in uploads:
                ws.add_upload(name, path)
            ws.status = "pages"
            if uploads:
                ws.logmsg("Готовлю страницы для просмотра…")
            ws.build_pages(ws.files[nf:])
            ws.save()
            ws.status = "classifying"
            ws.classify(start=npg)
            ws.status = "ready"
            n = {c: sum(1 for p in ws.pages if p.cls == c) for c in CLASSES}
            ws.logmsg("Страницы разобраны: " + ", ".join(f"{CLASSES[c].lower()} — {k}" for c, k in n.items() if k)
                      if ws.pages else "Дело без файлов: архив или файлы можно перетащить в рабочее место позже")
            ws.save()
            if self.on_ready:
                self.on_ready(ws)
        except Exception as e:  # noqa: BLE001
            ws.status, ws.error = "error", str(e)
            ws.logmsg(f"ОШИБКА: {e}")
            traceback.print_exc()
            ws.save()

    def get(self, ws_id: str) -> Workspace | None:
        if not re.fullmatch(r"[0-9a-f]{6,32}", ws_id or ""):
            return None
        with self.lock:
            ws = self.items.get(ws_id)
        if ws is not None:
            return ws
        hits = sorted(self.root.glob(f"*_{ws_id}/ws.json")) if self.root.exists() else []
        if not hits:
            return None
        ws = Workspace.load(hits[-1].parent)
        with self.lock:
            self.items[ws_id] = ws
        return ws

    def note_result_dir(self, ws: Workspace, case_dir) -> None:
        """(0.8.0) Запомнить папку результата проверки дела — она удаляется вместе с делом."""
        case_dir = str(Path(case_dir).resolve()) if case_dir else ""
        if case_dir and case_dir not in ws.result_dirs:
            with ws.lock:
                ws.result_dirs.append(case_dir)
            ws.save()

    def busy(self, ws: Workspace) -> bool:
        return ws.status in ("new", "unpacking", "pages", "classifying")

    def delete(self, ws_id: str, extra_dirs: list[str] | None = None) -> dict:
        """(0.8.0) Удалить дело: папку results/_workspace/<время>_<id> и папки результатов его проверок.
        Удаляется только то, что лежит внутри папки результатов программы. Дело, которое ещё разбирается, не удаляется."""
        ws = self.get(ws_id)
        if ws is None:
            raise WsError("нет такого дела")
        if self.busy(ws):
            raise WsError("дело ещё разбирается — удалите его, когда разбор закончится")
        out_root = self.root.parent.resolve()
        removed = []
        targets = [ws.dir] + [Path(d) for d in (ws.result_dirs + list(extra_dirs or []))]
        for d in targets:
            try:
                rp = Path(d).resolve()
            except OSError:
                continue
            if rp == out_root or out_root not in rp.parents or not rp.is_dir():
                continue
            if rp != ws.dir.resolve() and rp.parent == self.root.resolve():
                continue            # папку другого дела рабочего места не трогаем
            shutil.rmtree(rp, ignore_errors=True)
            removed.append(str(rp))
        with self.lock:
            self.items.pop(ws.id, None)
        return {"id": ws.id, "removed": removed}

    def all_ids(self) -> list[str]:
        if not self.root.exists():
            return []
        ids = []
        for f in sorted(self.root.glob("*/ws.json")):
            m = re.search(r"_([0-9a-f]{6,32})$", f.parent.name)
            if m:
                ids.append(m.group(1))
        return ids

    def recent(self, limit: int = 15) -> list[dict]:
        out = []
        if not self.root.exists():
            return out
        for f in sorted(self.root.glob("*/ws.json"), reverse=True)[:limit]:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            st = data.get("state") or {}
            out.append({"id": data.get("id"), "created": data.get("created"), "ticket": st.get("ticket", ""),
                        "operation": st.get("operation", ""), "files": [x.get("name") for x in data.get("files", [])][:4]})
        return out
