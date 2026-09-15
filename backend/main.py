"""Hookline backend: FastAPI + SSE analyze pipeline.

Endpoints:
  GET  /api/health
  GET  /api/video-title?video_id=...
  POST /api/analyze   (SSE stream of pipeline stages)
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import re
import time
import uuid
from pathlib import Path
from typing import Any, AsyncIterator
from fastapi import UploadFile, File

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import TranscriptsDisabled, NoTranscriptFound

import video_engine as ve

load_dotenv(Path(__file__).resolve().parent / ".env")
SERVER_GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "").strip()

app = FastAPI(title="hookline", version="1.0.0")

YT_OEMBED = "https://www.youtube.com/oembed"

_INNERTUBE_KEY_CACHE: str | None = None


def innertube_key() -> str | None:
    """Resolve YouTube's public innertube client key at runtime (rotated server-side by Google,
    published on every watch page) -- no hardcoded key in this repo."""
    global _INNERTUBE_KEY_CACHE
    if _INNERTUBE_KEY_CACHE:
        return _INNERTUBE_KEY_CACHE
    env = os.environ.get("HOOKLINE_INNERTUBE_KEY")
    if env:
        return env
    try:
        r = requests.get("https://www.youtube.com/watch?v=jNQXAC9IVRw", headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}, timeout=10)
        m = re.search(r'"INNERTUBE_API_KEY":"([A-Za-z0-9_-]{30,})"', r.text)
        if m:
            _INNERTUBE_KEY_CACHE = m.group(1)
            return _INNERTUBE_KEY_CACHE
    except Exception:
        pass
    return None
INNERTUBE_URL = "https://www.youtube.com/youtubei/v1/player"

GEMINI_CHAIN = [
    "gemini-3.6-flash",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
    "gemini-1.5-flash-8b",
    "gemini-1.5-flash",
]
GEMINI_STYLE_NOTE = "text/plain"


def discover_flash_models(api_key: str) -> list[str]:
    """List models the key can actually use, newest Flash first. Empty on failure."""
    try:
        from google import genai

        client = genai.Client(api_key=api_key)
        found: list[str] = []
        for m in client.models.list():
            name = getattr(m, "name", "") or ""
            short = name.removeprefix("models/").strip()
            if not short:
                continue
            low = short.lower()
            if "flash" not in low or "tts" in low or "image" in low or "thinking" in low or "lite" in low and "8b" in low:
                continue
            if any(low.endswith(s) for s in ("-latest", "-preview", "-exp", "-001")):
                continue
            actions = getattr(m, "supported_generation_methods", None) or []
            if actions and "generateContent" not in actions:
                continue
            if re.fullmatch(r"gemini-\d+(\.\d+)?-flash(-8b)?", low):
                found.append(short)
        # version-descending: 3.6 before 2.5 etc.
        def vkey(s: str) -> list[int]:
            return [int(x) for x in re.findall(r"\d+", s)]

        found.sort(key=vkey, reverse=True)
        return found
    except Exception:
        return []


class AnalyzeRequest(BaseModel):
    url: str = ""
    duration: str = "30s"
    api_key: str = ""
    model: str | None = None
    custom_prompt: str | None = None
    target_clip_count: int = 8
    subtitles: str | None = None
    language: str = "id"
    upload_id: str | None = None


# ---------------------------------------------------------------------------
# helpers

def extract_video_id(url: str) -> str | None:
    if not url:
        return None
    m = re.search(r"(?:v=|/shorts/|/embed/|youtu\.be/)([A-Za-z0-9_-]{11})", url.strip())
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", url.strip()):
        return url.strip()
    return None


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def source_id(raw: str) -> str:
    """Client-supplied source token (YouTube URL/id or upload key) -> filesystem-safe id."""
    vid = (extract_video_id(raw) or raw or "").strip()
    if not SAFE_ID_RE.fullmatch(vid):
        raise HTTPException(400, "Invalid source id.")
    return vid


async def stage(event: str, data: dict, delay: float = 0.25) -> str:
    await asyncio.sleep(delay)
    return sse(event, data)


def get_video_title(video_id: str) -> dict:
    try:
        r = requests.get(YT_OEMBED, params={"url": f"https://www.youtube.com/watch?v={video_id}", "format": "json"}, timeout=8)
        if r.status_code == 200:
            j = r.json()
            return {"video_id": video_id, "title": j.get("title", video_id), "author": j.get("author_name", "")}
    except Exception:
        pass
    return {"video_id": video_id, "title": video_id, "author": ""}


def probe_most_replayed(video_id: str) -> list[dict] | None:
    """Best-effort real retention curve via innertube ANDROID client. None on any failure."""
    key = innertube_key()
    if not key:
        return None
    try:
        payload = {
            "context": {"client": {"clientName": "ANDROID", "clientVersion": "20.10.38", "androidSdkVersion": 30}},
            "videoId": video_id,
        }
        r = requests.post(
            f"{INNERTUBE_URL}?key={key}",
            json=payload,
            headers={"Content-Type": "application/json", "User-Agent": "com.google.android.youtube/20.10.38 (Linux; U; Android 11) gzip"},
            timeout=10,
        )
        if r.status_code != 200:
            return None
        player = r.json()
        markers = player.get("frameworkUpdates", {}).get("entityBatchUpdate", {}).get("mutations", [])
        for mut in markers:
            payload_mut = mut.get("payload", {})
            mlr = payload_mut.get("mostReplayedMarker", {})
            if mlr:
                out = []
                for intensity in mlr.get("markers", []):
                    start = intensity.get("startMillis", 0)
                    out.append({"start": start / 1000.0, "end": (start + intensity.get("durationMillis", 60000)) / 1000.0, "intensity": intensity.get("intensityScoreNormalized", 0.5)})
                return out
        return None
    except Exception:
        return None


def estimate_duration_from_transcript(segments: list[dict]) -> float:
    if not segments:
        return 600.0
    last = segments[-1]
    return max(60.0, last.get("start", 0) + last.get("duration", 4))


def fetch_transcript(video_id: str, preferred_langs: tuple[str, ...] = ("id", "en")) -> tuple[list[dict] | None, str]:
    """Pick the best transcript available (manual > auto, preferred language first)."""
    api = YouTubeTranscriptApi()
    try:
        available = list(api.list(video_id))
    except (TranscriptsDisabled, NoTranscriptFound):
        return None, "disabled"
    except Exception:
        return None, "error"
    if not available:
        return None, "disabled"

    def score(t) -> int:
        s = 0
        if not getattr(t, "is_generated", True):
            s += 10
        lang = getattr(t, "language_code", "")
        if lang in preferred_langs:
            s += 5 - preferred_langs.index(lang)
        return s

    best = max(available, key=score)
    try:
        fetched = best.fetch()
        segments = [{"start": sn.start, "duration": sn.duration, "text": sn.text} for sn in fetched]
        return (segments, "youtube") if segments else (None, "empty")
    except Exception:
        return None, "error"


def fetch_words(video_id: str, preferred_langs: tuple[str, ...] = ("id", "en")) -> list[dict]:
    """Per-word timings straight from YouTube's caption track (json3). Best-effort: [] on any
    failure; the line-level transcript still drives everything else."""
    api = YouTubeTranscriptApi()
    try:
        available = list(api.list(video_id))
    except Exception:
        return []

    def score(t: object) -> int:
        s = 0
        if not getattr(t, "is_generated", True):
            s += 10
        lang = getattr(t, "language_code", "") or ""
        if lang in preferred_langs:
            s += 5 - preferred_langs.index(lang)
        return s

    best = max(available, key=score)
    url = getattr(best, "_url", None)
    if not url:
        return []
    try:
        r = requests.get(url, params={"fmt": "json3"}, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            return []
        events = r.json().get("events", [])
    except Exception:
        return []
    words: list[dict] = []
    for ev in events:
        segs = ev.get("segs") or []
        t0 = float(ev.get("tStartMs", 0)) / 1000.0
        last = t0
        for s in segs:
            txt = str(s.get("utf8", "")).strip()
            if not txt:
                continue
            off = s.get("tOffsetMs")
            t = (float(off) / 1000.0) if off is not None else last
            words.append({"t": round(t, 3), "w": txt})
            last = t
    return words


def format_transcript(segments: list[dict], cap: int = 20000) -> tuple[str, bool]:
    """Timestamped blocks merged to ~12s/200-char units; if over cap, sample 10
    evenly distributed windows so the model sees the WHOLE video, not just the start."""
    blocks: list[tuple[float, str]] = []
    cur_start: float | None = None
    cur: list[str] = []
    for s in segments:
        txt = str(s.get("text", "")).strip()
        if not txt:
            continue
        if cur_start is None:
            cur_start = float(s.get("start", 0))
        cur.append(txt)
        end = cur_start + float(s.get("duration", 2))
        if end - cur_start >= 12 or len(" ".join(cur)) > 200:
            blocks.append((cur_start, " ".join(cur)))
            cur_start, cur = None, []
    if cur and cur_start is not None:
        blocks.append((cur_start, " ".join(cur)))

    def fmt(b: tuple[float, str]) -> str:
        t = int(b[0])
        return f"[{t // 3600}:{t // 60 % 60:02d}:{t % 60:02d}] {b[1]}"

    lines = [fmt(b) for b in blocks]
    full = "\n".join(lines)
    if len(full) <= cap:
        return full, False
    k = 10
    size = max(1, len(lines) // k)
    per = cap // k
    parts = []
    for i in range(k):
        window = lines[i * size:(i + 1) * size] if i < k - 1 else lines[i * size:]
        joined = "\n".join(window)
        if len(joined) > per:
            joined = joined[:per]
        parts.append(joined)
    return "\n".join(parts), True


def build_signal_estimates(segments: list[dict], count: int = 60) -> list[float]:
    """Synthetic but data-grounded attention curve when real markers unavailable:
    peaks where transcript density spikes (speech rate + keywords + punctuation)."""
    total = estimate_duration_from_transcript(segments)
    step = max(1.0, total / count)
    values: list[float] = []
    words_cache: list[tuple[float, float, str]] = [(s.get("start", 0), s.get("duration", 4), s.get("text", "")) for s in segments]
    for i in range(count):
        t = i * step
        window = [w for w in words_cache if w[0] <= t < w[0] + w[1] + 4]
        density = len(window)
        base = min(1.0, density / 6.0)
        boost = 0.0
        for _s, _d, txt in window:
            low = txt.lower()
            if any(k in low for k in ("?", "!", "wait", "actually", "the secret", "here's", "listen")):
                boost = max(boost, 0.35)
        values.append(max(0.05, min(1.0, base * 0.7 + boost)))
    # smooth
    smoothed = [values[0]] + [(values[i - 1] + 2 * values[i] + values[i + 1]) / 4 for i in range(1, count - 1)] + [values[-1]]
    return smoothed


def parse_manual_subtitles(raw: str) -> list[dict]:
    """Parse SRT or plain text into segments."""
    raw = raw.strip()
    if not raw:
        return []
    if "-->" in raw:
        segs: list[dict] = []
        blocks = re.split(r"\n\s*\n", raw)
        for b in blocks:
            lines = [l for l in b.splitlines() if l.strip()]
            m = re.search(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)", b)
            if not m:
                continue
            g = [int(x) for x in m.groups()]
            start = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
            end = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
            text = " ".join(l for l in lines if "-->" not in l and not l.strip().isdigit())
            if text:
                segs.append({"start": start, "duration": end - start, "text": text})
        return segs
    # plain text: distribute across assumed 10 minutes
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", raw) if s.strip()]
    per = 600.0 / max(1, len(sentences))
    return [{"start": i * per, "duration": per, "text": s} for i, s in enumerate(sentences)]


def sanitize_caption(text: str) -> str:
    """Strip [LAUGHTER]/[APPLAUSE]/(music) tags, keep meaningful punctuation."""
    text = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def score_clip_against_signal(clip: dict, signal: list[dict] | None, estimates: list[float] | None, total: float) -> float:
    s, e = clip.get("start", 0), clip.get("end", 0)
    if signal:
        overlap = [m["intensity"] for m in signal if m["start"] < e and m["end"] > s]
        if overlap:
            return round(max(overlap), 3)
    if estimates and total > 0:
        idx_s = int(s / total * len(estimates))
        idx_e = max(idx_s + 1, int(e / total * len(estimates)))
        window = estimates[idx_s:idx_e]
        if window:
            return round(max(window), 3)
    return 0.5


FILLER_PAT = re.compile(
    r"\b(like and subscribe|don'?t forget to|smash (the )?(like|bell)|sponsor|discount code|use code|coupon code|promo code|in this video (i|we)|welcome back|hey guys|thank you for watching|link in (the )?bio|comment below)\b",
    re.I,
)


def transcript_text(segments: list[dict]) -> str:
    return " ".join(str(s.get("text", "")).lower() for s in segments)


def window_segments(segments: list[dict], start: float, end: float) -> list[dict]:
    return [s for s in segments if float(s.get("start", 0)) < end and float(s.get("start", 0)) + float(s.get("duration", 2)) > start]


def quote_fidelity(quote: str, segments: list[dict]) -> float:
    """Share of quote tokens present in the transcript. Guards against invented moments."""
    toks = [t for t in re.findall(r"[a-z0-9']+", quote.lower()) if len(t) > 1]
    if not toks:
        return 0.0
    hay = transcript_text(segments)
    hit = sum(1 for t in toks if t in hay)
    return round(hit / len(toks), 3)


def snap_edge(segments: list[dict], t: float, tol: float = 6.0) -> float:
    """Snap a cut edge to the nearest transcript boundary so clips never start mid-word."""
    edges = [float(s.get("start", 0)) for s in segments] + [float(s.get("start", 0)) + float(s.get("duration", 2)) for s in segments]
    if not edges:
        return t
    best = min(edges, key=lambda e: abs(e - t))
    return best if abs(best - t) <= tol else t


def enrich_clip(c: dict, segments: list[dict], signal: list[dict] | None, estimates: list[float] | None, total: float) -> dict:
    """Attach real captions, verify the moment exists, snap edges, score substance."""
    c["start"] = round(snap_edge(segments, float(c["start"])), 2)
    c["end"] = round(snap_edge(segments, float(c["end"])), 2)
    if c["end"] - c["start"] < 8:
        c["end"] = round(c["start"] + 12.0, 2)
    c["subtitles"] = clip_subtitles(segments, c["start"], c["end"])
    win = window_segments(segments, c["start"], c["end"])
    dur = max(1.0, c["end"] - c["start"])
    wcount = sum(len(str(s.get("text", "")).split()) for s in win)
    density = wcount / dur
    fid = quote_fidelity(c.get("quote", ""), segments) if segments else 0.5
    filler = bool(FILLER_PAT.search(" ".join(str(s.get("text", "")) for s in win)))
    c["word_count"] = wcount
    c["source_confidence"] = fid
    c["score"] = score_clip_against_signal(c, signal, estimates, total)
    value = 0.55 * c["score"] + 0.30 * fid + 0.15 * min(1.0, density / 3.0)
    if filler:
        value *= 0.5
    if wcount < 12:
        value *= 0.6
    c["value"] = round(min(1.0, value), 3)
    return c


# ---------------------------------------------------------------------------
# Gemini

def gemini_generate(api_key: str, model: str, prompt: str) -> tuple[str, str]:
    """Returns (text, model_used). Raises on total failure."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    resp = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(response_mime_type=GEMINI_STYLE_NOTE),
    )
    text = resp.text or ""
    if not text.strip():
        raise ValueError("empty response")
    return text, model


def gemini_generate_on_video(api_key: str, model: str, path: Path, prompt: str) -> str:
    """Send a video file through the Files API and ask about it (no transcript needed)."""
    from google import genai

    client = genai.Client(api_key=api_key)
    f = gemini_upload_video(client, path)
    try:
        return gemini_generate_on_file(client, model, f, prompt)
    finally:
        gemini_delete_file(client, f)


def gemini_upload_video(client: Any, path: Path) -> Any:
    """Upload once and wait for ACTIVE; the same file can serve the whole model chain."""
    import time as _time

    f = client.files.upload(file=str(path))
    fname = f.name or ""
    if not fname:
        raise ValueError("the upload was rejected by the Gemini Files API")
    waited = 0.0
    while str(getattr(f.state, "name", f.state)) == "PROCESSING" and waited < 300:
        _time.sleep(3)
        waited += 3
        f = client.files.get(name=fname)
    if str(getattr(f.state, "name", f.state)) != "ACTIVE":
        raise ValueError(f"file not ready (state={getattr(f.state, 'name', f.state)})")
    return f


def gemini_generate_on_file(client: Any, model: str, f: Any, prompt: str) -> str:
    from google.genai import types

    resp = client.models.generate_content(
        model=model,
        contents=[
            types.Part(file_data=types.FileData(file_uri=f.uri or f.name, mime_type=f.mime_type or "video/mp4")),
            types.Part(text=prompt),
        ],
        config=types.GenerateContentConfig(response_mime_type=GEMINI_STYLE_NOTE),
    )
    text = resp.text or ""
    if not text.strip():
        raise ValueError("empty response")
    return text


def gemini_delete_file(client: Any, f: Any) -> None:
    try:
        if f.name:
            client.files.delete(name=f.name)
    except Exception:
        pass


LANG_NAMES = {"id": "Indonesian", "en": "English", "ms": "Malay", "ar": "Arabic", "es": "Spanish", "pt": "Portuguese", "ja": "Japanese", "ko": "Korean"}


def lang_name(code: str) -> str:
    return LANG_NAMES.get((code or "").lower()[:2], "the same language as the source")


def build_video_prompt(clip_ms: int, custom_prompt: str | None, target_count: int, total_seconds: float, language: str) -> str:
    focus = f"\nViewer promise (pick ONLY moments that deliver this): {custom_prompt}" if custom_prompt else ""
    return f"""You are the head editor of a knowledge channel. You have been sent ONE raw video clip (picture and audio included). Watch and listen to all of it, then cut the moments that are worth republishing on their own.{focus}

Video length: {total_seconds:.0f} seconds. Target clip length: {clip_ms} seconds. Return up to {target_count} clips.

SELECTION BAR (all must hold):
- Substance: a complete idea - a claim, a number, a method step, a cause, a decision. Reject greetings, sponsor reads, "like and subscribe", meta-chatter, repetition, and anything that only restates another clip you already cut.
- Payoff inside the window: the viewer learns something specific before it ends.
- Standalone: makes sense with zero context. No "as I said before", no dangling pronouns - widen the window until it resolves.
- Clean edges: start on a sentence boundary, end on a completed thought. Use what is actually SAID, and use the picture too (a demo, a chart, a screen share, a reveal is a moment even if the words look plain).
- Only cut inside the real duration. Never invent a timestamp.

Return ONLY a JSON array (no markdown fences, no commentary), best-first. Each element:
{{"start": <seconds>, "end": <seconds>, "title": "<<= 60 chars, names the concrete takeaway>", "kind": "insight|method|number|story|warning", "reason": "<one sentence: the specific idea taught and why it holds up>", "quote": "<verbatim line, 5-15 words, exactly as spoken>", "captions": [{{"s": <seconds>, "d": <seconds>, "t": "<exactly what is said>"}}] }}
`captions` = the complete speech of that window as you hear it, 2-5 words per entry, times RELATIVE to that clip's start (first entry s=0), covering the whole window with no gaps.
Write title and reason in {lang_name(language)}. Keep quote and captions verbatim in the language actually spoken.
If nothing clears the bar, return [].
"""


def parse_video_clips(raw: str, offset: float, cap: float) -> list[dict]:
    """JSON from the video prompt, re-based from chunk-relative to absolute seconds."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
    a, b = raw.find("["), raw.rfind("]")
    if a == -1 or b <= a:
        raise ValueError("no JSON array found")
    arr = json.loads(raw[a : b + 1])
    clips: list[dict] = []
    for c in arr:
        try:
            st = float(c["start"]) + offset
            en = float(c["end"]) + offset
        except (KeyError, TypeError, ValueError):
            continue
        if en <= st or st < 0:
            continue
        en = min(en, offset + cap)
        kind = str(c.get("kind", "")).strip().lower()
        caps: list[dict] = []
        for cp in c.get("captions") or []:
            try:
                cs = float(cp.get("s", 0))
                cd = max(0.4, float(cp.get("d", 1.6)))
            except (TypeError, ValueError):
                continue
            txt = sanitize_caption(str(cp.get("t", "")))
            if txt:
                caps.append({"start": round(st + max(0.0, cs), 2), "duration": round(cd, 2), "text": txt[:120]})
        caps.sort(key=lambda s: s["start"])
        clips.append({
            "start": round(st, 2),
            "end": round(en, 2),
            "title": str(c.get("title", "Clip"))[:80],
            "kind": kind if kind in ("insight", "method", "number", "story", "warning") else "insight",
            "reason": str(c.get("reason", ""))[:300],
            "quote": sanitize_caption(str(c.get("quote", "")))[:160],
            "captions": caps[:60],
        })
    return clips



def clip_subtitles(segments: list[dict], start: float, end: float) -> list[dict]:
    """Absolute-time transcript window for a clip; the render engine re-bases it."""
    out: list[dict] = []
    for s in segments:
        try:
            ss, sd = float(s.get("start", 0)), float(s.get("duration", 2))
        except (TypeError, ValueError):
            continue
        se = ss + sd
        if se <= start or ss >= end:
            continue
        text = sanitize_caption(str(s.get("text", "")))
        if text:
            out.append({"start": ss, "duration": se - ss, "text": text})
    return out


def build_analysis_prompt(transcript: str, duration: str, custom_prompt: str | None, signal_summary: str, target_count: int, total_seconds: float, sampled: bool = False) -> str:
    clip_ms = {"15s": 15, "30s": 30, "60s": 60}.get(duration, 30)
    focus = f"\nViewer promise (pick ONLY moments that deliver this): {custom_prompt}" if custom_prompt else ""
    sampling_note = "\nThe transcript below is evenly sampled across the full video. Each line starts with [h:mm:ss] position. Convert that timestamp to SECONDS for start/end." if sampled else "\nEach transcript line starts with [h:mm:ss]; convert to SECONDS for start/end."
    instr = f"""You are the head editor of a knowledge channel that repurposes long videos into short clips. Your audience is demanding: they mute a video the second it stops teaching. Analyze this YouTube transcript with attention-signal data.{sampling_note}

TRANSCRIPT:
{transcript}

ATTENTION SIGNALS (most replayed regions, normalized 0-1):
{signal_summary}

Video duration: {total_seconds:.0f} seconds. Target clip length: {clip_ms} seconds. Extract up to {target_count} clips.{focus}

SELECTION BAR (all must hold):
- Substance: the window carries a complete idea - a claim, a number, a method step, a cause, or a decision. If it is greeting, sponsor read, self-promo, meta-chatter ("like and subscribe", "in this video we will"), pure repetition, or a restatement of a clip you already cut, it is disqualified.
- Payoff inside the window: the viewer learns something specific before it ends, not "stays tuned for it".
- Standalone: understandable with zero knowledge of the source. No dangling "as I mentioned before" or pronouns that need earlier context - extend or move the window until it resolves.
- Clean edges: start on a sentence boundary, end on a completed thought. Snap to the transcript lines; never cut mid-word or mid-sentence.
- Prefer windows near attention peaks, but substance outranks signal.

Return ONLY a JSON array (no markdown fences, no commentary), ranked by how much the viewer walks away with (best first). Each element:
{{"start": <seconds number>, "end": <seconds number>, "title": "<<= 60 chars, names the concrete takeaway, no clickbait filler>", "kind": "insight|method|number|story|warning", "reason": "<one sentence: the specific idea taught and why it holds up>", "quote": "<verbatim 5-15 word line from the transcript that lands the takeaway>"}}
Rules: start/end are numbers in seconds; clips must not overlap; end-start within {max(10, clip_ms - 12)}-{clip_ms + 25}s; quote must be verbatim from the transcript; if nothing in the video meets the bar, return [].
"""
    return instr


def parse_gemini_clips(raw: str) -> list[dict]:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
    start = raw.find("[")
    end = raw.rfind("]")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("no JSON array found")
    arr = json.loads(raw[start : end + 1])
    clips = []
    for c in arr:
        try:
            kind = str(c.get("kind", "")).strip().lower()
            clips.append({
                "start": float(c["start"]),
                "end": float(c["end"]),
                "title": str(c.get("title", "Clip"))[:80],
                "kind": kind if kind in ("insight", "method", "number", "story", "warning") else "insight",
                "reason": str(c.get("reason", ""))[:300],
                "quote": sanitize_caption(str(c.get("quote", "")))[:160],
            })
        except Exception:
            continue
    return clips


def signal_summary_text(signal: list[dict] | None, estimates: list[float] | None, total: float) -> str:
    if signal:
        top = sorted(signal, key=lambda m: -m["intensity"])[:8]
        return "; ".join(f"{m['start']:.0f}-{m['end']:.0f}s score {m['intensity']:.2f}" for m in top)
    if estimates:
        step = total / len(estimates)
        top_idx = sorted(range(len(estimates)), key=lambda i: -estimates[i])[:8]
        return "; ".join(f"{i * step:.0f}-{(i + 1) * step:.0f}s score {estimates[i]:.2f}" for i in top_idx)
    return "unavailable"


# ---------------------------------------------------------------------------
# mock

MOCK_TITLE = "The Hidden Economics of Attention (Mock Demo)"
MOCK_AUTHOR = "Hookline Sandbox"

def mock_transcript() -> list[dict]:
    lines = [
        (0, 4, "Everyone thinks viral videos are luck. I used the data and found the opposite."),
        (5, 4, "We tracked two million recommendation impressions over ninety days."),
        (10, 4, "And the pattern was hiding in one specific metric nobody watches."),
        (15, 4, "Here's the counterintuitive part. The best predictor isn't watch time."),
        (20, 4, "It's the rewatch signal. When viewers rewind, they vote with their thumb."),
        (26, 4, "Wait, let me say that again because it changes everything about editing."),
        (31, 4, "Rewind traffic predicted viral spread with eighty percent accuracy."),
        (36, 4, "So we rebuilt our whole editing pipeline around this one number."),
        (42, 4, "First test. A nineteen minute video, cut down to one sharp hook."),
        (47, 4, "The result shocked even me. Eleven million views in four days."),
        (53, 4, "But there's a catch, and most creators miss it completely."),
        (58, 4, "The hook must pay off inside eight seconds or rewind turns into skip."),
        (64, 4, "Here's exactly how we structure the first eight seconds now."),
        (69, 4, "Step one. Open with the payoff promise, not the setup."),
        (74, 4, "Step two. Cut every word that doesn't earn the next second."),
        (80, 4, "Step three. Land the payoff before the viewer's thumb decides."),
        (86, 4, "And the final result? Retention graphs that look impossible."),
        (92, 4, "We took a dead channel from four hundred to ninety thousand subs."),
        (98, 4, "Not with luck. With signal. That's the whole game."),
    ]
    return [{"start": s, "duration": d, "text": t} for s, d, t in lines]


def mock_signal() -> list[dict]:
    return [
        {"start": 20.0, "end": 30.0, "intensity": 0.94},
        {"start": 44.0, "end": 52.0, "intensity": 0.88},
        {"start": 64.0, "end": 84.0, "intensity": 0.91},
        {"start": 86.0, "end": 102.0, "intensity": 0.83},
    ]


def mock_clips(duration: str) -> list[dict]:
    base = [
        {"start": 20.0, "end": 44.0, "title": "The rewatch signal nobody watches", "reason": "Counterintuitive claim restated, highest replay density in the video", "quote": "It's the rewatch signal. When viewers rewind, they vote with their thumb."},
        {"start": 44.0, "end": 66.0, "title": "Eleven million views from one edit", "reason": "Concrete result with numbers right after a shock statement", "quote": "Eleven million views in four days."},
        {"start": 64.0, "end": 90.0, "title": "The 8-second payoff rule", "reason": "Actionable framework delivered step by step", "quote": "The hook must pay off inside eight seconds or rewind turns into skip."},
        {"start": 86.0, "end": 102.0, "title": "Four hundred to ninety thousand subs", "reason": "Closing payoff with a big numeric before-after", "quote": "We took a dead channel from four hundred to ninety thousand subs."},
    ]
    span = {"15s": (8, 20), "30s": (18, 40), "60s": (35, 75)}.get(duration, (18, 40))
    for c in base:
        length = min(c["end"] - c["start"], random.uniform(span[0], span[1]))
        c["end"] = c["start"] + length
    return base


# ---------------------------------------------------------------------------
# endpoints

@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "message": "hookline api active", "v2": True}


@app.get("/api/video-title")
def video_title(video_id: str) -> dict:
    return get_video_title(video_id)


# ---------------------------------------------------------------------------
# v2: render studio


class RenderBatchRequest(BaseModel):
    video_id: str
    clips: list[dict]
    opts: dict = {}


class FinalCutRequest(BaseModel):
    video_id: str
    clips: list[dict]
    opts: dict = {}


@app.post("/api/final-cut")
async def final_cut(req: FinalCutRequest) -> dict:
    """One finished vertical video: render the selected moments, then stitch them with transitions."""
    vid = source_id(req.video_id)
    if not req.clips:
        return {"error": "no clips selected for the final cut"}
    clean: list[dict] = []
    for c in req.clips[:12]:
        try:
            clean.append(
                {
                    "start": float(c.get("start", 0)),
                    "end": float(c.get("end", 0)),
                    "title": str(c.get("title", ""))[:80],
                    "quote": str(c.get("quote", ""))[:160],
                    "subtitles": _clean_subs(c.get("subtitles")),
                    "words": _clean_words(c.get("words")),
                }
            )
        except (TypeError, ValueError):
            continue
    if not clean:
        return {"error": "clips have invalid timestamps"}
    opts = dict(req.opts)
    opts["transition"] = str(opts.get("transition", "fade"))[:16]
    try:
        opts["xfade"] = max(0.0, min(1.5, float(opts.get("xfade", 0.4))))
    except (TypeError, ValueError):
        opts["xfade"] = 0.4
    opts["order"] = "value" if str(opts.get("order", "")) == "value" else "chronological"
    job_id = ve.new_job(vid, clean, opts, kind="final")
    asyncio.get_running_loop().run_in_executor(None, ve.run_final_job, job_id, bool(opts.get("cookies", False)))
    return {"job_id": job_id, "clips": len(clean)}


_CAPS_CACHE: dict | None = None


@app.get("/api/capabilities")
def capabilities() -> dict:
    global _CAPS_CACHE
    if _CAPS_CACHE is None:
        ff = ve.ff_available()
        try:
            import cv2  # noqa: F401
            has_cv2 = hasattr(cv2, "CascadeClassifier") and hasattr(cv2, "data")
        except ImportError:
            has_cv2 = False
        _CAPS_CACHE = {
            "ffmpeg": ff["available"],
            "ffmpeg_version": ff.get("ffmpeg", ""),
            "nvenc": ff.get("nvenc", False),
            "cv2": has_cv2,
            "ytdlp": True,
            "server_key": bool(SERVER_GEMINI_KEY),
            "presets": list(ve.PRESETS.keys()),
            "aspects": list(ve.ASPECTS.keys()),
            "transitions": list(ve.TRANSITIONS),
            "max_upload_mb": ve.UPLOAD_MAX_MB,
            "layouts": list(ve.LAYOUTS),
            "backdrops": list(ve.BACKDROPS),
        }
    return _CAPS_CACHE


@app.post("/api/render-batch")
async def render_batch(req: RenderBatchRequest) -> dict:
    vid = source_id(req.video_id)
    clean = []
    for c in req.clips:
        try:
            clean.append({
                "start": float(c["start"]),
                "end": float(c["end"]),
                "title": str(c.get("title", "clip"))[:80],
                "quote": str(c.get("quote", "")),
                "subtitles": c.get("subtitles") if isinstance(c.get("subtitles"), list) else [],
                "words": _clean_words(c.get("words")),
            })
        except Exception:
            continue
    if not clean:
        return {"error": "no valid clips"}
    opts = ve.normalize_opts(req.opts)
    cookie_mode = bool(opts.get("cookies", False))
    job_id = ve.new_job(vid, clean, opts)
    asyncio.get_running_loop().run_in_executor(None, ve.run_job, job_id, cookie_mode)
    return {"job_id": job_id, "total": len(clean)}


@app.get("/api/render-progress/{job_id}")
def render_progress(job_id: str) -> dict:
    p = ve.job_progress(job_id)
    if p is None:
        return {"error": "unknown job"}
    return p


@app.get("/api/download-rendered/{name}")
def download_rendered(name: str):
    p = ve.EXPORTS / name
    if not p.exists() or ".." in name or "/" in name or "\\" in name:
        raise HTTPException(404, "not found")
    return FileResponse(p, filename=p.name)


@app.get("/api/download-batch-zip/{job_id}")
def download_batch_zip(job_id: str):
    j = ve.JOBS.get(job_id)
    if not j or not j.get("zip"):
        raise HTTPException(404, "zip not ready")
    p = Path(j["zip"])
    if not p.exists():
        raise HTTPException(404, "zip not ready")
    return FileResponse(p, filename=p.name)


class RawDlRequest(BaseModel):
    video_id: str
    cookies: bool = False


@app.post("/api/download-raw-video")
async def download_raw(req: RawDlRequest) -> dict:
    vid = source_id(req.video_id)
    dl_id = ve.start_raw_download(vid, req.cookies)
    asyncio.get_running_loop().run_in_executor(None, ve.run_raw_download, dl_id, req.cookies)
    return {"dl_id": dl_id}


@app.get("/api/download-raw-status/{dl_id}")
def raw_status(dl_id: str) -> dict:
    s = ve.raw_download_status(dl_id)
    if s is None:
        return {"error": "unknown"}
    return s


@app.get("/api/clip-frame")
def clip_frame(video_id: str, t: float) -> dict:
    vid = source_id(video_id)
    src = ve.source_path(vid) or ve.sandbox_source_path(vid)
    if src is None:
        return {"available": False, "reason": "source not downloaded"}
    fr = ve.extract_frame(src, t)
    if fr is None:
        return {"available": False, "reason": "extract failed"}
    return {"available": True, "path": f"/api/frame-file/{fr.name}"}


@app.get("/api/frame-file/{name}")
def frame_file(name: str):
    p = ve.TEMP / name
    if not p.exists() or ".." in name or "/" in name or "\\" in name:
        raise HTTPException(404, "not found")
    return FileResponse(p, media_type="image/jpeg")


class CookiesBody(BaseModel):
    text: str = ""


@app.get("/api/cookies")
def cookies_get() -> dict:
    return ve.cookies_status()


@app.post("/api/cookies")
def cookies_post(body: CookiesBody) -> dict:
    return ve.save_cookies(body.text)


@app.delete("/api/cookies")
def cookies_delete() -> dict:
    return ve.delete_cookies()


@app.get("/api/temp-storage-info")
def temp_info() -> dict:
    return ve.temp_storage_info()


@app.post("/api/clear-temp")
def clear_temp() -> dict:
    return ve.clear_temp()


# ---------------------------------------------------------------------------
# uploaded clips (user sends their own video instead of a YouTube link)


@app.get("/api/uploads")
def uploads_list() -> dict:
    return {"uploads": ve.list_uploads(), "max_mb": ve.UPLOAD_MAX_MB}


@app.post("/api/upload")
async def upload_clip(file: UploadFile = File(...)) -> dict:
    ext = (Path(file.filename or "").suffix or ".mp4").lower()
    if ext not in ve.UPLOAD_EXT:
        raise HTTPException(400, f"Unsupported file type {ext}. Use MP4, MOV, M4V, MKV or WEBM.")
    uid = ve.new_upload_id()
    dest = ve.UPLOADS / f"{uid}{ext}"
    limit = ve.UPLOAD_MAX_MB * 1024 * 1024
    size = 0
    try:
        with dest.open("wb") as fh:
            while True:
                chunk = await file.read(1 << 22)
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, f"File larger than the {ve.UPLOAD_MAX_MB} MB cap. Cut it down first.")
                fh.write(chunk)
    except HTTPException:
        dest.unlink(missing_ok=True)
        ve.upload_meta_path(uid).unlink(missing_ok=True)
        raise
    except OSError:
        dest.unlink(missing_ok=True)
        raise HTTPException(507, "Cannot write the upload (disk full or permission denied).")
    finally:
        await file.close()
    try:
        meta = ve.register_upload(uid, ext, file.filename or f"{uid}{ext}")
    except FileNotFoundError:
        raise HTTPException(400, "That file could not be read as video. Re-export it as MP4 and retry.")
    if meta["duration"] < 5:
        ve.delete_upload(uid)
        raise HTTPException(400, "Clip shorter than 5 seconds - nothing to cut.")
    if meta["duration"] > 3 * 3600:
        ve.delete_upload(uid)
        raise HTTPException(400, "Clip longer than 3 hours. Split it before uploading.")
    return meta


@app.delete("/api/uploads/{upload_id}")
def upload_delete(upload_id: str) -> dict:
    uid = source_id(upload_id)
    return {"deleted": ve.delete_upload(uid)}


@app.get("/api/source-video/{video_id}")
def source_video(video_id: str):
    """Stream the local source (uploaded clip or downloaded YouTube video) for preview."""
    vid = source_id(video_id)
    src = ve.upload_source(vid) or ve.source_path(vid) or ve.sandbox_source_path(vid)
    if src is None:
        raise HTTPException(404, "source not available")
    return FileResponse(src, media_type="video/mp4", filename=src.name)


def _clean_words(raw) -> list[dict]:
    """Sanitize per-word timings from any client payload shape (legacy data stored a count here)."""
    out: list[dict] = []
    for w in raw if isinstance(raw, list) else []:
        if not isinstance(w, dict):
            continue
        try:
            t = float(w.get("t", 0))
        except (TypeError, ValueError):
            continue
        txt = str(w.get("w", ""))[:40].strip()
        if txt:
            out.append({"t": round(t, 3), "w": txt})
        if len(out) >= 160:
            break
    return out


def _clean_subs(raw) -> list[dict]:
    out: list[dict] = []
    for s in raw if isinstance(raw, list) else []:
        if not isinstance(s, dict):
            continue
        try:
            st = float(s.get("start", 0))
            du = float(s.get("duration", 2))
        except (TypeError, ValueError):
            continue
        txt = str(s.get("text", ""))[:240].strip()
        if txt:
            out.append({"start": st, "duration": du, "text": txt})
        if len(out) >= 80:
            break
    return out


CHUNK_SECONDS = 600



def load_upload_meta(uid: str) -> dict:
    try:
        return json.loads(ve.upload_meta_path(uid).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def model_chain(api_key: str, requested: str | None) -> list[str]:
    """Key-specific model truth first, static fallback chain after it."""
    discovered = discover_flash_models(api_key)
    if discovered:
        if requested and requested in discovered:
            return [requested] + [m for m in discovered if m != requested]
        return discovered
    if requested:
        return [requested] + [m for m in GEMINI_CHAIN if m != requested]
    return list(GEMINI_CHAIN)


def analyze_upload(req: AnalyzeRequest, effective_key: str, vid: str, upload_src: Path, started: float) -> AsyncIterator[str]:
    """Stream SSE for user-uploaded clip analysis using Gemini video understanding."""
    async def gen() -> AsyncIterator[str]:
        meta = load_upload_meta(vid)
        clip_ms = {"15s": 15, "30s": 30, "60s": 60}.get(req.duration, 30)
        yield await stage("meta", {"video_id": vid, "mock": False, "uploaded": True}, 0.05)
        title_data = {"video_id": vid, "title": str(meta.get("filename") or "Uploaded clip"), "author": "your upload"}
        yield sse("title", title_data)
        total = float(meta.get("duration") or ve.ffprobe_media(upload_src)["duration"])
        yield await stage("stage", {"stage": "transcript", "status": "done", "source": "video-understanding", "segments": 0})
        yield await stage("stage", {"stage": "signal", "status": "done", "mode": "model-timed", "points": 0})
        signal, estimates = None, None
        chain = model_chain(effective_key, req.model)
        yield sse("stage", {"stage": "analyze", "status": "discovered", "models": chain[:6]})
        windows: list[tuple[float, float]] = []
        pos = 0.0
        while pos < total - 1:
            span = min(CHUNK_SECONDS, total - pos)
            windows.append((pos, span))
            pos += span
        clips: list[dict] = []
        model_used = ""
        last_err = ""
        from google import genai

        vclient = genai.Client(api_key=effective_key)
        for wi, (ws, wspan) in enumerate(windows):
            proxy = await asyncio.to_thread(ve.analysis_copy, upload_src, vid, ws, wspan)
            if proxy is None:
                yield sse("error", {"stage": "analyze", "message": f"Could not prepare chunk {wi + 1} of {len(windows)} for analysis (ffmpeg failed)."})
                return
            yield sse("stage", {"stage": "analyze", "status": "run", "model": "uploading chunk", "attempt": wi + 1, "chunk": wi + 1, "chunks": len(windows)})
            try:
                vf = await asyncio.to_thread(gemini_upload_video, vclient, proxy)
            except Exception as e:
                yield sse("error", {"stage": "analyze", "message": f"Gemini could not ingest chunk {wi + 1}: {type(e).__name__}: {str(e)[:180]}"})
                return
            vprompt = build_video_prompt(clip_ms, req.custom_prompt, max(2, round(req.target_clip_count * wspan / total)), wspan, req.language)
            try:
                for mi, model in enumerate(chain):
                    yield await stage("stage", {"stage": "analyze", "status": "run", "model": model, "attempt": mi + 1, "chunk": wi + 1, "chunks": len(windows)}, 0.05)
                    try:
                        raw = await asyncio.to_thread(gemini_generate_on_file, vclient, model, vf, vprompt)
                        found = parse_video_clips(raw, ws, wspan)
                        if found:
                            model_used = model
                            clips.extend(found)
                            break
                        last_err = "model returned no usable clips"
                    except Exception as e:
                        last_err = f"{type(e).__name__}: {str(e)[:180]}"
                        yield sse("stage", {"stage": "analyze", "status": "retry", "model": model, "error": last_err})
                else:
                    yield sse("error", {"stage": "analyze", "message": f"Chunk {wi + 1} failed across the whole chain. {last_err}"})
                    return
            finally:
                gemini_delete_file(vclient, vf)
        yield sse("stage", {"stage": "analyze", "status": "done", "model": model_used, "clips": len(clips)})
        segments = [s for c in clips for s in c.get("captions") or []]
        for c in clips:
            c.pop("captions", None)
        clips = [enrich_clip(c, segments, signal, estimates, total) for c in clips]
        clips.sort(key=lambda c: (-c["value"], -c["score"]))
        del clips[req.target_clip_count:]
        if not clips:
            yield sse("error", {"stage": "analyze", "code": "no_value", "message": "No moment cleared the substance bar. Try a longer target length, a narrower prompt, or another source."})
            return
        yield sse("done", {
            "video_id": vid,
            "title": title_data.get("title", vid),
            "author": title_data.get("author", ""),
            "mock": False,
            "uploaded": True,
            "total_seconds": total,
            "signal_mode": "model-timed",
            "clips": clips,
            "model": model_used,
            "heatmap": [],
            "elapsed_ms": int((time.time() - started) * 1000),
            "segments": len(segments),
        })
    return gen()


@app.post("/api/analyze")
async def analyze(req: AnalyzeRequest) -> StreamingResponse:
    """Two input modes: YouTube URL (transcript+signal->Gemini picks substance) or upload_id (video itself->Gemini picks moments+captions)."""
    async def gen() -> AsyncIterator[str]:
        started = time.time()
        effective_key = req.api_key.strip() or SERVER_GEMINI_KEY
        is_mock = effective_key.lower() == "mock"

        if req.upload_id:
            try:
                vid = source_id(req.upload_id)
            except HTTPException:
                yield sse("error", {"stage": "resolve", "message": "Invalid upload id."})
                return
            upload_src = ve.upload_source(vid)
            if upload_src is None:
                yield sse("error", {"stage": "resolve", "message": "Uploaded clip is gone. Send it again."})
                return
            if is_mock:
                yield sse("error", {"stage": "analyze", "message": "Sandbox cannot watch your own clip - it has no model. Turn sandbox off and set GEMINI_API_KEY in backend/.env."})
                return
            async for line in analyze_upload(req, effective_key, vid, upload_src, started):
                yield line
            return

        vid = extract_video_id(req.url)
        if not vid:
            yield sse("error", {"stage": "resolve", "message": "Invalid YouTube URL. Use watch?v=, youtu.be, shorts, embed link, or an 11-char video ID."})
            return
        if not is_mock and not effective_key:
            yield sse("error", {"stage": "analyze", "code": "no_key", "message": "No Gemini key configured. Set GEMINI_API_KEY in backend/.env or enable sandbox mode."})
            return

        if is_mock:
            title_data = {"video_id": vid, "title": MOCK_TITLE, "author": MOCK_AUTHOR}
        else:
            title_data = get_video_title(vid)
        yield await stage("meta", {"video_id": vid, "mock": is_mock, "uploaded": False}, 0.05)
        yield sse("title", title_data)

        words: list[dict] = []
        if req.subtitles and req.subtitles.strip():
            segments = parse_manual_subtitles(req.subtitles)
            source = "manual"
            yield sse("stage", {"stage": "transcript", "status": "done", "source": "manual", "segments": len(segments)})
        elif is_mock:
            segments = mock_transcript()
            source = "mock"
            yield await stage("stage", {"stage": "transcript", "status": "done", "source": "mock", "segments": len(segments)})
        else:
            pref = tuple(dict.fromkeys((req.language, "id", "en")))
            segments, source = await asyncio.to_thread(fetch_transcript, vid, pref)
            words = await asyncio.to_thread(fetch_words, vid, pref) if segments else []
            yield await stage("stage", {"stage": "transcript", "status": "done", "source": source, "segments": len(segments or []), "words": len(words)})

        if not segments:
            yield sse("error", {"stage": "transcript", "message": "No transcript available for this video. Use manual subtitle paste or try another video."})
            return

        total = estimate_duration_from_transcript(segments)

        if is_mock:
            signal = mock_signal()
            yield await stage("stage", {"stage": "signal", "status": "done", "mode": "sandbox", "points": 4})
        else:
            signal = await asyncio.to_thread(probe_most_replayed, vid)
            if signal:
                yield sse("stage", {"stage": "signal", "status": "done", "mode": "youtube-real", "points": len(signal)})
            else:
                yield sse("stage", {"stage": "signal", "status": "done", "mode": "estimated", "points": 60})
        estimates = None if signal else build_signal_estimates(segments)

        if is_mock:
            await asyncio.sleep(0.6)
            clips = mock_clips(req.duration)
            model_used = "sandbox"
            yield sse("stage", {"stage": "analyze", "status": "done", "model": "sandbox", "clips": len(clips)})
        else:
            chain = model_chain(effective_key, req.model)
            yield sse("stage", {"stage": "analyze", "status": "discovered", "models": chain[:6]})
            summary = signal_summary_text(signal, estimates, total)
            # Chunked: small payloads survive Gemini load spikes and pin timestamps to the
            # window the model actually sees, instead of guessing across a 20-minute transcript.
            windows: list[tuple[float, float, list[dict]]] = []
            chunk = 720.0
            w_start = 0.0
            while w_start < total - 30:
                w_end = min(total, w_start + chunk)
                seg_w = [s for s in segments if float(s.get("start", 0)) < w_end and float(s.get("start", 0)) + float(s.get("duration", 2)) > w_start]
                if len(seg_w) >= 3:
                    windows.append((w_start, w_end, seg_w))
                w_start = w_end
            if len(windows) < 2:
                transcript, sampled = format_transcript(segments)
                windows = [(0.0, total, segments)]
                sampled_note = sampled
            clips = []
            model_used = ""
            last_err = ""
            for wi, (ws, we, seg_w) in enumerate(windows):
                transcript, sampled = format_transcript(seg_w, cap=11000)
                window_note = f"\nYou are analysing ONLY the window {ws:.0f}s to {we:.0f}s of a longer video. Timestamps must stay inside it." if len(windows) > 1 else ""
                prompt = build_analysis_prompt(transcript, req.duration, req.custom_prompt, summary, max(2, round(req.target_clip_count * (we - ws) / total)), total, sampled) + window_note
                for attempt_round in range(2):
                    hit = False
                    for i, model in enumerate(chain):
                        yield await stage("stage", {"stage": "analyze", "status": "run", "model": model, "attempt": i + 1, "window": wi + 1, "windows": len(windows)}, 0.05)
                        try:
                            raw, model_used = await asyncio.to_thread(gemini_generate, effective_key, model, prompt)
                            found = parse_gemini_clips(raw)
                            found = [c for c in found if ws <= c["start"] < we]
                            if found:
                                clips.extend(found)
                                hit = True
                                break
                        except Exception as e:
                            last_err = f"{type(e).__name__}: {str(e)[:180]}"
                            yield sse("stage", {"stage": "analyze", "status": "retry", "model": model, "error": last_err})
                            if "503" in last_err or "UNAVAILABLE" in last_err.upper():
                                await asyncio.sleep(2.5 if attempt_round == 0 else 6)
                    if hit or attempt_round == 1:
                        break
            if not clips:
                yield sse("error", {"stage": "analyze", "message": f"Gemini failed across the fallback chain. {last_err}"})
                return
            yield sse("stage", {"stage": "analyze", "status": "done", "model": model_used, "clips": len(clips)})

        clips = [enrich_clip(c, segments, signal, estimates, total) for c in clips]
        if words:
            for c in clips:
                c["words"] = [w for w in words if c["start"] - 0.4 <= float(w["t"]) <= c["end"] + 0.35]
        clips = [c for c in clips if c["source_confidence"] >= 0.45]
        clips.sort(key=lambda c: (-c["value"], -c["score"]))
        del clips[req.target_clip_count:]
        if not clips:
            yield sse("error", {"stage": "analyze", "code": "no_value", "message": "No moment cleared the substance bar. Try a longer target length, a narrower prompt, or another source."})
            return
        hp: list[list[float]] = []
        if signal:
            hp = [[round(m["start"], 1), round(m["intensity"], 3), round(m["end"], 1)] for m in signal]
        elif estimates:
            step = total / len(estimates)
            hp = [[round(i * step, 1), round(v, 3), round((i + 1) * step, 1)] for i, v in enumerate(estimates)]
        yield sse("done", {
            "video_id": vid,
            "title": title_data.get("title", vid),
            "author": title_data.get("author", ""),
            "mock": is_mock,
            "uploaded": False,
            "total_seconds": total,
            "signal_mode": ("youtube-real" if (signal and not is_mock) else ("mock" if is_mock else "estimated")),
            "clips": clips,
            "model": model_used,
            "heatmap": hp,
            "elapsed_ms": int((time.time() - started) * 1000),
            "segments": len(segments),
        })

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
