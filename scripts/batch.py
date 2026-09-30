"""Batch demo run. Incremental and safe to re-run; finished work is skipped.

    .venv/bin/python scripts/batch.py scrape     # people.txt -> Apify -> data/raw + photos
    .venv/bin/python scripts/batch.py analyze    # every scraped person -> evidence-cited profile
    .venv/bin/python scripts/batch.py date [N]   # first dates (N per person, default 3), each grows into an arc
    .venv/bin/python scripts/batch.py all [N]
"""
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from app import brain, dating, llm, store  # noqa: E402
from app.scrape import ig_username, save_photo, scrape  # noqa: E402


def read_people() -> list[tuple[str, str]]:
    pairs = []
    for line in open(os.path.join(ROOT, "people.txt"), encoding="utf-8"):
        li = re.search(r"\S*linkedin\.com/in/[^\s,;]+", line)
        ig = re.search(r"\S*instagram\.com/[^\s,;]+", line)
        if li and ig:
            pairs.append((li.group(0), ig.group(0)))
    return pairs


def do_scrape():
    todo = []
    for li, ig in read_people():
        p = store.person(ig_username(ig))
        if not p or p["status"] in (None, "queued", "error"):
            todo.append((li, ig))
    print(f"scraping {len(todo)} people locally (no login)…", flush=True)
    if not todo:
        return
    got, errors = scrape(todo)
    os.makedirs(os.path.join(ROOT, "data", "raw"), exist_ok=True)
    for pid, raw in got.items():
        json.dump(raw, open(os.path.join(ROOT, "data", "raw", f"{pid}.json"), "w", encoding="utf-8"), ensure_ascii=False)
        photo = save_photo(pid, raw)
        store.upsert_person(pid, name=raw["instagram"].get("full_name") or raw["linkedin"].get("name") or pid, raw=raw,
                            li_url=raw["input"]["linkedin"], ig_url=raw["input"]["instagram"], status="scraped",
                            source="demo", error=None)
        print(f"  ✓ {pid}: {len(raw['instagram']['posts'])} posts, {len(raw['linkedin']['text'])} LinkedIn chars, photo={photo}", flush=True)
    for pid, err in errors.items():
        print(f"  ✗ {pid}: {err}", flush=True)
    json.dump(errors, open(os.path.join(ROOT, "data", "raw", "_failed.json"), "w"), indent=1)


def do_analyze():
    todo = [p["id"] for p in store.people(status=None) if p["status"] in ("scraped", "analyzing", "error")]
    print(f"analyzing {len(todo)}…", flush=True)

    def one(pid):
        try:
            pr = brain.analyze_person(pid)
            print(f"  ✓ {pid}: {pr.get('name')} — {pr.get('vibe')}", flush=True)
        except Exception as e:
            store.upsert_person(pid, status="error", error=str(e)[:300])
            print(f"  ✗ {pid}: {e}", flush=True)

    with ThreadPoolExecutor(5) as ex:
        list(ex.map(one, todo))


def do_dates(n: int):
    ids = [p["id"] for p in store.people()]
    pairs = dating.schedule(ids, per_person=n)
    live = [(r["a"], r["b"]) for r in store.arcs() if r["status"] != "done"]  # resume half-finished arcs
    pairs = live + [p for p in pairs if p not in live]
    print(f"{len(ids)} people -> {len(pairs)} relationships to run", flush=True)
    t0 = time.time()

    def one(xy):
        try:
            aid = dating.run_arc(*xy)
            d = dating.arc_payload(aid)
            print(f"  ✓ arc {aid}: {xy[0]} × {xy[1]} -> {len(d['dates'])} dates, {d['outcome_label']} "
                  f"[{int(time.time() - t0)}s]", flush=True)
        except Exception as e:
            print(f"  ✗ {xy}: {e}", flush=True)

    with ThreadPoolExecutor(int(os.environ.get("ARC_WORKERS", "6"))) as ex:
        list(ex.map(one, pairs))


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        s.reconfigure(encoding="utf-8", errors="replace")
    stage = sys.argv[1] if len(sys.argv) > 1 else "all"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    if stage in ("scrape", "all"):
        do_scrape()
    if stage in ("analyze", "all"):
        do_analyze()
    if stage in ("date", "all"):
        do_dates(n)
    print("llm calls:", llm.stats(), flush=True)
