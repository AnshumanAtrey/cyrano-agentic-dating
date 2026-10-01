"""Scrape public Instagram + LinkedIn locally: no login, no cookies, no paid API.

Instagram: the mobile web_profile_info endpoint -> profile + 12 latest posts (captions, tags, locations, likes).
LinkedIn:  the public profile page -> JSON-LD Person (titles, companies, education, about) + their posts + page text.
"""
from __future__ import annotations

import html
import json
import os
import re
import threading
import time
from datetime import datetime, timezone

import requests

from . import llm  # noqa: F401  (loads .env)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PHOTOS = os.path.join(ROOT, "data", "photos")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"


class ScrapeError(RuntimeError):
    pass


def ig_username(s: str) -> str:
    s = s.strip().lstrip("@")
    m = re.search(r"instagram\.com/([A-Za-z0-9._]+)", s)
    user = (m.group(1) if m else s).strip("/").lower()
    if not re.fullmatch(r"[a-z0-9._]{1,30}", user) or user in {"p", "reel", "reels", "stories", "explore"}:
        raise ScrapeError(f"Not an Instagram profile link: {s}")
    return user


def li_url(s: str) -> str:
    m = re.search(r"linkedin\.com/in/([^/?#\s]+)", s.strip())
    if not m:
        raise ScrapeError(f"Not a LinkedIn profile link (needs linkedin.com/in/...): {s}")
    return f"https://www.linkedin.com/in/{requests.utils.unquote(m.group(1)).strip('/')}/"


def _slug(url: str) -> str:
    m = re.search(r"linkedin\.com/in/([^/?#\s]+)", url or "")
    return requests.utils.unquote(m.group(1)).strip("/").lower() if m else (url or "").lower()


# ------------------------------------------------------------------ Instagram (local, no login)
IG_MOBILE_UA = ("Instagram 219.0.0.12.117 Android (31/12; 420dpi; 1080x2400; samsung; SM-G991B; o1s; exynos2100; "
                "en_US; 346138365)")
_ig_lock = threading.Lock()


def _ig_apify(user: str) -> dict:
    """On a cloud host Instagram blocks the server's IP, so Apify's Instagram actor (residential proxies) reads it."""
    r = requests.post("https://api.apify.com/v2/acts/apify~instagram-profile-scraper/run-sync-get-dataset-items",
                      params={"token": os.environ["APIFY_TOKEN"], "timeout": 120}, json={"usernames": [user]}, timeout=180)
    items = r.json() if r.status_code < 400 else []
    it = next((i for i in items if isinstance(i, dict) and (i.get("username") or "").lower() == user), None)
    if not it:
        raise ScrapeError(f"Instagram @{user} not found")
    posts = [{"caption": p.get("caption") or "", "ts": p.get("timestamp"), "type": p.get("type"), "likes": p.get("likesCount"),
              "comments": p.get("commentsCount"), "url": p.get("url"), "alt": p.get("alt"), "location": p.get("locationName"),
              "hashtags": p.get("hashtags") or [], "mentions": p.get("mentions") or [], "owner": p.get("ownerUsername"),
              "tagged": [t.get("username") for t in p.get("taggedUsers") or [] if isinstance(t, dict) and t.get("username")]}
             for p in it.get("latestPosts") or []]
    return {"username": user, "full_name": it.get("fullName"), "bio": it.get("biography"), "followers": it.get("followersCount"),
            "following": it.get("followsCount"), "posts_count": it.get("postsCount"), "private": bool(it.get("private")),
            "verified": it.get("verified"), "category": it.get("businessCategoryName"), "external_url": it.get("externalUrl"),
            "pic": it.get("profilePicUrlHD") or it.get("profilePicUrl"), "pronouns": None,
            "url": f"https://www.instagram.com/{user}/", "posts": posts[:12]}


def scrape_instagram_one(user: str) -> dict:
    """Mobile web_profile_info first (profile + 12 posts with captions); if Instagram throttles it,
    a real logged-out browser reads the profile JSON embedded in the page plus the 12-post grid.
    With APIFY_TOKEN set (cloud deploys), Apify's Instagram actor goes first."""
    if os.environ.get("APIFY_TOKEN"):
        try:
            return _ig_apify(user)
        except (ScrapeError, requests.RequestException, ValueError) as e:
            if "not found" in str(e):
                raise
    try:
        return _ig_mobile(user)
    except ScrapeError as e:
        if "not found" in str(e):
            raise
        return scrape_instagram_browser([user])[user]


def _ig_mobile(user: str) -> dict:
    with _ig_lock:  # one at a time, politely spaced
        r = requests.get("https://i.instagram.com/api/v1/users/web_profile_info/", params={"username": user}, timeout=30,
                         headers={"User-Agent": IG_MOBILE_UA, "x-ig-app-id": "567067343352427", "Accept-Language": "en-US"})
        time.sleep(1.2)
    if r.status_code == 404:
        raise ScrapeError(f"Instagram @{user} not found")
    if r.status_code != 200:
        raise ScrapeError(f"Instagram answered {r.status_code} for @{user} (rate limit?)")
    u = (r.json().get("data") or {}).get("user")
    if not u:
        raise ScrapeError(f"Instagram @{user} not found")
    posts = []
    for e in (u.get("edge_owner_to_timeline_media") or {}).get("edges", []):
        n = e.get("node") or {}
        cap = ((n.get("edge_media_to_caption") or {}).get("edges") or [{}])[0].get("node", {}).get("text", "")
        ts = n.get("taken_at_timestamp")
        posts.append({
            "caption": cap, "ts": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else None,
            "type": "Video" if n.get("is_video") else ("Sidecar" if n.get("__typename") == "GraphSidecar" else "Image"),
            "likes": (n.get("edge_liked_by") or n.get("edge_media_preview_like") or {}).get("count"),
            "comments": (n.get("edge_media_to_comment") or {}).get("count"),
            "url": f"https://www.instagram.com/p/{n.get('shortcode')}/", "alt": n.get("accessibility_caption"),
            "location": (n.get("location") or {}).get("name"), "hashtags": re.findall(r"#(\w+)", cap or ""),
            "mentions": re.findall(r"@([A-Za-z0-9._]+)", cap or ""), "owner": (n.get("owner") or {}).get("username"),
            "tagged": [t["node"]["user"]["username"] for t in (n.get("edge_media_to_tagged_user") or {}).get("edges", [])
                       if (t.get("node") or {}).get("user")],
        })
    return {
        "username": (u.get("username") or user).lower(), "full_name": u.get("full_name"), "bio": u.get("biography"),
        "followers": (u.get("edge_followed_by") or {}).get("count"), "following": (u.get("edge_follow") or {}).get("count"),
        "posts_count": (u.get("edge_owner_to_timeline_media") or {}).get("count"), "private": bool(u.get("is_private")),
        "verified": u.get("is_verified"), "category": u.get("category_name"), "external_url": u.get("external_url"),
        "pic": u.get("profile_pic_url_hd") or u.get("profile_pic_url"), "pronouns": u.get("pronouns"),
        "url": f"https://www.instagram.com/{(u.get('username') or user).lower()}/", "posts": posts,
    }


_MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                        "september", "october", "november", "december"], 1)}


def _find_user(blocks: list[str], user: str) -> dict | None:
    best = None

    def walk(o, d=0):
        nonlocal best
        if d > 18 or not isinstance(o, (dict, list)):
            return
        if isinstance(o, dict):
            if str(o.get("username", "")).lower() == user and ("biography" in o or "follower_count" in o):
                if best is None or len(o) > len(best):
                    best = o
            for v in o.values():
                walk(v, d + 1)
        else:
            for v in o:
                walk(v, d + 1)
    for t in blocks:
        try:
            walk(json.loads(t))
        except ValueError:
            pass
    return best


def _ig_browser_one(page, user: str) -> dict:
    page.goto(f"https://www.instagram.com/{user}/", wait_until="domcontentloaded", timeout=25000)
    page.wait_for_timeout(3500)
    blocks = page.eval_on_selector_all('script[type="application/json"]', "els => els.map(e => e.textContent || '')")
    u = _find_user(blocks, user)
    if not u:
        text = page.evaluate("() => (document.body && document.body.innerText || '').slice(0, 300)")
        if re.search(r"isn't available|Sorry, this page|Page Not Found", text or "", re.I):
            raise ScrapeError(f"Instagram @{user} not found")
        raise ScrapeError(f"Instagram @{user}: profile data did not load")
    grid = page.eval_on_selector_all('a[href*="/p/"], a[href*="/reel/"]',
                                     "els => els.map(a => ({href: a.getAttribute('href'), alt: (a.querySelector('img') || {}).alt || ''}))")
    posts, seen = [], set()
    for g in grid:
        m = re.search(r"/(p|reel)/([^/?#]+)", g["href"] or "")
        if not m or m.group(2) in seen:
            continue
        seen.add(m.group(2))
        alt = g["alt"] or ""
        d = re.search(r" on ([A-Za-z]+) (\d{1,2}), (\d{4})", alt)
        ts = (f"{d.group(3)}-{_MONTHS.get(d.group(1).lower(), 1):02d}-{int(d.group(2)):02d}T12:00:00+00:00" if d else None)
        posts.append({"caption": "", "ts": ts, "type": "Video" if m.group(1) == "reel" or alt.startswith("Video") else "Image",
                      "likes": None, "comments": None, "url": f"https://www.instagram.com/p/{m.group(2)}/", "alt": alt,
                      "location": None, "hashtags": [], "mentions": [], "tagged": [], "owner": user})
    pic = (u.get("hd_profile_pic_url_info") or {}).get("url") or u.get("profile_pic_url_hd") or u.get("profile_pic_url")
    links = [l.get("url") for l in u.get("bio_links") or [] if isinstance(l, dict) and l.get("url")]
    return {"username": user, "full_name": u.get("full_name"), "bio": u.get("biography"),
            "followers": u.get("follower_count") or (u.get("edge_followed_by") or {}).get("count"),
            "following": u.get("following_count") or (u.get("edge_follow") or {}).get("count"),
            "posts_count": u.get("media_count") or u.get("all_media_count"), "private": bool(u.get("is_private")),
            "verified": u.get("is_verified"), "category": u.get("category") or u.get("category_name"),
            "external_url": u.get("external_url") or (links[0] if links else None), "pic": pic, "pronouns": u.get("pronouns"),
            "url": f"https://www.instagram.com/{user}/", "posts": posts[:12]}


def scrape_instagram_browser(users: list[str], workers: int = 3) -> dict:
    """Logged-out Chromium, a few browsers in parallel. Returns {user: data | ScrapeError}."""
    from playwright.sync_api import sync_playwright
    out, todo, lock = {}, list(users), threading.Lock()

    def worker():
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
            ctx = browser.new_context(user_agent=UA, locale="en-US", viewport={"width": 1280, "height": 900})
            while True:
                with lock:
                    if not todo:
                        break
                    user = todo.pop(0)
                page = ctx.new_page()
                try:
                    out[user] = _ig_browser_one(page, user)
                except Exception as e:
                    out[user] = e if isinstance(e, ScrapeError) else ScrapeError(f"Instagram @{user}: {str(e)[:120]}")
                finally:
                    page.close()
            browser.close()
    threads = [threading.Thread(target=worker) for _ in range(max(1, min(workers, len(users))))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    if len(users) == 1 and isinstance(out.get(users[0]), Exception):
        raise out[users[0]]
    return out


# ------------------------------------------------------------------ LinkedIn (local, no login)
_li_lock = threading.Lock()


def scrape_linkedin_one(url: str) -> dict:
    try:
        with _li_lock:
            data = _li_direct(url)
            time.sleep(1.0)
        return data
    except (ScrapeError, requests.RequestException):
        return _li_jina(url)


def _li_jina(url: str) -> dict:
    """Jina Reader fetches the same public page from its own browsers and returns it as markdown."""
    r = requests.get(f"https://r.jina.ai/{url}", timeout=60, headers={"Accept": "text/plain"})
    if r.status_code != 200 or "Markdown Content" not in r.text:
        raise ScrapeError(f"LinkedIn {url} could not be read ({r.status_code})")
    title = re.search(r"^Title: (.+)$", r.text, re.M)
    t = (title.group(1) if title else "").split(" | LinkedIn")[0]
    name, _, headline = t.partition(" - ")
    body = r.text.split("Markdown Content:", 1)[1]
    body = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", body)
    body = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", body)
    lines, seen = [], set()
    for ln in body.splitlines():
        ln = ln.strip(" *#-\t")
        if len(ln) > 2 and ln not in seen and not re.search(r"Sign in|Join now|Cookie|Password|Email or phone|Forgot password|User Agreement|Privacy Policy|Skip to main|Top Content|^Jobs$|^Games$|^People$|^Learning$", ln):
            seen.add(ln)
            lines.append(ln)
    if len(lines) < 5:
        raise ScrapeError(f"LinkedIn {url} came back empty")
    return {"url": url, "name": name.strip() or None, "headline": headline.strip() or None, "location": None, "photo": None,
            "text": ("\n".join([f"Name: {name}", f"Headline: {headline}", *lines]))[:9000]}


def _li_direct(url: str) -> dict:
    """The public profile page (what search engines see): JSON-LD Person + their posts + the page text."""
    r = requests.get(url, timeout=30, allow_redirects=True,
                     headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml", "Accept-Language": "en-US,en;q=0.9"})
    if r.status_code != 200 or "authwall" in r.url or "/login" in r.url:
        raise ScrapeError(f"LinkedIn answered {r.status_code} for {url}")
    page = r.text
    person, posts = {}, []
    for m in re.finditer(r'<script type="application/ld\+json">(.*?)</script>', page, re.S):
        try:
            j = json.loads(m.group(1))
        except ValueError:
            continue
        for it in j.get("@graph", [j]) if isinstance(j, dict) else []:
            t = it.get("@type")
            if t == "Person" and not person:
                person = it
            elif t in ("Article", "DiscussionForumPosting", "SocialMediaPosting"):
                body = it.get("articleBody") or it.get("text") or it.get("headline") or it.get("name") or ""
                if body:
                    posts.append(html.unescape(str(body))[:500])
    if not person:
        raise ScrapeError(f"LinkedIn profile {url} could not be read")
    names = lambda xs: [x.get("name", "").strip() for x in (xs if isinstance(xs, list) else [xs]) if isinstance(x, dict) and x.get("name")]
    title = re.search(r"<title>([^<]+)</title>", page)
    headline = html.unescape(title.group(1)).split(" | LinkedIn")[0].split(" - ", 1)[-1] if title else None
    body = re.sub(r"(?s)<script.*?</script>|<style.*?</style>|<code.*?</code>", " ", page)
    lines, seen = [], set()
    for ln in html.unescape(re.sub(r"<[^>]+>", "\n", body)).splitlines():
        ln = ln.strip()
        if len(ln) > 2 and ln not in seen and not re.search(r"Sign in|Join now|Cookie|Password|Email or phone|Forgot password|User Agreement|Privacy Policy", ln):
            seen.add(ln)
            lines.append(ln)
    addr = person.get("address") or {}
    img = person.get("image")
    text = "\n".join([
        f"Name: {person.get('name')}", f"Headline: {headline}",
        f"Job titles: {', '.join(person.get('jobTitle') or []) if isinstance(person.get('jobTitle'), list) else person.get('jobTitle')}",
        f"Works for: {', '.join(names(person.get('worksFor', [])))}", f"Education: {', '.join(names(person.get('alumniOf', [])))}",
        f"About: {html.unescape(str(person.get('description') or ''))}",
        f"Languages: {person.get('knowsLanguage')}", f"Awards: {person.get('awards')}", f"Member of: {names(person.get('memberOf', []))}",
        "Their posts and activity:", *[f"- {p}" for p in posts[:12]], "Profile page text:", *lines[:220],
    ])
    return {"url": url, "name": person.get("name"), "headline": headline,
            "location": addr.get("addressLocality") if isinstance(addr, dict) else None,
            "photo": img.get("contentUrl") if isinstance(img, dict) else img, "text": text[:9000]}


# ------------------------------------------------------------------ public API
def scrape(pairs: list[tuple[str, str]], log=print) -> tuple[dict, dict]:
    """pairs of (linkedin_url, instagram_url) -> ({ig_username: raw}, {ig_username: error}). Local, no login."""
    from concurrent.futures import ThreadPoolExecutor
    norm, errors = [], {}
    for li, ig in pairs:
        try:
            norm.append((li_url(li), ig_username(ig)))
        except ScrapeError as e:
            errors[ig] = str(e)
    igs = {}
    if os.environ.get("APIFY_TOKEN"):  # cloud: Apify first, per person
        for _, u in norm:
            igs[u] = _safe(scrape_instagram_one, u)
    else:
        try:  # probe the fast endpoint once; if it's throttled, send everyone through the browser
            first = norm[0][1] if norm else None
            if first:
                igs[first] = _ig_mobile(first)
                for _, u in norm[1:]:
                    try:
                        igs[u] = _ig_mobile(u)
                    except ScrapeError as e:
                        igs[u] = e
        except ScrapeError:
            log("Instagram's mobile API is throttled here; reading profiles in a real logged-out browser")
            igs = scrape_instagram_browser([u for _, u in norm])
    retry = [u for u, v in igs.items() if isinstance(v, ScrapeError) and "not found" not in str(v)]
    if retry and len(retry) < len(norm):
        igs.update(scrape_instagram_browser(retry))
    with ThreadPoolExecutor(4) as ex:
        lis = dict(zip([l for l, _ in norm], ex.map(lambda l: _safe(scrape_linkedin_one, l), [l for l, _ in norm])))
    out = {}
    for li, user in norm:
        g, ln = igs.get(user), lis.get(li)
        if isinstance(g, Exception) or g is None:
            errors[user] = str(g or f"Instagram @{user} not read")
        elif g["private"]:
            errors[user] = f"Instagram @{user} is private — only public profiles are allowed"
        elif isinstance(ln, Exception):
            errors[user] = str(ln)
        else:
            out[user] = {"instagram": g, "linkedin": ln, "input": {"linkedin": li, "instagram": g["url"]}}
            log(f"@{user}: {len(g['posts'])} posts, {g.get('followers')} followers · LinkedIn {len(ln['text'])} chars")
    for u, err in errors.items():
        log(f"@{u}: {err}")
    return out, errors


def _safe(fn, *a):
    try:
        return fn(*a)
    except Exception as e:
        return e if isinstance(e, ScrapeError) else ScrapeError(str(e)[:200])


def save_photo(pid: str, raw: dict) -> bool:
    os.makedirs(PHOTOS, exist_ok=True)
    for url in (raw["instagram"].get("pic"), raw["linkedin"].get("photo")):
        if not url:
            continue
        try:
            r = requests.get(url, timeout=30, headers={"User-Agent": UA})
            if r.status_code == 200 and len(r.content) > 1500:
                open(os.path.join(PHOTOS, f"{pid}.jpg"), "wb").write(r.content)
                return True
        except requests.RequestException:
            pass
    return False
