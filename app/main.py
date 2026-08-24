"""Zrcadlo - lokalni prekladac knih. HTTP vrstva.

Vse bezi na 127.0.0.1, ven z pocitace nejde nic.
"""
import json
import queue
import tempfile
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import cleanup, export, glossary, projects, runner, sysinfo, translate
from . import config
from .config import CFG, ROOT

STATIC = ROOT / "static"

app = FastAPI(title="Zrcadlo", docs_url=None, redoc_url=None)


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/status")
def status():
    lm = sysinfo.lm_studio(CFG["lm_studio"]["base_url"], CFG["lm_studio"]["model"])
    return {
        "model": CFG["lm_studio"]["model"],
        "context": CFG["lm_studio"]["context"],
        "lm_studio": lm,
        "gpu": sysinfo.gpu_memory(),
    }


@app.post("/api/model")
def api_set_model(model: str):
    """Prepne model. Vybira se z toho, co ma LM Studio nactene."""
    for slug, run in list(runner.RUNS.items()):
        if not run.finished:
            raise HTTPException(409, "Na projektu " + slug + " právě běží práce."
                                     " Model se dá přepnout, až doběhne.")
    lm = sysinfo.lm_studio(CFG["lm_studio"]["base_url"], CFG["lm_studio"]["model"])
    if not lm["ok"] and lm["reason"] in ("offline", "http"):
        raise HTTPException(409, lm["message"] + " " + lm["hint"])
    if model not in lm["models"]:
        raise HTTPException(400, "Model " + model +
                                 " není v LM Studiu k dispozici.")
    config.save({"lm_studio": {"model": model}})
    return {"model": CFG["lm_studio"]["model"]}


@app.get("/api/projects")
def api_projects():
    return {"projects": projects.list_projects()}


@app.post("/api/projects")
async def api_import(file: UploadFile = File(...)):
    name = file.filename or "kniha.epub"
    if not name.lower().endswith(".epub"):
        raise HTTPException(400, "Čekám soubor .epub.")
    tmp = Path(tempfile.gettempdir()) / ("zrcadlo-" + name)
    try:
        data = await file.read()
        tmp.write_bytes(data)
        slug = projects.import_epub(tmp, name)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, "EPUB se nepodařilo rozložit: " + str(exc))
    finally:
        tmp.unlink(missing_ok=True)
    return {"slug": slug, "book": projects.book_info(slug)}


@app.get("/api/projects/{slug}")
def api_project(slug: str):
    info = projects.book_info(slug, check_source=True)
    if info is None:
        raise HTTPException(404, "Projekt nenalezen.")
    return info


@app.patch("/api/projects/{slug}")
async def api_project_edit(slug: str, request: Request):
    """Stylova karta projektu. Jde do systemoveho promptu kazde davky."""
    fields = await request.json()
    if not isinstance(fields, dict):
        raise HTTPException(400, "Čekám objekt se změněnými poli.")
    info = projects.update_book(slug, fields)
    if info is None:
        raise HTTPException(404, "Projekt nenalezen.")
    return info


@app.get("/api/projects/{slug}/runs")
def api_runs(slug: str):
    history = projects.runs(slug)
    if history is None:
        raise HTTPException(404, "Projekt nenalezen.")
    return {"runs": history}


@app.get("/api/projects/{slug}/segments")
def api_segments(slug: str, chapter: int = None, offset: int = 0, limit: int = 0):
    segs = projects.segments(slug, chapter, offset, limit)
    if segs is None:
        raise HTTPException(404, "Projekt nenalezen.")
    return {"segments": segs}


@app.post("/api/projects/{slug}/translate")
def api_translate(slug: str, chapter: int = None):
    """Bez parametru chapter prelozi celou knihu od mista, kde skoncil."""
    info = projects.book_info(slug)
    if info is None:
        raise HTTPException(404, "Projekt nenalezen.")
    if chapter is not None and not any(c["ord"] == chapter for c in info["chapters"]):
        raise HTTPException(400, "Taková kapitola v knize není.")
    lm = sysinfo.lm_studio(CFG["lm_studio"]["base_url"], CFG["lm_studio"]["model"])
    if not lm["ok"] and lm["reason"] in ("offline", "http"):
        raise HTTPException(409, lm["message"] + " " + lm["hint"])
    run, fresh = translate.start(slug, chapter)
    if run.kind != "translate":
        raise HTTPException(409, "Na projektu právě běží sestavování slovníčku.")
    return {"started": fresh, "chapter": run.params.get("chapter"),
            "running": not run.finished}


@app.post("/api/projects/{slug}/stop")
def api_stop(slug: str):
    run = runner.active(slug)
    if run is None:
        return {"running": False}
    run.request_stop()
    return {"running": True, "stopping": True, "kind": run.kind}


def _sse(payload):
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


@app.get("/api/projects/{slug}/stream")
def api_stream(slug: str):
    # i dobehnuty beh, aby kratky beh neutekl driv, nez se okno pripoji
    run = runner.latest(slug)
    if run is None:
        return StreamingResponse(iter([_sse({"type": "idle"})]),
                                 media_type="text/event-stream")
    if run.finished:
        tail = [_sse(dict(run.state, type="progress")),
                _sse(dict(run.state, type="end",
                          status=run.final_status or "done",
                          translated=run.segments_done,
                          failed=run.segments_failed,
                          seconds=run.seconds))]
        return StreamingResponse(iter(tail), media_type="text/event-stream")

    def events():
        q = run.subscribe()
        try:
            yield _sse(dict(run.state, type="progress"))
            if run.finished:
                yield _sse(dict(run.state, type="end",
                                status=run.final_status or "done",
                                translated=run.segments_done,
                                failed=run.segments_failed))
                return
            while True:
                try:
                    event = q.get(timeout=15)
                except queue.Empty:
                    yield ": ping\n\n"
                    if run.finished:
                        yield _sse(dict(run.state, type="end",
                                        status=run.final_status or "done",
                                        translated=run.segments_done,
                                        failed=run.segments_failed))
                        return
                    continue
                yield _sse(event)
                if event.get("type") in ("end", "error"):
                    return
        finally:
            run.unsubscribe(q)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store",
                                      "X-Accel-Buffering": "no"})


@app.post("/api/projects/{slug}/export")
def api_export(slug: str, kind: str = "translation"):
    if projects.book_info(slug) is None:
        raise HTTPException(404, "Projekt nenalezen.")
    try:
        info = export.run(slug, kind)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, "Export se nepodařil: " + str(exc))
    if info is None:
        raise HTTPException(404, "Projekt nenalezen.")
    return info


@app.post("/api/projects/{slug}/recheck")
def api_recheck(slug: str, chapter: int = None):
    """Prepocita kontroly nad hotovym prekladem. Model se nevola."""
    if runner.active(slug) is not None:
        raise HTTPException(409, "Na projektu právě běží jiná práce.")
    result = translate.recheck(slug, chapter)
    if result is None:
        raise HTTPException(404, "Projekt nenalezen.")
    return result


@app.patch("/api/projects/{slug}/segments/{ord}")
async def api_segment_edit(slug: str, ord: int, request: Request):
    """Rucni uprava odstavce: opravit preklad, nebo ho vyradit z knihy."""
    fields = await request.json()
    if not isinstance(fields, dict):
        raise HTTPException(400, "Čekám objekt se změněnými poli.")
    seg = projects.update_segment(slug, ord, fields)
    if seg is None:
        raise HTTPException(404, "Takový odstavec v knize není.")
    return {"segment": seg}


@app.get("/api/projects/{slug}/cleanup")
def api_cleanup_scan(slug: str):
    """Najde tiskovy balast, ale nic nezmeni."""
    found = cleanup.scan(slug)
    if found is None:
        raise HTTPException(404, "Projekt nenalezen.")
    return found


@app.post("/api/projects/{slug}/cleanup")
def api_cleanup_apply(slug: str):
    if runner.active(slug) is not None:
        raise HTTPException(409, "Na projektu právě běží jiná práce.")
    result = cleanup.apply(slug)
    if result is None:
        raise HTTPException(404, "Projekt nenalezen.")
    return result


@app.post("/api/projects/{slug}/cleanup/restore")
def api_cleanup_restore(slug: str):
    if runner.active(slug) is not None:
        raise HTTPException(409, "Na projektu právě běží jiná práce.")
    result = cleanup.restore(slug)
    if result is None:
        raise HTTPException(404, "Projekt nenalezen.")
    return result


@app.post("/api/projects/{slug}/segments/{ord}/retranslate")
def api_segment_retranslate(slug: str, ord: int):
    """Jeden segment zpatky k prekladu. Pouziva se u stavu 'review'."""
    if runner.active(slug) is not None:
        raise HTTPException(409, "Na projektu právě běží jiná práce.")
    lm = sysinfo.lm_studio(CFG["lm_studio"]["base_url"], CFG["lm_studio"]["model"])
    if not lm["ok"] and lm["reason"] in ("offline", "http"):
        raise HTTPException(409, lm["message"] + " " + lm["hint"])
    chapter = projects.reset_segment(slug, ord)
    if chapter is None:
        raise HTTPException(404, "Takový odstavec v knize není.")
    run, fresh = translate.start(slug, chapter)
    return {"started": fresh, "chapter": chapter, "ord": ord}


@app.post("/api/projects/{slug}/glossary/build")
def api_glossary_build(slug: str):
    if projects.book_info(slug) is None:
        raise HTTPException(404, "Projekt nenalezen.")
    lm = sysinfo.lm_studio(CFG["lm_studio"]["base_url"], CFG["lm_studio"]["model"])
    if not lm["ok"] and lm["reason"] in ("offline", "http"):
        raise HTTPException(409, lm["message"] + " " + lm["hint"])
    run, fresh = glossary.start(slug)
    if run.kind != "glossary":
        raise HTTPException(409, "Na projektu právě běží překlad.")
    return {"started": fresh, "running": not run.finished}


@app.get("/api/projects/{slug}/glossary")
def api_glossary(slug: str):
    entries = glossary.list_entries(slug)
    if entries is None:
        raise HTTPException(404, "Projekt nenalezen.")
    return {"entries": entries}


@app.patch("/api/projects/{slug}/glossary/{entry_id}")
async def api_glossary_edit(slug: str, entry_id: int, request: Request):
    fields = await request.json()
    if not isinstance(fields, dict):
        raise HTTPException(400, "Čekám objekt se změněnými poli.")
    entry = glossary.update_entry(slug, entry_id, fields)
    if entry is None:
        raise HTTPException(404, "Položka slovníčku nenalezena.")
    return {"entry": entry}


@app.delete("/api/projects/{slug}/glossary/{entry_id}")
def api_glossary_delete(slug: str, entry_id: int):
    if not glossary.delete_entry(slug, entry_id):
        raise HTTPException(404, "Položka slovníčku nenalezena.")
    return {"deleted": True}


@app.post("/api/projects/{slug}/glossary/lock-all")
def api_glossary_lock_all(slug: str):
    if projects.book_info(slug) is None:
        raise HTTPException(404, "Projekt nenalezen.")
    return {"locked": glossary.lock_all(slug)}


@app.get("/api/projects/{slug}/glossary/{entry_id}/affected")
def api_glossary_affected(slug: str, entry_id: int):
    found = glossary.affected_segments(slug, entry_id)
    if found is None:
        raise HTTPException(404, "Položka slovníčku nenalezena.")
    return found


@app.post("/api/projects/{slug}/glossary/{entry_id}/retranslate")
def api_glossary_retranslate(slug: str, entry_id: int):
    if runner.active(slug) is not None:
        raise HTTPException(409, "Na projektu právě běží jiná práce.")
    result = glossary.mark_for_retranslation(slug, entry_id)
    if result is None:
        raise HTTPException(404, "Položka slovníčku nenalezena.")
    return result


@app.exception_handler(HTTPException)
def http_error(request, exc):
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)


app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
