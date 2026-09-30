"""Reading a person: a source pack, hard signals computed in plain Python, and one evidence-cited LLM analysis."""
from __future__ import annotations

import re
import statistics as st
from collections import Counter
from datetime import datetime

from . import store
from .llm import complete_json

TAGS = ["startups", "tech", "ai", "finance", "investing", "marketing", "content", "design", "art", "photography",
        "film", "music", "dance", "comedy", "theatre", "books", "writing", "fashion", "beauty", "fitness", "gym",
        "running", "yoga", "sports", "cricket", "football", "outdoors", "hiking", "travel", "food", "cooking",
        "coffee", "nightlife", "gaming", "anime", "cars", "mindfulness", "wellness", "education", "social-impact",
        "sustainability", "pets", "family", "public-speaking", "podcasts", "science", "history"]

GUARDRAILS = (
    "Use ONLY the two sources below (their LinkedIn and their Instagram). Never add outside knowledge, even if you "
    "recognise the person. Every claim must trace to a source. Mark guesses as inferences. Never infer or discuss "
    "religion, politics, health, sexuality, ethnicity, caste, or finances beyond what they publicly teach. "
    "Do not assume relationship status or orientation."
)

EMOJI = re.compile("[\U0001F1E6-\U0001F1FF\U0001F300-\U0001FAFF☀-➿⭐❤]")
HINGLISH = set("hai hain nahi nahin kya bhi yaar bhai accha acha achha karo raha rahi mein tum aap hum tha thi kuch "
               "bahut bohot sab abhi matlab haan toh wala wali ekdum mast chalo dekho kaise kyun arre bas zindagi dost "
               "pyaar dil sapne bilkul shukriya".split())


def _date(ts) -> str:
    return str(ts)[:10] if ts else "?"


def source_pack(raw: dict) -> tuple[str, dict]:
    """Text the analysis agent reads, plus refs so citations (P1.., LI, BIO) can link back to the real source."""
    ig, li = raw["instagram"], raw["linkedin"]
    refs = {"BIO": ig["url"], "LI": li["url"]}
    lines = []
    for i, p in enumerate(ig["posts"][:12], 1):
        refs[f"P{i}"] = p.get("url")
        meta = [_date(p.get("ts")), p.get("type") or "post"] + ([f"{p['likes']} likes"] if p.get("likes") is not None else [])
        if p.get("location"):
            meta.append(f"at {p['location']}")
        people = sorted(set((p.get("tagged") or []) + (p.get("mentions") or [])))
        if people:
            meta.append("with @" + ", @".join(people[:6]))
        line = f"P{i} [{' · '.join(str(m) for m in meta)}] {(p.get('caption') or '').replace(chr(10), ' ')[:420]}"
        if p.get("alt"):
            line += f"  (image: {p['alt'][:160]})"
        lines.append(line)
    text = (
        f"=== SOURCE 1: INSTAGRAM @{ig['username']} ({ig.get('full_name')}) · {ig.get('followers')} followers · "
        f"{ig.get('posts_count')} posts · category: {ig.get('category') or 'none'} ===\n"
        f"BIO (cite as BIO): {ig.get('bio') or '(empty)'}\nLINK: {ig.get('external_url') or '-'}\n"
        "POSTS, newest first (cite as P1, P2, ...):\n" + "\n".join(lines) +
        f"\n\n=== SOURCE 2: LINKEDIN (cite as LI) ===\nName: {li.get('name')}\nHeadline: {li.get('headline')}\n"
        f"Location: {li.get('location')}\n{li.get('text', '')[:8000]}"
    )
    return text, refs


def signals(raw: dict) -> dict:
    """Numbers measured straight from the posts — no model involved."""
    ig = raw["instagram"]
    posts = ig["posts"]
    caps = [p.get("caption") or "" for p in posts]
    words = [len(c.split()) for c in caps]
    toks = [re.findall(r"[a-z]+", c.lower()) for c in caps]
    tags = Counter(h.lower() for p in posts for h in p.get("hashtags") or [])
    circle = Counter(u.lower() for p in posts for u in (p.get("tagged") or []) + (p.get("mentions") or [])
                     if u and u.lower() != ig["username"])
    places = Counter(p["location"] for p in posts if p.get("location"))
    stamps = sorted(datetime.fromisoformat(str(p["ts"]).replace("Z", "+00:00")) for p in posts if p.get("ts"))
    months = max(1.0, (stamps[-1] - stamps[0]).days / 30.4) if len(stamps) > 1 else 1.0
    has_caps = any(c.strip() for c in caps)
    blank = lambda v: v if has_caps else None
    return {
        "posts_read": len(posts),
        "words_per_caption": blank(round(st.mean(words), 1) if words else 0),
        "emoji_per_post": blank(round(sum(len(EMOJI.findall(c)) for c in caps) / max(1, len(caps)), 1)),
        "hinglish_share": blank(round(sum(1 for t in toks if HINGLISH & set(t)) / max(1, len(toks)), 2)),
        "questions_per_post": blank(round(sum(c.count("?") for c in caps) / max(1, len(caps)), 1)),
        "posts_per_month": round(len(stamps) / months, 1),
        "video_share": round(sum(1 for p in posts if (p.get("type") or "").lower() in ("video", "reel", "clips")) / max(1, len(posts)), 2),
        "avg_likes": int(st.mean([p["likes"] for p in posts if isinstance(p.get("likes"), int)] or [0])),
        "top_hashtags": [t for t, _ in tags.most_common(6)],
        "circle": [u for u, _ in circle.most_common(6)],
        "circle_size": len(circle),
        "places": [p for p, _ in places.most_common(5)],
        "linkedin_chars": len(raw["linkedin"].get("text") or ""),
    }


SCHEMA = """{
 "name": "full name", "headline": "what they do, one line", "location": "city, country or unknown",
 "age_hint": "only if the sources state or clearly imply it, else null", "pronouns": "only if stated, else null",
 "vibe": "punchy 8-12 word dating-profile tagline, in their spirit",
 "summary": "3 sentences: who they are, what drives them, what they're like to be around",
 "needs": [{"text": "what they need from a partner", "why": "reasoning", "evidence": "short quote or fact", "src": "P3|LI|BIO"}],
 "hobbies": [{"text": "...", "evidence": "...", "src": "..."}],
 "interests": [{"text": "...", "evidence": "...", "src": "..."}],
 "values": [{"text": "...", "evidence": "...", "src": "..."}],
 "qualities": [{"text": "other notable quality", "evidence": "...", "src": "..."}],
 "personal_facts": [{"text": "hometown / birthday month / languages / pets / alma mater — ONLY if directly in the sources", "evidence": "...", "src": "..."}],
 "personality": {"openness": 0, "conscientiousness": 0, "extraversion": 0, "agreeableness": 0, "emotional_stability": 0, "notes": "why, citing evidence"},
 "energy": "introvert|ambivert|extrovert",
 "voice": {"style": "register, humour, emoji, language mix", "quirks": ["2-4 verbal habits"], "samples": ["3 short verbatim phrases they wrote"]},
 "lifestyle": {"pace": "...", "social": "...", "travel": "...", "fitness": "...", "work": "..."},
 "ambitions": ["..."],
 "love_language": {"guess": "words|acts|gifts|time|touch", "why": "evidence (inference)"},
 "green_flags": ["..."], "friction": ["honest but kind"], "dealbreakers": ["inferred"],
 "looking_for": "2 sentences: the partner who would suit them",
 "tags": ["6-12 items from the TAG LIST only"],
 "confidence": "what the sources don't show, and how sure you are"
}"""


def analyze(raw: dict, sig: dict) -> dict:
    pack, refs = source_pack(raw)
    system = ("You are the personal dating agent for one real person. Before you date on their behalf, read their "
              "LinkedIn and Instagram like a perceptive friend: what they post, how they write, what they celebrate, "
              "where they go, who they show up with, what they work on and are proud of. " + GUARDRAILS)
    user = (f"{pack}\n\nMEASURED SIGNALS (computed from the posts): {sig}\n\nTAG LIST: {', '.join(TAGS)}\n\n"
            "Write the analysis with exactly this JSON shape (4-6 needs, 4-8 hobbies, 5-8 interests, 3-5 values, "
            "3-5 qualities, 0-5 personal_facts). Personality numbers are 0-100. Cite every item's src.\n" + SCHEMA)
    pr = complete_json(system, user, max_tokens=3500, temperature=0.4, heavy=True)
    for key in ("needs", "hobbies", "interests", "values", "qualities", "personal_facts"):
        items = []
        for it in pr.get(key) or []:
            if isinstance(it, dict) and it.get("text"):
                src = str(it.get("src") or "").upper().strip()
                it["url"] = refs.get(src)
                it["src"] = "in" if src == "LI" else "IG"
                items.append(it)
        pr[key] = items
    pr["tags"] = [t for t in (pr.get("tags") or []) if t in TAGS][:12]
    return pr


def analyze_person(pid: str) -> dict:
    p = store.person(pid, raw=True)
    store.upsert_person(pid, status="analyzing")
    sig = signals(p["raw"])
    pr = analyze(p["raw"], sig)
    if not pr.get("name"):
        raise RuntimeError("analysis came back empty")
    store.upsert_person(pid, profile=pr, signals=sig, name=pr["name"], status="ready", error=None)
    return pr
