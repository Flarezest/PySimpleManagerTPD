"""Локальный веб-интерфейс: python -m doctool web  →  http://127.0.0.1:8765

Работает без интернета: страница и скрипты отдаются этим же сервером, внешних CDN нет.
Сюда же подключается расширение Chrome «PySimpleManager» (WebSocket /ext) — через него
загружаются данные доменов (Sd, S, ЕСИА) и аккаунтов из manager и заполняется базовая анкета
(вкладка «Смена владельца ЛК», API /api/owner/*).

С 0.7.6 — «Рабочее место» (API /api/ws/*, doctool/workspace.py): дело из архива тикетницы, страницы по
документам, сверка Sd / ЕСИА / заявление / паспорт, заполнение «Проверки действительности паспорта» на Госуслугах
и страницы смены администратора в manager (через расширение), строка таблицы.
"""
from __future__ import annotations

import base64
import json
import shutil
import tempfile
import uuid
from pathlib import Path
from urllib.parse import quote

import re

import cv2
import numpy as np
from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile, WebSocket
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

from . import APP_NAME, __version__, account, domain_report, workspace, wsconfig
from .domains import ManagerUnavailable, sort_by_problem, split_domains
from .integrations import push_to_system
from .jobs import JobManager, result_payload
from .manager_bridge import ExtensionBridge, ManagerTasks
from .ocr import IMAGE_EXT, OllamaVLM, downscale
from .service import APP_MODES, PAS_MODES, CaseInput, table_rows
from .verdict import load_case_types

STATIC = Path(__file__).resolve().parent / "static"


def create_app(out_root: str = "results", default_model: str = "qwen3-vl:4b-instruct",
               ollama: str = "http://127.0.0.1:11434", llm_model: str | None = None) -> FastAPI:
    from .llm_extract import DEFAULT_MODEL as LLM_DEFAULT
    llm_model = llm_model or LLM_DEFAULT
    app = FastAPI(title=APP_NAME, version=__version__, docs_url=None, redoc_url=None)
    bridge = ExtensionBridge()
    manager = ManagerTasks(bridge)
    jobs = JobManager(manager=manager)
    app.state.bridge, app.state.manager, app.state.jobs = bridge, manager, jobs
    uploads = Path(out_root) / "_uploads"

    def file_url(job_id: str):
        def f(path: str) -> str:
            return f"/api/jobs/{job_id}/file?path={quote(str(Path(path).resolve()))}"
        return f

    @app.get("/", response_class=HTMLResponse)
    def index():
        return (STATIC / "index.html").read_text(encoding="utf-8")

    @app.get("/static/{name}")
    def static_file(name: str):
        """(0.7.6) Скрипт и стили рабочего места (doctool/static/), без внешних CDN."""
        if name not in ("workspace.js", "workspace.css"):
            raise HTTPException(404)
        media = "text/javascript; charset=utf-8" if name.endswith(".js") else "text/css; charset=utf-8"
        return Response((STATIC / name).read_text(encoding="utf-8"), media_type=media,
                        headers={"Cache-Control": "no-cache"})

    @app.get("/api/meta")
    def meta():
        types = [{"id": k, "title": v["title"], "enabled": v.get("enabled", True), "has_form": bool(v.get("form"))}
                 for k, v in load_case_types().items()]
        models = []
        try:
            import urllib.request
            with urllib.request.urlopen(f"{ollama}/api/tags", timeout=2) as r:
                models = [m["name"] for m in json.load(r).get("models", [])]
        except Exception:  # noqa: BLE001
            pass
        return {"app": APP_NAME, "version": __version__,
                "case_types": types, "app_modes": APP_MODES, "pas_modes": PAS_MODES,
                "default_model": default_model, "ollama": ollama, "ollama_models": models,
                "default_llm_model": llm_model}

    def _save(f: UploadFile | None, folder: Path) -> str | None:
        if f is None or not f.filename:
            return None
        folder.mkdir(parents=True, exist_ok=True)
        dst = folder / Path(f.filename).name
        with dst.open("wb") as out:
            shutil.copyfileobj(f.file, out)
        return str(dst)

    @app.post("/api/jobs")
    async def create_job(options: str = Form(...), application: UploadFile | None = File(None),
                         passport: UploadFile | None = File(None)):
        opt = json.loads(options)
        folder = uploads / uuid.uuid4().hex[:12]
        app_path, pas_path = _save(application, folder), _save(passport, folder)
        if not app_path and not pas_path:
            raise HTTPException(400, "Загрузите хотя бы один файл")
        roles = opt.get("page_roles")
        inp = CaseInput(
            case_type=opt.get("case_type", "admin_change_person"),
            application=app_path or (pas_path if opt.get("combined") else None),
            passport=None if opt.get("combined") else pas_path,
            combined=bool(opt.get("combined")),
            page_roles={int(k): v for k, v in roles.items()} if roles else None,
            app_mode=opt.get("app_mode", "auto"), app_handwritten=bool(opt.get("handwritten")),
            pas_mode=opt.get("pas_mode", "auto"),
            vlm_model=(opt.get("model") or default_model) if opt.get("use_vlm") else None,
            ollama=ollama,
            case_id=opt.get("case_id") or None, out_root=out_root,
            manager_autoload=bool(opt.get("manager_autoload")),
            llm_fields=bool(opt.get("llm_fields")), llm_model=opt.get("llm_model") or llm_model)
        job = jobs.submit(inp)
        return {"job_id": job.id}

    @app.post("/api/jobs/manual")
    def create_manual(body: dict = Body(default={})):
        """(0.7.3) Ручной ввод данных заявления: пустое дело выбранного типа, данные — в таблице результата."""
        ct = body.get("case_type") or "admin_change_person"
        if ct not in load_case_types():
            raise HTTPException(400, f"Неизвестный тип заявления: {ct}")
        job = jobs.manual(CaseInput(case_type=ct, out_root=out_root, case_id=body.get("case_id") or None, ollama=ollama))
        return {"job_id": job.id, "result": result_payload(job.result, file_url(job.id))}

    @app.get("/api/jobs/{job_id}")
    def job_status(job_id: str, since: int = 0):
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(404, "Нет такой задачи")
        data = {"status": job.status, "log": job.log[since:], "log_len": len(job.log), "error": job.error,
                "combined": job.inp.combined}
        if job.status == "done" and job.result is not None:
            data["result"] = result_payload(job.result, file_url(job_id))
        return data

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel(job_id: str):
        job = jobs.get(job_id)
        if job:
            job.cancel.set()
        return {"ok": True}

    @app.post("/api/jobs/{job_id}/recompute")
    async def recompute(job_id: str, body: dict):
        job = jobs.get(job_id)
        if not job or job.result is None:
            raise HTTPException(404, "Нет результата")
        jobs.recompute(job_id, body.get("overrides") or {}, body.get("confirmed") or [])
        return result_payload(job.result, file_url(job_id))

    @app.post("/api/jobs/{job_id}/push")
    def push(job_id: str):
        job = jobs.get(job_id)
        if not job or job.result is None:
            raise HTTPException(404, "Нет результата")
        try:
            push_to_system(job.result.record)
        except NotImplementedError as e:
            return JSONResponse({"ok": False, "message": str(e)}, status_code=501)
        return {"ok": True}

    @app.get("/api/jobs/{job_id}/file")
    def get_file(job_id: str, path: str):
        job = jobs.get(job_id)
        if not job or job.result is None:
            raise HTTPException(404)
        p = Path(path).resolve()
        root = Path(job.result.case_dir).resolve()
        if root not in p.parents and p != root:
            raise HTTPException(403)
        if not p.exists():
            raise HTTPException(404)
        if p.suffix == ".json":
            return FileResponse(p, media_type="application/json", filename=p.name)
        return FileResponse(p)

    @app.get("/api/jobs/{job_id}/page/{n}")
    def page_thumb(job_id: str, n: int):
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(404)
        src = Path(job.inp.application or job.inp.passport)
        if src.suffix.lower() == ".pdf":
            import pymupdf
            with pymupdf.open(src) as d:
                if n >= d.page_count:
                    raise HTTPException(404)
                return Response(d[n].get_pixmap(dpi=40).tobytes("png"), media_type="image/png")
        if src.suffix.lower() in IMAGE_EXT and n == 0:
            img = cv2.imdecode(np.fromfile(str(src), dtype=np.uint8), cv2.IMREAD_COLOR)
            return Response(cv2.imencode(".png", downscale(img, 300))[1].tobytes(), media_type="image/png")
        raise HTTPException(404)

    # ---------------------------------------------------------------- manager (расширение Chrome)
    @app.websocket("/ext")
    async def ext_socket(ws: WebSocket):
        await bridge.serve(ws)

    @app.get("/api/manager/status")
    def manager_status():
        return bridge.status()

    @app.post("/api/manager/lookup")
    def manager_lookup(body: dict = Body(...)):
        doms = body.get("domains") or []
        doms = split_domains(doms if isinstance(doms, str) else " ".join(doms))
        if not doms:
            raise HTTPException(400, "Укажите домены")
        if not bridge.connected():
            raise HTTPException(409, "Расширение «PySimpleManager» не подключено: откройте Chrome с расширением "
                                     "(значок расширения покажет состояние связи)")
        job_id = body.get("job_id") or None
        job = jobs.get(job_id) if job_id else None
        if job_id and (job is None or job.result is None):
            raise HTTPException(404, "Нет результата проверки для этих доменов")
        on_done = (lambda t: jobs.attach_domains(job_id, t.items)) if job else None
        t = manager.submit(doms, job_id=job_id if job else None, on_done=on_done)
        return {"task_id": t.id, "domains": doms}

    @app.get("/api/manager/tasks/{task_id}")
    def manager_task(task_id: str, since: int = 0):
        t = manager.get(task_id)
        if not t:
            raise HTTPException(404, "Нет такой задачи")
        data = {"status": t.status, "log": t.log[since:], "log_len": len(t.log), "error": t.error,
                "domains": t.domains, "items": [i.to_dict() for i in sort_by_problem(t.items)]}
        if t.status == "done" and t.job_id:
            job = jobs.get(t.job_id)
            data["result"] = result_payload(job.result, file_url(t.job_id))
        return data

    def _report_response(html_text: str, name: str) -> Response:
        return Response(html_text, media_type="text/html; charset=utf-8",
                        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})

    @app.get("/api/manager/tasks/{task_id}/report")
    def manager_task_report(task_id: str):
        """Выгрузка по доменам (HTML), загруженным кнопкой «Загрузить данные из manager»."""
        t = manager.get(task_id)
        if not t or not t.items:
            raise HTTPException(404, "Нет данных доменов")
        if t.job_id:
            return job_domains_report(t.job_id)
        return _report_response(domain_report.build([i.to_dict() for i in t.items]), domain_report.filename())

    @app.get("/api/jobs/{job_id}/domains_report")
    def job_domains_report(job_id: str):
        """Выгрузка по доменам дела: с проверками дела и сверкой Sd с заявлением/паспортом."""
        job = jobs.get(job_id)
        if not job or job.result is None or not job.result.domains_sd:
            raise HTTPException(404, "Нет данных доменов")
        res = job.result
        text = domain_report.build([i.to_dict() for i in res.domains_sd], case_id=res.case_id,
                                   case_title=res.case_type.get("title", ""), checks=res.checks)
        (Path(res.case_dir) / "domains.html").write_text(text, encoding="utf-8")
        return _report_response(text, domain_report.filename(res.case_id))

    @app.post("/api/manager/tasks/{task_id}/cancel")
    def manager_cancel(task_id: str):
        t = manager.get(task_id)
        if t:
            t.cancel.set()
        return {"ok": True}

    # ---------------------------------------------------------------- смена владельца ЛК (физлица, с 0.7.3 — ИП и юрлица)
    def _need_extension():
        if not bridge.connected():
            raise HTTPException(409, "Расширение «PySimpleManager» не подключено: откройте Chrome с расширением "
                                     "(значок расширения покажет состояние связи)")

    def _ext(cmd: str, params: dict, timeout: float = 240, log=None) -> dict:
        """Запрос к расширению; ошибки связи — ответом {ok: false, error}."""
        try:
            return bridge.request_sync(cmd, params, timeout,
                                       (lambda m: log(f"manager: {m.get('message') or m.get('step')}")) if log else None)
        except ManagerUnavailable as e:
            raise HTTPException(409, str(e)) from e
        except TimeoutError as e:
            return {"ok": False, "error": {"code": "timeout", "message": f"расширение не ответило вовремя ({e})"}}

    @app.post("/api/owner/parse")
    def owner_parse(body: dict = Body(...)):
        """Что ввели в поле «Пользователь» (подсказка под полем)."""
        return account.parse_query(body.get("query") or "")

    @app.post("/api/owner/account")
    def owner_account(body: dict = Body(...)):
        """Данные аккаунта и базовая анкета (по номеру, логину/e-mail или домену)."""
        _need_extension()
        log: list[str] = []
        try:
            acc = account.resolve_account(lambda c, p, t, cb: _ext(c, p, t, log.append), body.get("query") or "",
                                          log=log.append)
        except account.AccountError as e:
            return {"ok": False, "error": {"code": e.code, "message": str(e)}, "log": log}
        return {"ok": True, "account": acc, "log": log}

    @app.post("/api/owner/compare")
    def owner_compare(body: dict = Body(...)):
        """Текущий владелец: базовая анкета ↔ данные заявления. kind — тип анкеты аккаунта (pp / ip / org)."""
        return account.compare_owner(body.get("ba") or {}, body.get("app") or {}, body.get("kind") or "pp")

    @app.post("/api/owner/prepare")
    def owner_prepare(body: dict = Body(...)):
        """Новый владелец: что и как будет вписано в базовую анкету (предпросмотр). kind — pp / ip / org."""
        return account.prepare_fill(body.get("form") or {}, body.get("kind") or None)

    @app.post("/api/owner/fill")
    def owner_fill(body: dict = Body(...)):
        """«Заполнить автоматически» / «Вернуть как было» (restore: значения, которые были до заполнения).
        kind — тип нового владельца (pp / ip / org): страница базовой анкеты переключается на него. При restore
        kind — тип заполненной формы, back_type — тип анкеты до заполнения (вернуть и его)."""
        uid = str(body.get("user_id") or "").strip()
        if not uid.isdigit():
            raise HTTPException(400, "Сначала загрузите аккаунт (нужен номер аккаунта)")
        restore = bool(body.get("restore"))
        kind = body.get("kind") or "pp"
        if kind not in account.FILL_BY_KIND:
            raise HTTPException(400, f"Неизвестный тип нового владельца: {kind}")
        prepared = None
        if restore:
            fields = {k: str(v) for k, v in (body.get("fields") or {}).items()}
        else:
            prepared = account.prepare_fill(body.get("form") or {}, kind)
            fields = prepared["fields"]
        if not fields and not (restore and body.get("back_type")):
            raise HTTPException(400, "Нечего заполнять: форма нового владельца пустая")
        _need_extension()
        params = {"user_id": uid, "fields": fields, "restore": restore, "type": kind}
        if restore and body.get("back_type") in account.FILL_BY_KIND:
            params["back_type"] = body["back_type"]
        reply = _ext("fill_runic", params, timeout=300)
        return {"ok": bool(reply.get("ok")), "data": reply.get("data"), "error": reply.get("error"),
                "prepared": prepared, "fields": fields, "kind": kind}

    @app.post("/api/owner/verify")
    def owner_verify(body: dict = Body(...)):
        """«Проверить, что сохранилось»: перечитать базовую анкету и сверить с тем, что заполняли."""
        uid = str(body.get("user_id") or "").strip()
        if not uid.isdigit():
            raise HTTPException(400, "Нет номера аккаунта")
        _need_extension()
        reply = _ext("account", {"user_id": uid})
        if not reply.get("ok"):
            return {"ok": False, "error": reply.get("error")}
        acc = account.account_from_extension(reply.get("data") or {})
        form = acc["runic_forms"].get("org" if body.get("kind") == "org" else "pp") or acc["runic"]
        v = account.verify_saved(form, body.get("fields") or {})
        if body.get("kind") and acc["type"] and acc["type"] != body["kind"]:
            v["ok"] = False
            v["mismatch"].insert(0, f"тип анкеты: {account.TYPE_RU.get(acc['type'], acc['type'])}, а заполняли "
                                    f"{account.TYPE_RU.get(body['kind'], body['kind'])}")
        return {"ok": True, "verify": v, "account": acc}

    # ---------------------------------------------------------------- рабочее место (0.7.6)
    def _ws_check(ws: workspace.Workspace) -> str | None:
        """Проверка дела рабочего места: run_case по страницам заявления и паспорта + данные доменов из manager."""
        op = ws.operation()
        ct = op.get("case_type") or ""
        if not ct or ct not in load_case_types():
            ws.logmsg(f"Операция «{op.get('title')}»: проверка заявления не настроена — документы сверяются вручную")
            ws.save()
            return None
        app_src, pas_src = ws.sources()
        if not app_src and not pas_src:
            ws.logmsg("Нет страниц заявления и паспорта — проверка не запускалась")
            ws.save()
            return None
        ticket = (ws.state.get("ticket") or "").strip().lstrip("#")
        inp = CaseInput(case_type=ct, app_sources=app_src, pas_sources=pas_src, ollama=ollama, out_root=out_root,
                        case_id=ticket or None, manager_autoload=True)
        job = jobs.submit(inp)
        ws.job_id = job.id
        ws.job_ids.append(job.id)
        ws.logmsg(f"Проверка запущена: заявление — {len(app_src)} стр., паспорт — {len(pas_src)} стр.")
        ws.save()
        return job.id

    wsm = workspace.WorkspaceManager(out_root, on_ready=_ws_check)
    app.state.workspaces = wsm

    def _ws(ws_id: str) -> workspace.Workspace:
        ws = wsm.get(ws_id)
        if ws is None:
            raise HTTPException(404, "Нет такого дела рабочего места")
        return ws

    def _page_url(ws_id: str):
        def f(pid: str, thumb: bool) -> str:
            return f"/api/ws/{ws_id}/page/{pid}" + ("?thumb=1" if thumb else "")
        return f

    def _ws_job(ws: workspace.Workspace):
        job = jobs.get(ws.job_id) if ws.job_id else None
        return job

    @app.get("/api/ws/meta")
    def ws_meta():
        cfg = wsconfig.load()
        types = load_case_types()
        ops = [{**o, "checked": bool(o.get("case_type") and o["case_type"] in types)} for o in cfg["table"]["operations"]]
        return {"operations": ops, "receipt_methods": cfg["table"].get("receipt_methods") or [],
                "status_default": cfg["table"].get("status_default") or "", "operators": cfg["table"].get("operators") or {},
                "classes": workspace.CLASSES}

    @app.get("/api/ws/recent")
    def ws_recent():
        return {"items": wsm.recent()}

    @app.post("/api/ws")
    async def ws_create(files: list[UploadFile] = File(...), state: str = Form("{}")):
        tmp = Path(tempfile.mkdtemp(prefix="pysm_"))
        uploads = []
        for f in files:
            if not f.filename:
                continue
            dst = tmp / f"{len(uploads)}_{Path(f.filename).name}"
            with dst.open("wb") as out:
                shutil.copyfileobj(f.file, out)
            uploads.append((Path(f.filename).name, dst))
        if not uploads:
            raise HTTPException(400, "Перетащите архив из тикетницы или файлы заявления и паспорта")
        try:
            st = json.loads(state or "{}")
        except ValueError:
            st = {}
        ws = wsm.create(uploads, st)
        shutil.rmtree(tmp, ignore_errors=True)
        return {"id": ws.id}

    @app.get("/api/ws/{ws_id}")
    def ws_get(ws_id: str, since: int = 0):
        ws = _ws(ws_id)
        data = ws.to_api(_page_url(ws_id))
        data["log"], data["log_len"] = ws.log[since:], len(ws.log)
        job = _ws_job(ws)
        data["job"] = ({"id": job.id, "status": job.status, "error": job.error, "log": job.log[-6:]} if job else
                       ({"id": ws.job_id, "status": "lost"} if ws.job_id else None))
        return data

    @app.get("/api/ws/{ws_id}/page/{pid}")
    def ws_page(ws_id: str, pid: str, thumb: int = 0):
        ws = _ws(ws_id)
        if not re.fullmatch(r"p\d+", pid):
            raise HTTPException(404)
        p = ws.pages_dir / (f"{pid}_t.jpg" if thumb else f"{pid}.jpg")
        if not p.exists():
            raise HTTPException(404)
        return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "max-age=3600"})

    @app.get("/api/ws/{ws_id}/docx/{pid}", response_class=HTMLResponse)
    def ws_docx(ws_id: str, pid: str):
        ws = _ws(ws_id)
        try:
            return ws.docx_html(pid)
        except workspace.WsError as e:
            raise HTTPException(404, str(e)) from e

    @app.get("/api/ws/{ws_id}/file/{fid}")
    def ws_file(ws_id: str, fid: str):
        ws = _ws(ws_id)
        try:
            f = ws.file(fid)
        except workspace.WsError as e:
            raise HTTPException(404, str(e)) from e
        p = Path(f.path).resolve()
        if ws.dir.resolve() not in p.parents:
            raise HTTPException(403)
        return FileResponse(p, filename=Path(f.name).name)

    @app.post("/api/ws/{ws_id}/pages")
    def ws_pages(ws_id: str, body: dict = Body(...)):
        """Действия со страницами: move (группа), rotate (поворот), crop (вырезка рамкой)."""
        ws = _ws(ws_id)
        act = body.get("action")
        try:
            if act == "move":
                ws.move(body["pid"], body["cls"])
            elif act == "rotate":
                ws.set_rotation(body["pid"], int(body.get("rot") or 0))
            elif act == "crop":
                p = ws.add_crop(body["pid"], body["rect"], body.get("cls") or "passport")
                return {"ok": True, "page": p.id, "ws": ws.to_api(_page_url(ws_id))}
            else:
                raise HTTPException(400, f"Неизвестное действие: {act}")
        except (workspace.WsError, KeyError) as e:
            raise HTTPException(400, str(e)) from e
        return {"ok": True, "ws": ws.to_api(_page_url(ws_id))}

    @app.post("/api/ws/{ws_id}/check")
    def ws_check(ws_id: str):
        ws = _ws(ws_id)
        if ws.status not in ("ready", "error"):
            raise HTTPException(409, "Страницы ещё разбираются — подождите")
        job_id = _ws_check(ws)
        return {"job_id": job_id}

    @app.post("/api/ws/{ws_id}/state")
    def ws_state(ws_id: str, body: dict = Body(...)):
        ws = _ws(ws_id)
        return {"state": ws.set_state(body)}

    @app.get("/api/ws/{ws_id}/result")
    def ws_result(ws_id: str, domain: str = ""):
        """Результат проверки дела для рабочего места: строки, проверки, домены, сверка, места полей."""
        ws = _ws(ws_id)
        job = _ws_job(ws)
        if job is None or job.result is None:
            return {"ready": False, "status": job.status if job else "none"}
        res = job.result
        payload = result_payload(res, file_url(job.id))
        payload["job_id"] = job.id
        mx = workspace.matrix(payload["rows"], payload["domains"], domain or None)
        adm = workspace.new_admin(res)
        return {"ready": True, "result": payload, "matrix": mx, "boxes": workspace.field_boxes(ws, res),
                "gos": workspace.gosuslugi_data(res), "new_admin": adm,
                "login_to": workspace.contract_login(adm.get("contract", ""))}

    @app.post("/api/ws/{ws_id}/gosuslugi")
    def ws_gosuslugi(ws_id: str, body: dict = Body(default={})):
        """Вписать серию, номер, фамилию и имя на странице «Проверка действительности паспорта» (Госуслуги).
        «Продолжить» нажимает оператор."""
        ws = _ws(ws_id)
        job = _ws_job(ws)
        data = workspace.gosuslugi_data(job.result if job else None)
        data.update({k: str(v).strip() for k, v in body.items() if k in data and v})
        if not data["series"] or not data["number"]:
            raise HTTPException(400, "Нет серии и номера паспорта — впишите их в строке «Серия и номер паспорта»")
        _need_extension()
        reply = _ext("gosuslugi_fill", data, timeout=240)
        if reply.get("ok"):
            ws.set_state({"gos": {"status": "filled", "data": data}})
        return {"ok": bool(reply.get("ok")), "data": reply.get("data"), "error": reply.get("error"), "sent": data}

    @app.post("/api/ws/{ws_id}/manager/fill")
    def ws_manager_fill(ws_id: str, body: dict = Body(...)):
        """Шаг 1 «Перенос доменов между аккаунтами»: домены, смена администратора, перенос на аккаунт,
        принимающий аккаунт, документы в «Загрузить документы». «Далее» нажимает оператор."""
        ws = _ws(ws_id)
        doms = split_domains(" ".join(body.get("domains") or []) if isinstance(body.get("domains"), list)
                             else str(body.get("domains") or ""))
        if not doms:
            raise HTTPException(400, "Нет доменов для смены")
        files = []
        total = 0
        for fid in body.get("files") or []:
            try:
                f = ws.file(fid)
            except workspace.WsError:
                continue
            data = Path(f.path).read_bytes()
            total += len(data)
            if total > 40 * 1024 * 1024:
                raise HTTPException(400, "Документов больше 40 МБ — прикрепите их в manager вручную")
            files.append({"name": Path(f.name).name, "type": _mime(f.path), "b64": base64.b64encode(data).decode("ascii")})
        params = {"domains": doms, "change_admin": bool(body.get("change_admin", True)),
                  "change_account": bool(body.get("change_account")), "login_to": str(body.get("login_to") or ""),
                  "files": files}
        _need_extension()
        reply = _ext("domain_move_fill", params, timeout=300)
        if reply.get("ok"):
            ws.set_state({"mgr": {"step": 1, "domains": doms, "change_account": params["change_account"],
                                  "login_to": params["login_to"], "files": [f["name"] for f in files]}})
        return {"ok": bool(reply.get("ok")), "data": reply.get("data"), "error": reply.get("error")}

    @app.post("/api/ws/{ws_id}/manager/read")
    def ws_manager_read(ws_id: str, body: dict = Body(default={})):
        """Шаг 2: прочитать «данные нового администратора» на странице manager, сверить с заявлением и
        подсветить на странице (зелёный — совпадает, красный — нет)."""
        ws = _ws(ws_id)
        job = _ws_job(ws)
        adm = {**workspace.new_admin(job.result if job else None), **{k: v for k, v in (body.get("admin") or {}).items() if v}}
        _need_extension()
        reply = _ext("domain_move_read", {}, timeout=120)
        if not reply.get("ok"):
            return {"ok": False, "error": reply.get("error")}
        page = reply.get("data") or {}
        rows = workspace.compare_new_admin(adm, page)
        _ext("domain_move_mark", {"marks": {r["key"]: r["status"] for r in rows}}, timeout=60)
        ws.set_state({"mgr": {"step": 2, "rows": rows}})
        return {"ok": True, "page": page, "rows": rows, "admin": adm}

    @app.post("/api/ws/{ws_id}/manager/fill_contacts")
    def ws_manager_fill_contacts(ws_id: str, body: dict = Body(...)):
        """Шаг 2, «заполнить вручную»: ФИО, телефон, e-mail нового администратора из заявления."""
        _ws(ws_id)
        fields = {k: str(v) for k, v in (body.get("fields") or {}).items() if v}
        if not fields:
            raise HTTPException(400, "Нечего заполнять")
        _need_extension()
        reply = _ext("domain_move_fill_contacts", {"fields": fields}, timeout=120)
        return {"ok": bool(reply.get("ok")), "data": reply.get("data"), "error": reply.get("error")}

    @app.get("/api/manager/operator")
    def manager_operator():
        """Логин оператора в manager (шапка страниц: [логин]) и фамилия из config/workspace.yaml (operators)."""
        if not bridge.connected():
            return {"ok": False, "login": "", "surname": ""}
        reply = _ext("check_manager", {}, timeout=60)
        login = ((reply.get("data") or {}).get("login") or "").strip()
        ops = wsconfig.load()["table"].get("operators") or {}
        return {"ok": bool(reply.get("ok")), "login": login, "surname": ops.get(login.lower(), "")}

    return app


def _mime(path: str) -> str:
    import mimetypes
    return mimetypes.guess_type(path)[0] or "application/octet-stream"


def already_running(host: str, port: int) -> str | None:
    """Порт свободен — None; на нём уже работает PySimpleManager — его версия; занят другой программой — ""."""
    import socket
    import urllib.request
    probe = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    try:
        with socket.create_connection((probe, port), timeout=1):
            pass
    except OSError:
        return None
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # мимо прокси из настроек системы
        with opener.open(f"http://{probe}:{port}/api/meta", timeout=5) as r:
            meta = json.loads(r.read().decode("utf-8"))
        if meta.get("app") == APP_NAME:
            return str(meta.get("version") or "?")
    except Exception:
        pass
    return ""


def serve(host: str = "127.0.0.1", port: int = 8765, out_root: str = "results",
          model: str = "qwen3-vl:4b-instruct", ollama: str = "http://127.0.0.1:11434", open_browser: bool = True,
          llm_model: str | None = None):
    import threading
    import webbrowser

    import uvicorn
    url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '::') else host}:{port}"
    running = already_running(host, port)
    if running is not None:
        if not running:
            raise SystemExit(f"[!] Порт {port} занят другой программой. Закройте её или запустите с --port <номер>.")
        print(f"{APP_NAME} {running} уже запущен: {url} — открываю в браузере.")
        if open_browser:
            webbrowser.open(url)
        return
    print(f"{APP_NAME} {__version__}: веб-интерфейс на {url}  (остановить — Ctrl+C или закрыть это окно)")
    print(f"Результаты проверок: {Path(out_root).resolve()}")
    if host not in ("127.0.0.1", "localhost"):
        print("[!] Интерфейс доступен другим компьютерам сети. Документы будут обрабатываться на этом компьютере.")
    if open_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run(create_app(out_root, model, ollama, llm_model), host=host, port=port, log_level="warning")
