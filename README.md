# Cyrano 💌: agents that date for you

**Every person gets an AI agent. The agent reads their public LinkedIn and Instagram, builds an evidence-cited profile of their needs, hobbies, interests and qualities, then goes on real multi-turn, multi-day dates with other people's agents. It remembers each date and ranks who fits its person best.**

*Cyrano de Bergerac wrote someone else's love letters. These agents write yours, using only your own words.*

- **Demo** (the finished run with real people, nothing to type): `docs/` on GitHub Pages
- **Live**: paste any LinkedIn + public Instagram, then watch the agent scrape, analyze, match and go on live dates, with every message streaming in

## How it works
```
LinkedIn (public) ──┐
                    ├─► local scrapers, no login ──► raw JSON (bio, 12 latest posts, IG image descriptions, experience, posts…)
Instagram (public) ─┘
                                 │
                 plain Python ───┤  hard signals: words/caption, emoji rate, Hinglish mix, posting rhythm, who they tag
                                 ▼
                 ANALYSIS agent (1 LLM call) → evidence-cited profile; every claim cites a post (P1…P12) or LinkedIn
                                 ▼
                 MATCH (no LLM): shared tags + values, social energy, city, partner traits → two-way score matrix
                                 ▼
                 FIRST DATES for the best mutual pairs ── venue picked from Date Town for what both love
                                 ▼
    ┌──────────► DATE: each message = its own LLM call with only that agent's private brief + the other's public card
    │            (say · action · private thought). Near the end, an agent may propose the next date: venue + in_days.
    │                            ▼
    │            DEBRIEF: each agent privately rates the date, says whether it wants another, logs what it learned,
    │            and tags moments (😂 laugh, 🤝 common ground, 🎂 birthday, ✨ spark, 📅 plan, 🔁 callback…)
    └── both want more? ── next date, day +N, carrying each agent's own memory of the earlier dates (max 3)
                                 ▼
                 RANKINGS for every person: how far the pair got + both agents' ratings; the match score fills in the rest
                                 ▼
                 REPLAY PLAYER: the town map with the route, the whole conversation playing like a video,
                 a scrubber with moment markers, and "watch as" either agent (their private thoughts and debriefs)
```

### Design choices, and the research behind them
| Choice | Why |
|---|---|
| The math only shortlists; the agents actually date | Pre-date trait matching predicts who is attractive to people in general, but almost none of a specific pair's chemistry (Joel, Eastwick & Finkel 2017, *Psychological Science*) |
| Agents grounded in the person's verbatim posts | Agents built from a person's own words simulate them far better than description-based ones (Park et al. 2024, *Generative Agent Simulations of 1,000 People*) |
| Dates escalate (café → activity → evening) and self-disclosure deepens | Aron et al. 1997, the "36 questions" closeness procedure |
| Calibrated debriefs that must quote the moment behind a score | LLMs tend to agree with each other (Sharma et al. 2023), and two agreeable agents make every date an 8/10 |
| Voice rules repeated every turn | Personas drift within a handful of turns (Li et al. 2024) |
| Personality labelled "inferred", with evidence | Big Five read from social media correlates only about 0.3–0.4 with the real thing (Azucar et al. 2018 meta-analysis) |

Guardrails: only the two sources, no outside knowledge even about famous people, no invented facts, no inferring religion, politics, health, sexuality, ethnicity or caste, and relationship status and orientation are never assumed. Private Instagram accounts are rejected.

## Tech stack
- **Scraping (local, no login, no paid API, $0):** *Instagram:* first Instagram's mobile `web_profile_info` endpoint (profile + 12 latest posts with captions, tags, locations). When it throttles, a real logged-out Chromium (Playwright) loads the public profile and reads the profile JSON embedded in the page plus the 12-post grid (dates and Instagram's own image descriptions). Private accounts are rejected. *LinkedIn:* the public profile page: JSON-LD `Person` (job titles, companies, education, about, languages, awards), their posts and activity, and the page text. If LinkedIn answers 999, the same public page is fetched through Jina Reader.
- **Agents:** `app/llm.py`, a failover chain of zero-cost LLM lanes: Claude through Claude Code in headless mode (`claude -p`), then the Cerebras (`gpt-oss-120b`, Qwen) and Groq free tiers. Each lane is throttled to its free-tier limits and cools down on a 429.
- **Web:** FastAPI, Jinja2, Tailwind and SQLite. Live dates stream over Server-Sent Events, and the replay player is vanilla JS. `scripts/export.py` renders the finished run to static HTML for GitHub Pages.

## Run it
```bash
uv venv && uv pip install -r requirements.txt playwright
cp .env.example .env        # optional CEREBRAS_API_KEY / GROQ_API_KEY; Claude Code works with no key
.venv/bin/python -m playwright install chromium
# people.txt: one "linkedin_url instagram_url" per line
.venv/bin/python scripts/batch.py all 3      # scrape → analyze → 3 first dates each → arcs
.venv/bin/uvicorn app.main:app --port 8000   # the site; share it with: cloudflared tunnel --url http://localhost:8000
.venv/bin/python scripts/export.py /<repo> <live-url>   # static demo → docs/
```

## Code map
| File | What it does |
|---|---|
| `app/scrape.py` | local IG + LinkedIn scrapers (mobile API → real browser; public page → Jina), photos |
| `app/brain.py` | source pack, measured signals, evidence-cited analysis |
| `app/dating.py` | Date Town venues, matching, scheduling, personas, dates, memory, debriefs, moments, rankings |
| `app/llm.py` | zero-cost LLM lanes with throttling + failover |
| `app/main.py` | pages, live add job, SSE stream |
| `app/templates/arc.html` | the replay player |

*A technical demo built only from public profiles. The dates are simulations between AI agents. No one listed is affiliated with or endorses this project.*
