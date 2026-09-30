"""Agentic dating site. Run:  .venv/bin/uvicorn app.main:app --port 8000"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import brain, dating, llm, store
from .scrape import PHOTOS, ScrapeError, ig_username, li_url, save_photo, scrape

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_NAME = os.environ.get("APP_NAME", "Cyrano")
LIVE_MAX_DATES = int(os.environ.get("LIVE_MAX_DATES", "2"))  # keeps a live add to ~3 minutes
_live_jobs = threading.Semaphore(2)

app = FastAPI(title=APP_NAME)
app.mount("/static", StaticFiles(directory=os.path.join(ROOT, "app", "static")), name="static")
T = Jinja2Templates(directory=os.path.join(ROOT, "app", "templates"))
T.env.globals.update(APP=APP_NAME, MOMENTS=dating.MOMENTS, OUTCOMES=dating.OUTCOMES,
                     VENUES_JS=[{k: v[k] for k in ("id", "name", "emoji", "tier", "x", "y")} for v in dating.VENUES.values()])


def page(request: Request, name: str, **ctx):
    ctx.update(B=os.environ.get("BASE_PATH", ""), STATIC=os.environ.get("STATIC_EXPORT") == "1",
               LIVE_URL=os.environ.get("LIVE_URL", ""))
    return T.TemplateResponse(request, name, ctx)


def arc_cards(pid: str | None = None) -> list[dict]:
    out = []
    for r in store.arcs(pid):
        ds = store.dates(r["id"])
        if not ds:
            continue
        out.append({"id": r["id"], "a": r["a"], "b": r["b"], "status": r["status"], "match": r["outcome"] == "match",
                    "outcome": dating.OUTCOMES.get(r["outcome"] or "", "Dating…"), "n": len(ds), "days": ds[-1]["day"],
                    "route": [dating.VENUES[d["venue"]]["emoji"] for d in ds]})
    return out


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    ppl = store.people()
    arcs = arc_cards()
    by_id = {p["id"]: p for p in ppl}
    arcs = [a for a in arcs if a["a"] in by_id and a["b"] in by_id]
    n_dates = sum(a["n"] for a in arcs)
    return page(request, "home.html", people=ppl, arcs=arcs, by_id=by_id, n_dates=n_dates,
                n_match=sum(1 for a in arcs if a["match"]))


@app.get("/p/{pid}/", response_class=HTMLResponse)
def profile(request: Request, pid: str):
    p = store.person(pid, raw=True)
    if not p:
        raise HTTPException(404, "No such person")
    if p["status"] != "ready":
        return page(request, "pending.html", p=p)
    by_id = {x["id"]: x for x in store.people()}
    return page(request, "profile.html", p=p, pr=p["profile"], sig=p["signals"] or {}, raw=p["raw"],
                arcs=arc_cards(pid), ranking=dating.rankings().get(pid, []), by_id=by_id)


@app.get("/arc/{aid}/", response_class=HTMLResponse)
def arc_page(request: Request, aid: int):
    data = dating.arc_payload(aid)
    if not data:
        raise HTTPException(404, "No such date")
    return page(request, "arc.html", data=data)


@app.get("/rankings/", response_class=HTMLResponse)
def rankings(request: Request):
    ppl = store.people()
    people = {p["id"]: {"name": p["profile"].get("name"), "vibe": p["profile"].get("vibe")} for p in ppl}
    return page(request, "rankings.html", ranks=dating.rankings(), people=people)


@app.get("/how/", response_class=HTMLResponse)
def how(request: Request):
    return page(request, "how.html")


@app.get("/job/{job}/", response_class=HTMLResponse)
def job_page(request: Request, job: str):
    return page(request, "job.html", job=job)


@app.get("/photos/{pid}.jpg")
def photo(pid: str):
    path = os.path.join(PHOTOS, f"{os.path.basename(pid)}.jpg")
    if not os.path.exists(path):
        raise HTTPException(404)
    return FileResponse(path, media_type="image/jpeg")


# ------------------------------------------------------------------ live: someone pastes their two links
def add_job(job: str, li: str, ig: str):
    emit = lambda kind, data: store.emit(job, kind, data)
    if not _live_jobs.acquire(blocking=False):
        emit("log", {"msg": "Another agent is being built right now; you're next in line…"})
        _live_jobs.acquire()
    try:
        pid = ig_username(ig)
        emit("step", {"step": "scrape", "msg": f"Reading instagram.com/{pid} and the LinkedIn profile (real browser, no login)"})
        got, errors = scrape([(li, ig)], log=lambda m: emit("log", {"msg": m}))
        if pid not in got:
            raise ScrapeError(errors.get(pid) or next(iter(errors.values()), "Could not read those profiles"))
        raw = got[pid]
        save_photo(pid, raw)
        store.upsert_person(pid, name=raw["instagram"].get("full_name") or raw["linkedin"].get("name") or pid, raw=raw,
                            li_url=raw["input"]["linkedin"], ig_url=raw["input"]["instagram"], status="scraped",
                            source="live", error=None)
        emit("step", {"step": "analyze", "msg": f"The agent is reading {len(raw['instagram']['posts'])} posts and the LinkedIn profile"})
        pr = brain.analyze_person(pid)
        emit("profile", {"pid": pid, "name": pr.get("name"), "vibe": pr.get("vibe")})
        emit("step", {"step": "match", "msg": f"Scoring {pr.get('name')} against everyone already here (plain math, instant)"})
        pairs = dating.schedule([p["id"] for p in store.people()], per_person=3, focus=pid)
        emit("step", {"step": "dates", "msg": f"Going on {len(pairs)} first dates, live", "pairs": pairs})
        with ThreadPoolExecutor(3) as ex:
            list(ex.map(lambda xy: dating.run_arc(xy[0], xy[1], max_dates=LIVE_MAX_DATES, emit=emit), pairs))
        emit("step", {"step": "rank", "msg": "Ranking everyone for you"})
        emit("done", {"pid": pid})
    except Exception as e:
        traceback.print_exc()
        emit("error", {"msg": str(e)})
    finally:
        _live_jobs.release()


@app.post("/api/add")
async def api_add(request: Request):
    body = await request.json()
    li, ig = str(body.get("linkedin") or "").strip(), str(body.get("instagram") or "").strip()
    try:
        pid, _ = ig_username(ig), li_url(li)
    except ScrapeError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    existing = store.person(pid)
    if existing and existing["status"] == "ready" and not body.get("force"):
        return {"redirect": f"/p/{pid}/", "existing": True}
    job = uuid.uuid4().hex[:10]
    threading.Thread(target=add_job, args=(job, li, ig), daemon=True).start()
    return {"job": job}


@app.get("/api/jobs/{job}/events")
async def job_events(job: str, after: int = 0):
    async def gen():
        last, idle = after, 0
        while idle < 2400:
            evs = store.events(job, last)
            for e in evs:
                last = e["id"]
                yield f"id: {e['id']}\ndata: {json.dumps(e, ensure_ascii=False)}\n\n"
                if e["kind"] in ("done", "error"):
                    return
            idle = 0 if evs else idle + 1
            if not evs and idle % 30 == 0:
                yield ": keepalive\n\n"
            await asyncio.sleep(0.5)
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/arc/{aid}")
def api_arc(aid: int):
    data = dating.arc_payload(aid)
    if not data:
        raise HTTPException(404)
    return data


@app.get("/api/status")
def status():
    return {"llm_lanes": [l.name for l in llm.lanes()], "llm_calls": llm.stats(), "people": len(store.people()),
            "arcs": len(store.arcs())}
