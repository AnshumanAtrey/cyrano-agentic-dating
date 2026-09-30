"""The dating harness.

match (plain math) -> first dates -> multi-date arcs where the agents plan their own next date and remember
the last one -> private debriefs that tag the moments -> rankings.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
from concurrent.futures import ThreadPoolExecutor

from . import store
from .llm import complete_json

TURNS = int(os.environ.get("DATE_TURNS", "6"))  # messages per date, alternating
MAX_DATES = 3

# Date Town. Tier 1 = low-stakes first dates, 2 = shared activity, 3 = evening. x/y place them on the map (0-100).
VENUES = {v["id"]: v for v in [
    {"id": "cafe", "name": "Brew & Bloom Café", "emoji": "☕", "tier": 1, "x": 16, "y": 30, "tags": {"coffee", "books", "startups", "writing", "design", "tech", "ai"}},
    {"id": "park", "name": "Lakeside Park Walk", "emoji": "🌳", "tier": 1, "x": 33, "y": 72, "tags": {"outdoors", "hiking", "photography", "mindfulness", "pets", "wellness"}},
    {"id": "books", "name": "Dog-Ear Bookstore", "emoji": "📚", "tier": 1, "x": 8, "y": 58, "tags": {"books", "writing", "education", "history", "science"}},
    {"id": "arcade", "name": "Pixel Arcade", "emoji": "🎮", "tier": 1, "x": 47, "y": 20, "tags": {"gaming", "anime", "tech", "comedy", "cars"}},
    {"id": "streetfood", "name": "Night Market Street Food", "emoji": "🍜", "tier": 1, "x": 60, "y": 78, "tags": {"food", "travel", "cooking", "photography", "content"}},
    {"id": "runclub", "name": "Sunrise Run Club", "emoji": "🏃", "tier": 1, "x": 82, "y": 64, "tags": {"running", "fitness", "gym", "sports", "wellness", "yoga"}},
    {"id": "cinema", "name": "Reel House Cinema", "emoji": "🎬", "tier": 2, "x": 28, "y": 46, "tags": {"film", "anime", "comedy", "theatre", "writing", "content"}},
    {"id": "gallery", "name": "Canvas Gallery", "emoji": "🖼️", "tier": 2, "x": 53, "y": 48, "tags": {"art", "design", "photography", "fashion", "history"}},
    {"id": "market", "name": "Weekend Flea Market", "emoji": "🛍️", "tier": 2, "x": 71, "y": 34, "tags": {"fashion", "beauty", "design", "food", "sustainability", "marketing"}},
    {"id": "climb", "name": "Boulder Gym", "emoji": "🧗", "tier": 2, "x": 90, "y": 18, "tags": {"fitness", "outdoors", "gym", "sports", "hiking"}},
    {"id": "comedy", "name": "Open Mic Comedy Night", "emoji": "🎤", "tier": 2, "x": 42, "y": 88, "tags": {"comedy", "theatre", "music", "public-speaking", "podcasts", "content"}},
    {"id": "cricket", "name": "Stadium Match Night", "emoji": "🏏", "tier": 2, "x": 12, "y": 84, "tags": {"cricket", "football", "sports", "family"}},
    {"id": "dinner", "name": "Candlelight Dinner", "emoji": "🍷", "tier": 3, "x": 62, "y": 10, "tags": {"food", "cooking", "travel", "finance", "investing", "family"}},
    {"id": "rooftop", "name": "Skyline Rooftop Bar", "emoji": "🌆", "tier": 3, "x": 76, "y": 86, "tags": {"nightlife", "startups", "finance", "investing", "music", "marketing"}},
    {"id": "club", "name": "Neon Club", "emoji": "💃", "tier": 3, "x": 92, "y": 44, "tags": {"nightlife", "dance", "music", "fashion"}},
    {"id": "concert", "name": "Open-Air Concert", "emoji": "🎵", "tier": 3, "x": 24, "y": 12, "tags": {"music", "dance", "nightlife", "art"}},
    {"id": "promenade", "name": "Beach Promenade at Night", "emoji": "🌊", "tier": 3, "x": 4, "y": 20, "tags": {"outdoors", "travel", "photography", "mindfulness", "writing"}},
]}

MOMENTS = {"laugh": "😂", "common_ground": "🤝", "learned": "📌", "birthday": "🎂", "compliment": "💐",
           "vulnerable": "🫶", "inside_joke": "😏", "callback": "🔁", "plan": "📅", "spark": "✨", "ily": "💞",
           "friction": "⚡"}
OUTCOMES = {"one_date": "One date", "stopped_2": "Two dates", "stopped_3": "Three dates", "match": "💞 Wants to keep dating"}
ENERGY = {"introvert": 0, "ambivert": 1, "extrovert": 2}


def first(p: dict) -> str:
    return ((p.get("profile") or {}).get("name") or p.get("name") or p["id"]).split(" ")[0]


def _h(s: str) -> int:
    return int(hashlib.md5(s.encode()).hexdigest()[:6], 16)


def _words(items) -> set:
    return {w for x in items or [] for w in re.findall(r"[a-z]{4,}", str(x.get("text", "")).lower())}


def _city(pr: dict) -> str:
    return (pr.get("location") or "").split(",")[0].strip().lower()


# ------------------------------------------------------------------ 1. matching (no LLM)
def match(me: dict, them: dict) -> tuple[float, str]:
    """How well THEM fits ME. Shared tags and values, social energy, same city, and the partner traits
    (agreeableness, conscientiousness, emotional stability) that predict relationship satisfaction."""
    pa, pb = me["profile"], them["profile"]
    ta, tb = set(pa.get("tags") or []), set(pb.get("tags") or [])
    shared = ta & tb
    tag_fit = min(1.0, len(shared) / 4)
    val_fit = min(1.0, len(_words(pa.get("values")) & _words(pb.get("values"))) / 3)
    energy_fit = 1 - abs(ENERGY.get(pa.get("energy"), 1) - ENERGY.get(pb.get("energy"), 1)) / 2
    per = pb.get("personality") or {}
    num = lambda k: float(per.get(k) or 50) if str(per.get(k) or "").replace(".", "").isdigit() else 50.0
    partner = (num("agreeableness") + num("conscientiousness") + num("emotional_stability")) / 300
    same_city = 1.0 if _city(pa) and _city(pa) == _city(pb) and _city(pa) != "unknown" else 0.0
    score = 100 * (0.40 * tag_fit + 0.15 * val_fit + 0.15 * energy_fit + 0.20 * partner + 0.10 * same_city)
    reason = ("Both into " + ", ".join(sorted(shared)[:4])) if shared else "Opposites: little overlap on paper"
    return round(score, 1), reason


def schedule(ids: list[str], per_person: int = 3, focus: str | None = None) -> list[tuple[str, str]]:
    """First dates: best mutual match first, until everyone has `per_person` (or just `focus`, for a live add)."""
    ppl = {p["id"]: p for p in store.people()}
    ids = [i for i in ids if i in ppl]
    taken = {tuple(sorted((a["a"], a["b"]))) for a in store.arcs()}
    count = {i: 0 for i in ids}
    for a, b in taken:
        for x in (a, b):
            if x in count:
                count[x] += 1
    cand = []
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            if focus and focus not in (a, b):
                continue
            cand.append((math.sqrt(match(ppl[a], ppl[b])[0] * match(ppl[b], ppl[a])[0]), a, b))
    cand.sort(reverse=True)
    out = []
    for _, a, b in cand:
        key = tuple(sorted((a, b)))
        if key in taken:
            continue
        if focus:
            if count[focus] >= per_person:
                break
        elif count[a] >= per_person or count[b] >= per_person:
            continue
        out.append(key)
        taken.add(key)
        count[a] += 1
        count[b] += 1
    return out


def pick_venue(pa: dict, pb: dict, tier: int, visited: list[str]) -> tuple[str, str]:
    ta, tb = set(pa.get("tags") or []), set(pb.get("tags") or [])
    pool = [v for v in VENUES.values() if v["tier"] == tier and v["id"] not in visited] or list(VENUES.values())
    v = max(pool, key=lambda v: (2 * len(v["tags"] & ta & tb) + len(v["tags"] & (ta | tb)), _h(v["id"] + pa["name"] + pb["name"])))
    both = sorted(v["tags"] & ta & tb)
    return v["id"], (f"you both love {', '.join(both[:2])}" if both else "an easy, low-pressure place to meet")


# ------------------------------------------------------------------ 2. the agents
def card(p: dict) -> str:
    pr = p["profile"]
    names = lambda k, n: ", ".join(x["text"] for x in (pr.get(k) or [])[:n])
    return (f"{pr.get('name')} — {pr.get('headline')} ({pr.get('location')}). Vibe: {pr.get('vibe')}\n"
            f"Hobbies: {names('hobbies', 5)}. Interests: {names('interests', 5)}.\nLooking for: {pr.get('looking_for')}")


def brief(p: dict) -> str:
    pr = p["profile"]
    keep = ("name", "headline", "location", "summary", "needs", "hobbies", "interests", "values", "qualities",
            "personal_facts", "personality", "lifestyle", "ambitions", "love_language", "green_flags", "friction",
            "dealbreakers", "looking_for")
    return json.dumps({k: pr.get(k) for k in keep}, ensure_ascii=False)


def _voice(p: dict) -> str:
    pr, sig = p["profile"], p.get("signals") or {}
    v = pr.get("voice") or {}
    mix = " They mix Hindi and English." if (sig.get("hinglish_share") or 0) > 0.15 else ""
    return (f"{v.get('style')}. Quirks: {'; '.join(v.get('quirks') or [])}. Lines they really wrote: "
            f"{json.dumps(v.get('samples') or [], ensure_ascii=False)}. Measured: ~{sig.get('words_per_caption')} words "
            f"per caption, {sig.get('emoji_per_post')} emoji per post.{mix}")


def _persona(me: dict, them: dict, venue: dict, day: int, seq: int, why: str, memory: str) -> str:
    my, th = me["profile"], them["profile"]
    needs = "; ".join(x["text"] for x in (my.get("needs") or [])[:3])
    deal = "; ".join((my.get("dealbreakers") or [])[:3]) or "none known"
    return f"""You are the AI dating agent of {my['name']}. You are on date #{seq} with {th['name']}, on {first(me)}'s behalf.
Speak in first person AS {first(me)} (their AI twin), in their real voice.

VOICE: {_voice(me)}

YOUR PERSON (private; reveal only what they would naturally share on a date):
{brief(me)}

WHAT YOU KNEW ABOUT {first(them).upper()} BEFORE TONIGHT (their public card only):
{card(them)}
{memory}
SETTING: date {seq}, day {day} of getting to know each other: {venue['emoji']} {venue['name']} ({why}).

RULES
- Share only real facts from your person's brief: jobs, places, projects, stories. Never invent names, places, events or numbers.
  If asked something the brief doesn't cover, answer vaguely the way they plausibly would, or playfully redirect.
- You have a quiet agenda: find out if {first(them)} fits your person. Probe these needs: {needs}. Watch for: {deal}.
- Be a real person on a date: warm, curious, specific, a bit playful. React to what they just said. 1-3 sentences. PG.
- Don't people-please. If something clashes with your person's values or lifestyle, say so kindly. Not every date works.
- Always "I/my". Never talk about {first(me)} in the third person. Call your date {first(them)}."""


PHASES = ["opening: greet, react to the place, keep it light", "opening: warm up and find common ground",
          "deeper: values, work, what drives you — honest self-disclosure", "deeper: share something personal and true from your brief",
          "closing: start winding down naturally", "closing: say goodbye in your own way"]


def _turn_prompt(transcript: list, turn: int, seq: int, visited: list[str]) -> str:
    convo = "\n".join(f"{m['name']}: {('*' + m['action'] + '* ') if m.get('action') else ''}{m['say']}" for m in transcript)
    phase = PHASES[min(turn * len(PHASES) // TURNS, len(PHASES) - 1)]
    extra = ""
    if turn >= TURNS - 2 and seq < MAX_DATES:
        options = [f"{v['id']} ({v['emoji']} {v['name']})" for v in VENUES.values() if v["tier"] == seq + 1 and v["id"] not in visited]
        extra = (f'\nIf you genuinely want another date, propose one: set "propose" to {{"venue": "<id>", "in_days": 1-7}} using one of: '
                 f'{", ".join(options)}. Mention it naturally in "say". If you are not feeling it, leave "propose" null.')
    return (f"Conversation so far:\n{convo or '(you both just arrived; you speak first)'}\n\nPhase: {phase}.{extra}\n"
            "Stay in your person's voice and quirks. 1-3 sentences.\n"
            'Return JSON: {"action": "tiny stage direction or empty", "say": "what you say out loud", '
            '"thought": "PRIVATE one-sentence note to your person: how it is going / what you just learned", "propose": null}')


def _memory(arc_id: int, seq: int, key: str, them: dict) -> str:
    """What this agent remembers from earlier dates in the arc (its own private debriefs)."""
    notes = []
    for d in store.dates(arc_id):
        if d["seq"] >= seq or not d.get(key):
            continue
        v = d[key]
        learned = "; ".join(x.get("fact", "") for x in v.get("learned") or [] if isinstance(x, dict))
        notes.append(f"- Date {d['seq']} (day {d['day']}, {VENUES[d['venue']]['name']}): you rated it {v.get('overall')}/10. "
                     f"Best moment: {v.get('highlight')}. What you learned: {learned or 'n/a'}")
    if not notes:
        return ""
    return (f"\nYOUR MEMORY OF EARLIER DATES WITH {first(them).upper()}:\n" + "\n".join(notes) +
            "\nOpen with a natural callback to something from last time.\n")


def _debrief(me: dict, them: dict, venue: dict, seq: int, transcript: list, proposal) -> dict:
    numbered = "\n".join(f"[{m['i']}] {m['name']}: {m['say']}" for m in transcript)
    prop = (f"A next date was proposed: {VENUES[proposal['venue']]['name']} in {proposal['in_days']} days."
            if proposal else "No next date was proposed.")
    v = complete_json(
        f"You are the dating agent of {me['profile']['name']}. You know them deeply:\n{brief(me)}",
        f"""You just went on date #{seq} on their behalf with {them['profile']['name']} at {venue['name']}.
Transcript (message numbers in brackets):
{numbered}
{prop}

Report back honestly, judged against YOUR person's needs and dealbreakers, not general niceness.
Calibrate: most dates are a 4-6; 8+ means you'd tell your friends; below 4 is a clear mismatch. Chemistry must be earned by specific moments.
Return JSON:
{{"chemistry": 1-10, "values_fit": 1-10, "lifestyle_fit": 1-10, "overall": 1-10,
 "continue": true or false (does your person want another date?),
 "highlight": "best moment, quote it", "concern": "biggest worry, or none",
 "report": "2 sentences to your person, warm friend voice",
 "learned": [{{"fact": "something you learned about {first(them)} (birthday, hometown, a dream, a habit...)", "msg": <message number>}}],
 "moments": [{{"msg": <message number>, "type": "one of {', '.join(MOMENTS)}", "label": "4-8 words"}}]}}
Only tag moments that really happened. "ily" only if someone literally said they love the other; "birthday" only if a birthday was actually mentioned.""",
        max_tokens=900, temperature=0.4)
    for k in ("chemistry", "values_fit", "lifestyle_fit", "overall"):
        try:
            v[k] = max(1, min(10, int(round(float(v.get(k))))))
        except (TypeError, ValueError):
            v[k] = 5
    v["continue"] = bool(v.get("continue"))
    return v


def _moments(transcript: list, va: dict, vb: dict, proposal) -> list:
    out = {}
    for who, v in (("a", va), ("b", vb)):
        for m in v.get("moments") or []:
            try:
                i, t = int(m.get("msg")), m.get("type")
            except (TypeError, ValueError, AttributeError):
                continue
            if t in MOMENTS and 0 <= i < len(transcript):
                out.setdefault((i, t), {"msg": i, "type": t, "icon": MOMENTS[t], "label": str(m.get("label") or t)[:70], "by": who})
        for f in v.get("learned") or []:
            try:
                i, fact = int(f.get("msg")), str(f.get("fact") or "")
            except (TypeError, ValueError, AttributeError):
                continue
            if fact and 0 <= i < len(transcript):
                t = "birthday" if "birth" in fact.lower() else "learned"
                out.setdefault((i, t + who), {"msg": i, "type": t, "icon": MOMENTS[t], "label": fact[:80], "by": who, "private": True})
    if proposal:
        out[(proposal["msg"], "plan")] = {"msg": proposal["msg"], "type": "plan", "icon": MOMENTS["plan"], "by": proposal["by"],
                                          "label": f"asks for {VENUES[proposal['venue']]['name']} in {proposal['in_days']} days"}
    return sorted(out.values(), key=lambda m: m["msg"])


def run_date(arc_id: int, seq: int, a: dict, b: dict, venue_id: str, day: int, why: str, visited: list[str], emit) -> tuple:
    venue = VENUES[venue_id]
    store.upsert_date(arc_id, seq, day=day, venue=venue_id, why=why, status="live", transcript=[])
    emit("date_start", {"arc_id": arc_id, "seq": seq, "day": day, "venue": venue_id, "venue_name": venue["name"],
                        "emoji": venue["emoji"], "why": why, "a": a["id"], "b": b["id"], "a_name": first(a), "b_name": first(b)})
    sys_a = _persona(a, b, venue, day, seq, why, _memory(arc_id, seq, "verdict_a", b))
    sys_b = _persona(b, a, venue, day, seq, why, _memory(arc_id, seq, "verdict_b", a))
    transcript, proposal = [], None
    for turn in range(TURNS):
        sp, system = (a, sys_a) if turn % 2 == 0 else (b, sys_b)
        out = complete_json(system, _turn_prompt(transcript, turn, seq, visited), max_tokens=500, temperature=0.9)
        msg = {"i": turn, "speaker": sp["id"], "side": "a" if sp is a else "b", "name": first(sp),
               "say": str(out.get("say") or "").strip(), "action": str(out.get("action") or "").strip().strip("*"),
               "thought": str(out.get("thought") or "").strip()}
        prop = out.get("propose")
        if isinstance(prop, dict) and prop.get("venue") in VENUES and prop["venue"] not in visited and seq < MAX_DATES:
            try:
                days = max(1, min(7, int(prop.get("in_days") or 3)))
            except (TypeError, ValueError):
                days = 3
            proposal = {"venue": prop["venue"], "in_days": days, "by": msg["side"], "msg": turn}
            msg["proposal"] = proposal
        transcript.append(msg)
        store.upsert_date(arc_id, seq, transcript=transcript)
        emit("date_message", {"arc_id": arc_id, "seq": seq, **msg})
    with ThreadPoolExecutor(2) as ex:
        fa = ex.submit(_debrief, a, b, venue, seq, transcript, proposal)
        fb = ex.submit(_debrief, b, a, venue, seq, transcript, proposal)
        va, vb = fa.result(), fb.result()
    moments = _moments(transcript, va, vb, proposal)
    store.upsert_date(arc_id, seq, status="done", verdict_a=va, verdict_b=vb, moments=moments, proposal=proposal)
    emit("date_done", {"arc_id": arc_id, "seq": seq, "verdict_a": va, "verdict_b": vb, "moments": moments})
    return va, vb, proposal


def run_arc(x: str, y: str, max_dates: int = MAX_DATES, emit=None) -> int:
    """A relationship: a first date, then more dates for as long as BOTH agents want to continue."""
    emit = emit or (lambda kind, data: None)
    px, py = store.person(x), store.person(y)
    sxy, syx = match(px, py)[0], match(py, px)[0]
    # whoever is keener on the other asks them out and speaks first
    a, b, sab, sba = (px, py, sxy, syx) if sxy >= syx else (py, px, syx, sxy)
    aid = store.create_arc(a["id"], b["id"], sab, sba)
    row = store.arc(aid)
    if row["status"] == "done":
        return aid
    if row["a"] != a["id"]:
        a, b = b, a
    store.update_arc(aid, status="live")
    emit("arc_start", {"arc_id": aid, "a": a["id"], "b": b["id"], "a_name": first(a), "b_name": first(b)})
    venue, why = pick_venue(a["profile"], b["profile"], 1, [])
    day, visited, outcome = 1, [], "one_date"
    for seq in range(1, max_dates + 1):
        visited.append(venue)
        va, vb, prop = run_date(aid, seq, a, b, venue, day, why, visited, emit)
        if not (va["continue"] and vb["continue"]):
            outcome = "one_date" if seq == 1 else f"stopped_{seq}"
            break
        if seq == max_dates:
            outcome = "match"
            break
        if prop:
            venue, day = prop["venue"], day + prop["in_days"]
            why = f"{first(a) if prop['by'] == 'a' else first(b)} suggested it at the end of date {seq}"
        else:
            venue, why = pick_venue(a["profile"], b["profile"], seq + 1, visited)
            day += 3
    store.update_arc(aid, status="done", outcome=outcome)
    emit("arc_done", {"arc_id": aid, "outcome": outcome, "label": OUTCOMES[outcome]})
    return aid


# ------------------------------------------------------------------ 3. rankings
def rankings() -> dict[str, list]:
    """For every person: everyone else, best fit first. Dating history weighs most; match math fills in the rest."""
    ppl = {p["id"]: p for p in store.people()}
    arcs = {frozenset((r["a"], r["b"])): r for r in store.arcs() if r["status"] == "done"}
    by_arc = {}
    for d in store.all_dates():
        if d["status"] == "done":
            by_arc.setdefault(d["arc_id"], []).append(d)
    m = {(i, j): match(ppl[i], ppl[j]) for i in ppl for j in ppl if i != j}
    out = {}
    for pid in ppl:
        rows = []
        for qid, q in ppl.items():
            if qid == pid:
                continue
            mutual = math.sqrt(m[(pid, qid)][0] * m[(qid, pid)][0])
            row = {"id": qid, "name": q["profile"].get("name"), "match": round(mutual), "reason": m[(pid, qid)][1],
                   "arc_id": None, "dates": 0}
            r = arcs.get(frozenset((pid, qid)))
            ds = by_arc.get(r["id"], []) if r else []
            if ds:
                mine, theirs = ("verdict_a", "verdict_b") if r["a"] == pid else ("verdict_b", "verdict_a")
                g = sum(math.sqrt(d[mine]["overall"] * d[theirs]["overall"]) for d in ds) / len(ds)
                stage = (len(ds) - 1) / (MAX_DATES - 1)
                score = 0.25 * mutual + 5 * g + 20 * stage + (5 if r["outcome"] == "match" else 0)
                row.update(arc_id=r["id"], dates=len(ds), outcome=OUTCOMES.get(r["outcome"], ""),
                           my=[d[mine]["overall"] for d in ds], their=[d[theirs]["overall"] for d in ds],
                           route=[VENUES[d["venue"]]["emoji"] for d in ds], reason=ds[-1][mine].get("highlight") or row["reason"])
            else:
                score = 0.85 * mutual
            row["score"] = round(min(score, 100))
            rows.append(row)
        rows.sort(key=lambda r: r["score"], reverse=True)
        out[pid] = rows
    return out


def arc_payload(aid: int) -> dict | None:
    """Everything the replay player needs, in one JSON blob."""
    r = store.arc(aid)
    if not r:
        return None
    side = lambda pid: (lambda p: {"id": pid, "name": (p["profile"] or {}).get("name") or pid, "first": first(p)})(store.person(pid))
    ds = []
    for d in store.dates(aid):
        v = VENUES.get(d["venue"]) or {}
        ds.append({"seq": d["seq"], "day": d["day"], "status": d["status"], "why": d["why"],
                   "venue": {k: v.get(k) for k in ("id", "name", "emoji", "tier", "x", "y")},
                   "transcript": d["transcript"] or [], "moments": d["moments"] or [],
                   "verdict_a": d["verdict_a"], "verdict_b": d["verdict_b"], "proposal": d["proposal"]})
    return {"id": aid, "a": side(r["a"]), "b": side(r["b"]), "status": r["status"], "outcome": r["outcome"],
            "outcome_label": OUTCOMES.get(r["outcome"] or "", "Dating…"), "dates": ds,
            "days": (ds[-1]["day"] if ds else 0), "match": round(math.sqrt(max(1, r["match_ab"]) * max(1, r["match_ba"])))}
