"""LLM access over free / zero-marginal-cost lanes, with per-lane throttling and failover.

LLM_ORDER (default "claude,cerebras,groq") picks the order. A lane is skipped when its CLI or key is missing.
claude = the local Claude Code CLI in headless mode (`claude -p`), no API key.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_env() -> None:
    path = os.path.join(ROOT, ".env")
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8-sig"):
        k, sep, v = line.strip().partition("=")
        v = v.split(" #")[0].strip().strip('"').strip("'")
        if sep and k and not k.startswith("#") and v:
            os.environ.setdefault(k.strip(), v)


load_env()


class LLMError(RuntimeError):
    pass


class Retry(Exception):
    pass


class Lane:
    def __init__(self, provider, model, parallel, gap, base=None, key=None):
        self.provider, self.model, self.gap, self.base, self.key = provider, model, gap, base, key
        self.sem = threading.Semaphore(parallel)
        self.lock = threading.Lock()
        self.next_at = 0.0
        self.cool_until = 0.0
        self.calls = 0

    @property
    def name(self):
        return f"{self.provider}:{self.model}"

    def ready(self) -> bool:
        return bool(shutil.which("claude")) if self.provider == "claude" else bool(os.environ.get(self.key))

    def wait_turn(self):
        with self.lock:
            now = time.time()
            start = max(now, self.next_at)
            self.next_at = start + self.gap
        if start > now:
            time.sleep(start - now)

    def call(self, system: str, user: str, max_tokens: int, temperature: float) -> str:
        if self.provider == "claude":
            cmd = [shutil.which("claude"), "-p", "--output-format", "json", "--model", self.model,
                   "--system-prompt", system, "--tools", "", "--strict-mcp-config",
                   "--no-session-persistence", "--setting-sources", ""]
            with tempfile.TemporaryDirectory() as cwd:
                r = subprocess.run(cmd, input=user, capture_output=True, text=True, encoding="utf-8", cwd=cwd, timeout=240)
            out = r.stdout.strip()
            try:
                data = json.loads(out)
            except Exception:
                msg = (r.stderr or out)[:300]
                raise (Retry if any(w in msg.lower() for w in ("limit", "overload", "429", "529")) else LLMError)(msg)
            if data.get("is_error"):
                raise Retry(str(data.get("result"))[:300])
            return data.get("result") or ""
        body = {"model": self.model, "temperature": temperature,
                "max_tokens": max_tokens * 3 if "gpt-oss" in self.model else max_tokens,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        r = requests.post(f"{self.base}/chat/completions", json=body, timeout=150,
                          headers={"Authorization": f"Bearer {os.environ[self.key]}"})
        if r.status_code in (408, 429, 500, 502, 503, 504):
            raise Retry(f"{r.status_code} {r.text[:200]}")
        if r.status_code >= 400:
            raise LLMError(f"{r.status_code} {r.text[:300]}")
        return (r.json()["choices"][0]["message"].get("content") or "").strip()


LANES = [
    Lane("claude", os.environ.get("CLAUDE_MODEL", "sonnet"), int(os.environ.get("CLAUDE_PARALLEL", "5")), 0),
    Lane("cerebras", "gpt-oss-120b", 2, 12, "https://api.cerebras.ai/v1", "CEREBRAS_API_KEY"),
    Lane("cerebras", "qwen-3.8-27b", 2, 12, "https://api.cerebras.ai/v1", "CEREBRAS_API_KEY"),
    Lane("groq", "openai/gpt-oss-120b", 2, 2, "https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    Lane("groq", "llama-3.3-70b-versatile", 2, 2, "https://api.groq.com/openai/v1", "GROQ_API_KEY"),
]


def lanes(heavy: bool = False) -> list[Lane]:
    order = [p.strip() for p in os.environ.get("LLM_ORDER", "claude,cerebras,groq").split(",") if p.strip()]
    out = [l for p in order for l in LANES if l.provider == p and l.ready()]
    if heavy:  # groq's free tokens-per-minute cap cannot fit a full analysis prompt
        out = [l for l in out if l.provider != "groq"] or out
    return out


def complete(system: str, user: str, *, max_tokens: int = 900, temperature: float = 0.8, heavy: bool = False) -> str:
    pool = lanes(heavy)
    if not pool:
        raise LLMError("No LLM available: log in to Claude Code, or set CEREBRAS_API_KEY / GROQ_API_KEY in .env")
    last = None
    for attempt in range(6):
        live = [l for l in pool if l.cool_until <= time.time()]
        if not live:
            time.sleep(min(60, max(1, min(l.cool_until for l in pool) - time.time())))
            continue
        # take the first free lane; if every lane is busy, queue on the first one
        lane = next((l for l in live if l.sem.acquire(blocking=False)), None)
        if lane is None:
            lane = live[0]
            lane.sem.acquire()
        try:
            lane.wait_turn()
            text = lane.call(system, user, max_tokens, temperature)
            lane.calls += 1
            return text
        except Retry as e:
            last = f"{lane.name}: {e}"
            lane.cool_until = time.time() + 30 * (attempt + 1)
        except Exception as e:
            last = f"{lane.name}: {e}"
            lane.cool_until = time.time() + 20
        finally:
            lane.sem.release()
    raise LLMError(f"all LLM lanes failed; last: {last}")


def parse_json(text: str):
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        return json.loads(text)
    except Exception:
        pass
    i, j = text.find("{"), text.rfind("}")
    if i >= 0 and j > i:
        try:
            return json.loads(text[i:j + 1])
        except Exception:
            pass
    raise LLMError(f"model did not return JSON: {text[:160]}")


def complete_json(system: str, user: str, **kw) -> dict:
    system += "\n\nReply with ONLY one valid JSON object. No markdown, no commentary."
    for attempt in range(3):
        try:
            out = parse_json(complete(system, user, **kw))
            if isinstance(out, dict):
                return out
        except LLMError:
            if attempt == 2:
                raise
    raise LLMError("model kept returning non-object JSON")


def stats() -> dict:
    return {l.name: l.calls for l in LANES if l.calls}
