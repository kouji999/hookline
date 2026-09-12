"""Rewatch backend: FastAPI + SSE analyze pipeline.

Endpoints:
  GET  /api/health
  GET  /api/video-title?video_id=...
  POST /api/analyze   (SSE stream of pipeline stages)
"""

from __future__ import annotations

import asyncio
import json
import random
import re
import time
from pathlib import Path
from typing import Any, AsyncIterator

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import TranscriptsDisabled, NoTranscriptFound

import video_engine as ve

app = FastAPI(title="rewatch", version="1.0.0")

YT_OEMBED = "https://www.youtube.com/oembed"
import os

_INNERTUBE_KEY_CACHE: str | None = None


def innertube_key() -> str | None:
    """Resolve YouTube's public innertube client key at runtime (rotated server-side by Google,
    published on every watch page) — no hardcoded key in this repo."""
    global _INNERTUBE_KEY_CACHE
    if _INNERTUBE_KEY_CACHE:
        return _INNERTUBE_KEY_CACHE
    env = os.environ.get("REWATCH_INNERTUBE_KEY")
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
    url: str
    duration: str = "30s"
    api_key: str = ""
    model: str | None = None
    custom_prompt: str | None = None
    target_clip_count: int = 8
    subtitles: str | None = None


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


def transcript_to_text(segments: list[dict]) -> str:
    return " ".join(s.get("text", "").strip() for s in segments if s.get("text", " ").strip())


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


def fetch_transcript(video_id: str) -> tuple[list[dict] | None, str]:
    try:
        t = YouTubeTranscriptApi().fetch(video_id)
        segments = [{"start": snip.start, "duration": snip.duration, "text": snip.text} for snip in t]
        if segments:
            return segments, "youtube"
    except (TranscriptsDisabled, NoTranscriptFound):
        return None, "disabled"
    except Exception:
        return None, "error"
    return None, "error"


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


def build_analysis_prompt(transcript: str, duration: str, custom_prompt: str | None, signal_summary: str, target_count: int, total_seconds: float) -> str:
    clip_ms = {"15s": 15, "30s": 30, "60s": 60}.get(duration, 30)
    focus = f"\nFocus only on: {custom_prompt}" if custom_prompt else ""
    instr = f"""You are a short-form video strategist. Analyze this YouTube transcript with attention-signal data.

TRANSCRIPT:
{transcript[:24000]}

ATTENTION SIGNALS (most replayed regions, normalized 0-1):
{signal_summary}

Video duration: {total_seconds:.0f} seconds. Target clip length: {clip_ms} seconds. Extract up to {target_count} clips.{focus}

Return ONLY a JSON array (no markdown fences, no commentary). Each element:
{{"start": <seconds number>, "end": <seconds number>, "title": "<short punchy clip title>", "reason": "<why this moment is viral-worthy, one sentence>", "quote": "<verbatim 5-15 word quote from transcript>"}}
Rules: start/end are numbers in seconds; clips must not overlap; end-start within 10-100s; prefer moments near attention peaks; reason must reference hook/punchline/story/payoff; quote must be verbatim from transcript."""
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
            clips.append({
                "start": float(c["start"]),
                "end": float(c["end"]),
                "title": str(c.get("title", "Clip"))[:80],
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
MOCK_AUTHOR = "Rewatch Sandbox"

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
    return {"status": "ok", "message": "rewatch api active", "v2": True}


@app.get("/api/video-title")
def video_title(video_id: str) -> dict:
    return get_video_title(video_id)


# ---------------------------------------------------------------------------
# v2: render studio


class RenderBatchRequest(BaseModel):
    video_id: str
    clips: list[dict]
    opts: dict = {}


@app.get("/api/capabilities")
def capabilities() -> dict:
    ff = ve.ff_available()
    try:
        import cv2  # noqa: F401
        has_cv2 = True
    except ImportError:
        has_cv2 = False
    return {
        "ffmpeg": ff["available"],
        "ffmpeg_version": ff.get("ffmpeg", ""),
        "nvenc": ff.get("nvenc", False),
        "cv2": has_cv2,
        "ytdlp": True,
        "presets": list(ve.PRESETS.keys()),
        "aspects": list(ve.ASPECTS.keys()),
        "layouts": list(ve.LAYOUTS),
        "backdrops": list(ve.BACKDROPS),
    }


@app.post("/api/render-batch")
async def render_batch(req: RenderBatchRequest) -> dict:
    vid = extract_video_id(req.video_id) or req.video_id
    clean = []
    for c in req.clips:
        try:
            clean.append({
                "start": float(c["start"]),
                "end": float(c["end"]),
                "title": str(c.get("title", "clip"))[:80],
                "quote": str(c.get("quote", "")),
                "subtitles": c.get("subtitles") or [],
            })
        except Exception:
            continue
    if not clean:
        return {"error": "no valid clips"}
    cookie_mode = bool(req.opts.get("cookies", False))
    job_id = ve.new_job(vid, clean, dict(req.opts))
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
        from fastapi import HTTPException

        raise HTTPException(404, "not found")
    return FileResponse(p, filename=p.name)


@app.get("/api/download-batch-zip/{job_id}")
def download_batch_zip(job_id: str):
    j = ve.JOBS.get(job_id)
    if not j or not j.get("zip"):
        from fastapi import HTTPException

        raise HTTPException(404, "zip not ready")
    p = Path(j["zip"]) if False else __import__("pathlib").Path(j["zip"])
    return FileResponse(p, filename=p.name)


class RawDlRequest(BaseModel):
    video_id: str
    cookies: bool = False


@app.post("/api/download-raw-video")
async def download_raw(req: RawDlRequest) -> dict:
    vid = extract_video_id(req.video_id) or req.video_id
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
    vid = extract_video_id(video_id) or video_id
    src = ve.source_path(vid)
    if src is None:
        return {"available": False, "reason": "source not downloaded"}
    fr = ve.extract_frame(src, t)
    if fr is None:
        return {"available": False, "reason": "extract failed"}
    return {"available": True, "path": f"/api/frame-file/{fr.name}"}


@app.get("/api/frame-file/{name}")
def frame_file(name: str):
    from fastapi import HTTPException

    p = ve.TEMP / name
    if not p.exists() or ".." in name:
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


@app.post("/api/analyze")
async def analyze(req: AnalyzeRequest) -> StreamingResponse:
    async def gen() -> AsyncIterator[str]:
        started = time.time()
        is_mock = req.api_key.strip().lower() == "mock"

        # resolve video id (strict: invalid URL = explicit error, no silent fallback)
        vid = extract_video_id(req.url)
        if not vid:
            yield sse("error", {"stage": "resolve", "message": "URL YouTube tidak valid. Pakai link watch?v=, youtu.be, shorts, embed, atau 11-char video ID." if req.url.strip()[-2:].lower() == "id" else "Invalid YouTube URL. Use watch?v=, youtu.be, shorts, embed link, or an 11-char video ID."})
            return
        yield await stage("meta", {"video_id": vid, "mock": is_mock}, 0.05)

        # title
        if is_mock:
            title_data = {"video_id": vid, "title": MOCK_TITLE, "author": MOCK_AUTHOR}
        else:
            await asyncio.to_thread(get_video_title, vid)  # warm
            title_data = get_video_title(vid)
        yield sse("title", title_data)

        # transcript
        if req.subtitles and req.subtitles.strip():
            segments = parse_manual_subtitles(req.subtitles)
            source = "manual"
            yield sse("stage", {"stage": "transcript", "status": "done", "source": "manual", "segments": len(segments)})
        elif is_mock:
            segments = mock_transcript()
            source = "mock"
            yield await stage("stage", {"stage": "transcript", "status": "done", "source": "mock", "segments": len(segments)})
        else:
            segments, source = fetch_transcript(vid)
            yield await stage("stage", {"stage": "transcript", "status": "done", "source": source, "segments": len(segments or [])})

        if not segments:
            yield sse("error", {"stage": "transcript", "message": "No transcript available for this video. Use manual subtitle paste or try another video."})
            return

        total = estimate_duration_from_transcript(segments)

        # attention signal
        if is_mock:
            signal = mock_signal()
            yield await stage("stage", {"stage": "signal", "status": "done", "mode": "youtube-real", "points": 4})
        else:
            signal = await asyncio.to_thread(probe_most_replayed, vid)
            if signal:
                yield sse("stage", {"stage": "signal", "status": "done", "mode": "youtube-real", "points": len(signal)})
            else:
                yield sse("stage", {"stage": "signal", "status": "done", "mode": "estimated", "points": 60})
        estimates = None if signal else build_signal_estimates(segments)

        # analysis
        if is_mock:
            await asyncio.sleep(0.6)
            clips = mock_clips(req.duration)
            model_used = "sandbox"
            yield sse("stage", {"stage": "analyze", "status": "done", "model": "sandbox", "clips": len(clips)})
        else:
            transcript = transcript_to_text(segments)
            summary = signal_summary_text(signal, estimates, total)
            prompt = build_analysis_prompt(transcript, req.duration, req.custom_prompt, summary, req.target_clip_count, total)
            # dynamic discovery first (key-specific truth), fallback to static chain
            discovered = await asyncio.to_thread(discover_flash_models, req.api_key)
            chain: list[str] = []
            if discovered:
                chain = discovered
                if req.model in discovered:
                    chain = [req.model] + [m for m in discovered if m != req.model]
            else:
                chain = [req.model] + [m for m in GEMINI_CHAIN if m != req.model] if req.model in GEMINI_CHAIN else GEMINI_CHAIN
            yield sse("stage", {"stage": "analyze", "status": "discovered", "models": chain[:6]})
            clips: list[dict] = []
            model_used = ""
            last_err = ""
            for i, model in enumerate(chain):
                yield await stage("stage", {"stage": "analyze", "status": "run", "model": model, "attempt": i + 1}, 0.1)
                try:
                    raw, model_used = await asyncio.to_thread(gemini_generate, req.api_key, model, prompt)
                    clips = parse_gemini_clips(raw)
                    if clips:
                        break
                except Exception as e:
                    last_err = f"{type(e).__name__}: {str(e)[:180]}"
                    yield sse("stage", {"stage": "analyze", "status": "retry", "model": model, "error": last_err})
            if not clips:
                yield sse("error", {"stage": "analyze", "message": f"Gemini failed across fallback chain. {last_err}"})
                return
            yield sse("stage", {"stage": "analyze", "status": "done", "model": model_used, "clips": len(clips)})

        # enrich + done
        for c in clips:
            c["score"] = score_clip_against_signal(c, signal, estimates, total)
        clips.sort(key=lambda c: -c["score"])
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
            "total_seconds": total,
            "signal_mode": ("youtube-real" if (signal and not is_mock) else ("mock" if is_mock else "estimated")),
            "clips": clips,
            "model": model_used,
            "heatmap": hp,
            "elapsed_ms": int((time.time() - started) * 1000),
        })

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
