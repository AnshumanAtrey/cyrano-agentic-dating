"""Render the finished demo run to static HTML in docs/ (GitHub Pages = the demo link).

    .venv/bin/python scripts/export.py /<repo-name> [LIVE_URL]

The first argument is the Pages sub-path (e.g. /cyrano for https://you.github.io/cyrano/); use "" for a root site.
"""
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["STATIC_EXPORT"] = "1"
os.environ["BASE_PATH"] = (sys.argv[1] if len(sys.argv) > 1 else "").rstrip("/")
if len(sys.argv) > 2:
    os.environ["LIVE_URL"] = sys.argv[2]

from fastapi.testclient import TestClient  # noqa: E402

from app import store  # noqa: E402
from app.main import app  # noqa: E402

OUT = os.path.join(ROOT, "docs")


def main():
    if os.path.exists(OUT):
        shutil.rmtree(OUT)
    c = TestClient(app)
    routes = ["/", "/rankings/", "/how/"] + [f"/p/{p['id']}/" for p in store.people()] + \
             [f"/arc/{a['id']}/" for a in store.arcs() if a["status"] == "done"]
    for r in routes:
        res = c.get(r)
        if res.status_code != 200:
            print("skip", r, res.status_code)
            continue
        path = os.path.join(OUT, r.strip("/"), "index.html")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w", encoding="utf-8").write(res.text)
    shutil.copytree(os.path.join(ROOT, "data", "photos"), os.path.join(OUT, "photos"))
    shutil.copytree(os.path.join(ROOT, "app", "static"), os.path.join(OUT, "static"))
    open(os.path.join(OUT, ".nojekyll"), "w").close()
    print(f"exported {len(routes)} pages -> docs/")


if __name__ == "__main__":
    main()
