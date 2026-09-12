"""Hookline v2 render engine: yt-dlp download, FFmpeg slice, face tracking, ASS karaoke captions, batch render, ZIP export, cookies, temp cleanup.

All FFmpeg/yt-dlp calls run in threads (FastAPI BackgroundTasks / to_thread).
Exports land in backend/exports, scratch in backend/temp.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
TEMP = BASE / "temp"
EXPORTS = BASE / "exports"
COOKIES = BASE / "cookies.txt"


def _resolve_tool(name: str, env_key: str) -> str:
    """Resolution order: explicit env var -> tools/ dir (project-provisioned) -> system PATH."""
    env = os.environ.get(env_key)
    if env:
        return env
    local = BASE.parent / "tools" / "ffmpeg" / "bin" / f"{name}.exe"
    if local.exists():
        return str(local)
    found = shutil.which(name)
    if found:
        return found
    local_noext = BASE.parent / "tools" / "ffmpeg" / "bin" / name
    if local_noext.exists():
        return str(local_noext)
    return name


FFMPEG = _resolve_tool("ffmpeg", "HOOKLINE_FFMPEG")
FFPROBE = _resolve_tool("ffprobe", "HOOKLINE_FFPROBE")

TEMP.mkdir(exist_ok=True)
EXPORTS.mkdir(exist_ok=True)

JOBS: dict[str, dict[str, Any]] = {}

ASPECTS = {
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
    "4:3": (1440, 1080),
    "16:9": (1920, 1080),
}

PRESETS = {
    "viral-pop": {"primary": "&H00FFFFFF&", "secondary": "&H003CCBF2&", "outline": "&H00000000&", "font": "Arial", "size": 58},
    "beast-punch": {"primary": "&H00FFFFFF&", "secondary": "&H0053F53C&", "outline": "&H00000000&", "font": "Arial", "size": 62},
    "cyber-violet": {"primary": "&H00FFFFFF&", "secondary": "&H00F22EE2&", "outline": "&H00101010&", "font": "Arial", "size": 56},
    "fire-red": {"primary": "&H00FFFFFF&", "secondary": "&H002B2BF5&", "outline": "&H00000000&", "font": "Arial", "size": 60},
    "electric-cyan": {"primary": "&H00101010&", "secondary": "&H00D9F221&", "outline": "&H00FFFFFF&", "font": "Arial", "size": 56},
    "golden-aura": {"primary": "&H00FFFFFF&", "secondary": "&H001BC4F2&", "outline": "&H00202020&", "font": "Arial", "size": 58},
    "clean-minimal": {"primary": "&H00FFFFFF&", "secondary": "&H00C8C8C8&", "outline": "&H00000000&", "font": "Arial", "size": 48},
}

LAYOUTS = ("fullscreen", "split", "pip")
BACKDROPS = ("blur", "black")

# ---------------------------------------------------------------------------
# helpers


def run(cmd: list[str], timeout: int = 600, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, cwd=str(cwd) if cwd else None)


def ffprobe_duration(path: Path) -> float:
    r = run([FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)], timeout=60)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def ff_available() -> dict:
    try:
        a = run([FFMPEG, "-version"], timeout=15)
        p = run([FFPROBE, "-version"], timeout=15)
        nv = run([FFMPEG, "-hide_banner", "-encoders"], timeout=20)
        has_nvenc = "h264_nvenc" in (nv.stdout or "")
        ok = a.returncode == 0 and p.returncode == 0
        return {"available": ok, "ffmpeg": a.stdout.splitlines()[0] if a.returncode == 0 and a.stdout else "", "nvenc": has_nvenc}
    except Exception as e:
        return {"available": False, "error": str(e)[:160], "nvenc": False}


# ---------------------------------------------------------------------------
# download


def download_source(video_id: str, cookie_mode: bool, log: list[str]) -> Path | None:
    """Download best <=1080p mp4 with audio into temp. Returns local path or None."""
    out_tpl = str(TEMP / f"{video_id}.%(ext)s")
    cmd = [
        "python", "-m", "yt_dlp",
        "-f", "bv*[height<=1080]+ba/b[height<=1080]/b",
        "--merge-output-format", "mp4",
        "--no-playlist", "--no-warnings",
        "-o", out_tpl,
        f"https://www.youtube.com/watch?v={video_id}",
    ]
    if cookie_mode and COOKIES.exists():
        cmd += ["--cookies", str(COOKIES)]
    r = run(cmd, timeout=900)
    log.append((r.stdout or r.stderr or "")[-400:])
    if r.returncode != 0:
        return None
    for cand in TEMP.glob(f"{video_id}.*"):
        if cand.suffix.lower() in (".mp4", ".mkv", ".webm"):
            return cand
    return None


def source_path(video_id: str) -> Path | None:
    for cand in TEMP.glob(f"{video_id}.*"):
        if cand.suffix.lower() in (".mp4", ".mkv", ".webm"):
            return cand
    return None


def make_sandbox_source(video_id: str) -> Path | None:
    """Deterministic local testsrc2 clip so mock flow can render end-to-end without network."""
    out = TEMP / f"{video_id}.mp4"
    if out.exists() and out.stat().st_size > 10000:
        return out
    run(
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30:duration=90", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(out)],
        timeout=300,
    )
    return out if out.exists() else None


# ---------------------------------------------------------------------------
# face tracking


def detect_face_track_points(src: Path, start: float, end: float, fps_sample: int = 5, log: list[str] | None = None) -> list[tuple[float, float]]:
    """Return list of (time_offset, center_x_ratio) using OpenCV Haar cascade. Fallback: center."""
    try:
        import cv2
    except ImportError:
        return [(0.0, 0.5)]
    cap = cv2.VideoCapture(str(src))
    if not cap.isOpened():
        return [(0.0, 0.5)]
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")  # type: ignore[attr-defined]
    pts: list[tuple[float, float]] = []
    t = start
    while t < end:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, frame = cap.read()
        if not ok:
            t += fps_sample
            continue
        h, w = frame.shape[:2]
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = cascade.detectMultiScale(gray, 1.15, 5, minSize=(int(w * 0.08), int(w * 0.08)))
        if len(faces) > 0:
            # largest face
            x, _y, fw, _fh = max(faces, key=lambda f: f[2] * f[3])
            pts.append((t - start, (x + fw / 2) / w))
        t += fps_sample
    cap.release()
    if not pts:
        return [(0.0, 0.5)]
    # smooth (moving avg window 3)
    sm: list[tuple[float, float]] = []
    for i, (t, cx) in enumerate(pts):
        w0 = pts[max(0, i - 1)][1]
        w2 = pts[min(len(pts) - 1, i + 1)][1]
        sm.append((t, (w0 + 2 * cx + w2) / 4))
    return sm


def crop_filter(pts: list[tuple[float, float]], src_w: int, crop_w: int) -> str:
    """Static crop centered on average detected face position."""
    if not pts:
        cxr = 0.5
    else:
        cxr = sum(p[1] for p in pts) / len(pts)
    cx = int(cxr * src_w)
    x = max(0, min(src_w - crop_w, int(cx - crop_w / 2)))
    return f"crop={crop_w}:ih:{x}:0"


# ---------------------------------------------------------------------------
# ASS karaoke captions


def _ass_color(hex_bgr: str) -> str:
    return hex_bgr


def build_ass(subs: list[dict], preset: str, video_h: int, margin_v: int) -> str:
    p = PRESETS.get(preset, PRESETS["viral-pop"])
    primary = _ass_color(p["primary"])
    secondary = _ass_color(p["secondary"])
    outline = _ass_color(p["outline"])
    fontsize = max(30, int(p["size"] * video_h / 1920))
    header = f"""[Script Info]
Title: hookline karaoke
ScriptType: v4.00+
PlayResX: 1080
PlayResY: {video_h}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Karaoke,{p['font']},{fontsize},{primary},{secondary},{outline},&H96000000&,1,0,0,0,100,100,0,0,1,3,1,2,60,60,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    def ts(t: float) -> str:
        h = int(t // 3600)
        m = int((t % 3600) // 60)
        s = t % 60
        return f"{h}:{m:02d}:{s:05.2f}"

    lines = []
    for seg in subs:
        words = str(seg.get("text", "")).strip().split()
        if not words:
            continue
        start = float(seg.get("start", 0))
        dur = max(0.6, float(seg.get("duration", 2)))
        # group words into chunks of max 4 for readability
        chunks = [words[i : i + 4] for i in range(0, len(words), 4)]
        chunk_dur = dur / len(chunks)
        for ci, chunk in enumerate(chunks):
            cs = start + ci * chunk_dur
            ce = cs + chunk_dur
            parts = []
            for w in chunk:
                k = max(8, int(chunk_dur * 100 / len(chunk)))
                parts.append(r"{\kf%d}%s" % (k, _ass_escape(w)))
            text = " ".join(parts)
            lines.append(f"Dialogue: 0,{ts(cs)},{ts(ce)},Karaoke,,0,0,0,,{text}")
    return header + "\n".join(lines) + "\n"


def _ass_escape(w: str) -> str:
    return w.replace("{", "(").replace("}", ")").replace("\n", " ")


def _ass_filter_arg(path: Path) -> str:
    """FFmpeg ass/subtitles filters on Windows mis-parse absolute paths (drive colon treated
    as protocol -> 'original_size' arg error). Emit a path relative to BASE and run ffmpeg with
    cwd=BASE so it always resolves: the .ass lives under temp/."""
    try:
        return path.resolve().relative_to(BASE.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def write_ass(video_id: str, clip_key: str, subs: list[dict], preset: str, video_h: int, margin_v: int) -> Path:
    path = TEMP / f"{video_id}.{clip_key}.ass"
    path.write_text(build_ass(subs, preset, video_h, margin_v), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# render one clip


def render_clip(
    src: Path,
    video_id: str,
    clip: dict,
    opts: dict,
    log: list[str],
) -> Path | None:
    start, end = float(clip["start"]), float(clip["end"])
    dur = max(1.0, end - start)
    aspect = opts.get("aspect", "9:16")
    out_w, out_h = ASPECTS.get(aspect, ASPECTS["9:16"])
    layout = opts.get("layout", "fullscreen")
    backdrop = opts.get("backdrop", "blur")
    face_track = bool(opts.get("face_track", False))
    preset = opts.get("preset", "viral-pop")
    subtitle_v = int(opts.get("subtitle_v", 260))
    title_text = str(opts.get("title") or clip.get("title") or "")
    use_nvenc = bool(opts.get("nvenc", True))
    subs: list[dict] = clip.get("subtitles") or []

    key = re.sub(r"[^a-zA-Z0-9]", "", f"{clip['start']:.0f}_{clip['end']:.0f}") + f"_{uuid.uuid4().hex[:6]}"
    out_path = EXPORTS / f"{video_id}_{key}.mp4"

    # probe src dims
    r = run([FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "json", str(src)], timeout=60)
    try:
        info = json.loads(r.stdout or "{}")
        stream = info["streams"][0]
        src_w, src_h = int(stream["width"]), int(stream["height"])
    except Exception:
        src_w, src_h = 1920, 1080

    # build crop/scale filter chain for target aspect
    if aspect == "9:16":
        crop_w = int(src_h * 9 / 16)
        if face_track:
            pts = detect_face_track_points(src, start, end, log=log)
            vf = crop_filter(pts, src_w, crop_w)
        else:
            x = max(0, (src_w - crop_w) // 2)
            vf = f"crop={crop_w}:ih:{x}:0"
        # then scale + pad/crop to target
        vf += f",scale={out_w}:{out_h}:force_original_aspect_ratio=increase,crop={out_w}:{out_h}"
    elif aspect == "1:1" or aspect == "4:3":
        vf = f"scale={out_w}:{out_h}:force_original_aspect_ratio=increase,crop={out_w}:{out_h}"
    else:
        # 16:9 letterbox into vertical canvas with backdrop
        if backdrop == "blur":
            vf = (
                f"split[a][b];[a]scale={out_w}:{out_h}:force_original_aspect_ratio=increase,crop={out_w}:{out_h},"
                f"gblur=sigma=28,eq=brightness=-0.12[bg];[b]scale={out_w}:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2"
            )
        else:
            vf = f"scale={out_w}:-2,pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2:color=black"
    if layout == "pip":
        # pip corner cam: duplicate scaled small overlay top-right
        vf += f",split[base][cam];[cam]scale={out_w // 4}:-2[camS];[base][camS]overlay={out_w - out_w // 4 - 36}:36"
    elif layout == "split":
        vf += f",split[t][g];[g]scale={out_w}:-2,vflip[gm];[t][gm]vstack"

    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{start:.2f}", "-t", f"{dur:.2f}", "-i", str(src)]
    filters = vf
    ass_used = False
    if subs:
        ass_used = True
        # build word karaoke from clip subtitles (timestamps relative to clip)
        rel_subs = []
        for s in subs:
            ss, ee = float(s.get("start", 0)), float(s.get("start", 0)) + float(s.get("duration", 2))
            if ee < start or ss > end:
                continue
            rel_subs.append({"start": max(0.0, ss - start), "duration": min(dur, ee - max(0.0, ss - start)), "text": s.get("text", "")})
        if rel_subs:
            ass_path = write_ass(video_id, key, rel_subs, preset, out_h, subtitle_v)
            filters += f",ass={_ass_filter_arg(ass_path)}"
    cmd += ["-vf", filters, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out_path)]
    run_cwd = BASE if ass_used else None
    if use_nvenc:
        # try nvenc first, fallback libx264
        nv_cmd = list(cmd)
        i = nv_cmd.index("-c:v")
        nv_cmd[i + 1] = "h264_nvenc"
        nv_cmd[nv_cmd.index("-preset") + 1] = "p4"
        try:
            rr = run(nv_cmd, timeout=900, cwd=run_cwd)
            if rr.returncode == 0 and out_path.exists() and out_path.stat().st_size > 10000:
                log.append(f"rendered nvenc {out_path.name}")
                return out_path
            log.append(f"nvenc failed, fallback x264: {(rr.stderr or '')[-200:]}")
        except Exception as e:
            log.append(f"nvenc exception: {e}")
    rr = run(cmd, timeout=900, cwd=run_cwd)
    if rr.returncode == 0 and out_path.exists() and out_path.stat().st_size > 10000:
        log.append(f"rendered x264 {out_path.name}")
        return out_path
    log.append(f"render FAILED: {(rr.stderr or '')[-300:]}")
    if out_path.exists():
        out_path.unlink()
    return None


def extract_frame(src: Path, t: float) -> Path | None:
    out = TEMP / f"frame_{uuid.uuid4().hex[:8]}.jpg"
    r = run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{t:.2f}", "-i", str(src), "-frames:v", "1", "-q:v", "3", str(out)], timeout=60)
    if r.returncode == 0 and out.exists():
        return out
    return None


# ---------------------------------------------------------------------------
# jobs


def new_job(video_id: str, clips: list[dict], opts: dict) -> str:
    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {
        "id": job_id,
        "video_id": video_id,
        "clips": clips,
        "opts": opts,
        "status": "queued",
        "items": [{"key": f"{c['start']:.0f}-{c['end']:.0f}", "status": "pending", "file": None, "error": None} for c in clips],
        "created": time.time(),
        "zip": None,
        "log": [],
    }
    return job_id


def job_progress(job_id: str) -> dict | None:
    j = JOBS.get(job_id)
    if not j:
        return None
    done = sum(1 for i in j["items"] if i["status"] == "done")
    return {
        "id": j["id"],
        "status": j["status"],
        "total": len(j["items"]),
        "done": done,
        "items": [{"key": i["key"], "status": i["status"], "file": i["file"]} for i in j["items"]],
        "zip": j["zip"],
        "log": j["log"][-6:],
    }


def build_zip(job_id: str) -> Path | None:
    j = JOBS.get(job_id)
    if not j:
        return None
    files = [Path(i["file"]) for i in j["items"] if i["file"] and Path(i["file"]).exists()]
    if not files:
        return None
    zp = EXPORTS / f"hookline_{job_id}.zip"
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_STORED) as zf:
        for f in files:
            zf.write(f, f.name)
    j["zip"] = str(zp)
    return zp


def run_job(job_id: str, cookie_mode: bool) -> None:
    """Blocking worker: download once, render each clip, zip. Called via to_thread."""
    j = JOBS.get(job_id)
    if not j:
        return
    j["status"] = "running"
    video_id = j["video_id"]
    src = source_path(video_id)
    if src is None:
        if j["opts"].get("sandbox"):
            src = make_sandbox_source(video_id)
        else:
            j["status"] = "downloading"
            src = download_source(video_id, cookie_mode, j["log"])
    if src is None:
        j["status"] = "failed"
        for i in j["items"]:
            i["status"] = "failed"
            i["error"] = "download failed"
        return
    for idx, clip in enumerate(j["clips"]):
        item = j["items"][idx]
        item["status"] = "rendering"
        # attach clip subtitles if provided
        out = render_clip(src, video_id, clip, j["opts"], j["log"])
        if out:
            item["status"] = "done"
            item["file"] = str(out)
        else:
            item["status"] = "failed"
            item["error"] = "render failed"
    zp = build_zip(job_id)
    j["status"] = "done" if all(i["status"] == "done" for i in j["items"]) else ("partial" if zp else "failed")


# ---------------------------------------------------------------------------
# raw download + temp


def start_raw_download(video_id: str, cookie_mode: bool) -> str:
    dl_id = uuid.uuid4().hex[:10]
    JOBS[f"raw_{dl_id}"] = {"id": dl_id, "kind": "raw", "video_id": video_id, "status": "queued", "pct": 0, "file": None, "created": time.time(), "log": []}
    return dl_id


def raw_download_status(dl_id: str) -> dict | None:
    j = JOBS.get(f"raw_{dl_id}")
    if not j:
        return None
    src = source_path(j["video_id"])
    if src and j["status"] in ("queued", "running"):
        j["status"] = "done"
        j["file"] = str(src)
        j["pct"] = 100
    return {"id": j["id"], "status": j["status"], "pct": j.get("pct", 0), "file": j.get("file")}


def run_raw_download(dl_id: str, cookie_mode: bool) -> None:
    j = JOBS.get(f"raw_{dl_id}")
    if not j:
        return
    j["status"] = "running"
    src = download_source(j["video_id"], cookie_mode, j["log"])
    if src:
        j["status"] = "done"
        j["file"] = str(src)
        j["pct"] = 100
    else:
        j["status"] = "failed"


def temp_storage_info() -> dict:
    files = [p for p in TEMP.rglob("*") if p.is_file()]
    total = sum(p.stat().st_size for p in files)
    return {"files": len(files), "bytes": total}


def clear_temp() -> dict:
    kept = 0
    removed = 0
    freed = 0
    for p in TEMP.rglob("*"):
        if not p.is_file():
            continue
        # keep source videos (re-render friendly); remove slices/ass/frames
        if p.suffix.lower() in (".mp4", ".mkv", ".webm") and re.fullmatch(r"[A-Za-z0-9_-]{11}\.[a-z0-9]+", p.name.lower()):
            kept += 1
            continue
        freed += p.stat().st_size
        p.unlink()
        removed += 1
    return {"removed": removed, "kept_sources": kept, "freed_bytes": freed}


def save_cookies(text: str) -> dict:
    COOKIES.write_text(text, encoding="utf-8")
    domains = sorted(set(re.findall(r"(?m)^([a-z0-9.\-]+\.[a-z]+)\t", text.lower())))
    return {"saved": True, "domains": domains[:12], "lines": len(text.splitlines())}


def cookies_status() -> dict:
    if not COOKIES.exists():
        return {"present": False}
    text = COOKIES.read_text(encoding="utf-8", errors="replace")
    domains = sorted(set(re.findall(r"(?m)^([a-z0-9.\-]+\.[a-z]+)\t", text.lower())))
    return {"present": True, "domains": domains[:12], "lines": len(text.splitlines())}


def delete_cookies() -> dict:
    if COOKIES.exists():
        COOKIES.unlink()
    return {"present": False}
