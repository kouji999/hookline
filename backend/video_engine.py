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
import sys
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
TEMP = BASE / "temp"
EXPORTS = BASE / "exports"
COOKIES = BASE / "cookies.txt"
UPLOADS = TEMP / "uploads"
ANALYSIS = TEMP / "analysis"


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
UPLOADS.mkdir(exist_ok=True)
ANALYSIS.mkdir(exist_ok=True)

JOBS: dict[str, dict[str, Any]] = {}

ASPECTS = {
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
    "4:3": (1440, 1080),
    "16:9": (1920, 1080),
}

PRESETS = {
    "mono-condensed": {"primary": "&H00FFFFFF&", "secondary": "&H007E7E7E&", "outline": "&H00000000&", "font": "Bahnschrift SemiBold Condensed", "size": 64},
    "mono-heavy": {"primary": "&H00FFFFFF&", "secondary": "&H007E7E7E&", "outline": "&H00000000&", "font": "Segoe UI Black", "size": 56},
    "mono-stone": {"primary": "&H00F0F0F0&", "secondary": "&H00707070&", "outline": "&H00050505&", "font": "Arial Black", "size": 54},
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
        ok = a.returncode == 0 and p.returncode == 0
        # real nvenc probe: encode 1 frame; driver/binary mismatch fails fast here, not mid-job
        has_nvenc = False
        probe = TEMP / "_nvenc_probe.mp4"
        try:
            r = run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=128x128:rate=10:duration=0.5", "-c:v", "h264_nvenc", "-preset", "p1", str(probe)], timeout=20)
            has_nvenc = r.returncode == 0 and probe.exists() and probe.stat().st_size > 512
        except Exception:
            has_nvenc = False
        finally:
            if probe.exists():
                probe.unlink()
        return {"available": ok, "ffmpeg": a.stdout.splitlines()[0] if a.returncode == 0 and a.stdout else "", "nvenc": has_nvenc}
    except Exception as e:
        return {"available": False, "error": str(e)[:160], "nvenc": False}


# ---------------------------------------------------------------------------
# download


def download_source(video_id: str, cookie_mode: bool, log: list[str]) -> Path | None:
    """Download best <=1080p mp4 with audio into temp. Returns local path or None."""
    out_tpl = str(TEMP / f"{video_id}.%(ext)s")
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "-f", "bv*[height<=1080]+ba/b[height<=1080]/b",
        "--merge-output-format", "mp4",
        "--no-playlist", "--no-warnings",
        "-o", out_tpl,
        f"https://www.youtube.com/watch?v={video_id}",
    ]
    if cookie_mode and COOKIES.exists():
        if cookies_are_valid():
            cmd += ["--cookies", str(COOKIES)]
        else:
            log.append("cookies file is malformed - ignoring it and retrying anonymously (fix or remove it in the studio)")
    r = run(cmd, timeout=900)
    log.append((r.stdout or r.stderr or "")[-400:])
    if r.returncode != 0:
        if cookie_mode and COOKIES.exists() and not cookies_are_valid():
            return None
        # a bot-check wall answers as an error with "cookies" advice: surface it instead of a dead end
        if "cookies" in (r.stderr or "").lower() or "sign in" in (r.stderr or "").lower():
            log.append("YouTube asked to sign in for this video - enable Use cookies with a real browser export, or pick another video")
        return None
    for cand in TEMP.glob(f"{video_id}.*"):
        if cand.suffix.lower() in (".mp4", ".mkv", ".webm"):
            return cand
    return None


def sandbox_source_path(video_id: str) -> Path | None:
    cand = TEMP / "sandbox" / f"{video_id}.mp4"
    return cand if cand.exists() and cand.stat().st_size > 10000 else None


def source_path(video_id: str) -> Path | None:
    """Exact final-merged names only; never match yt-dlp intermediates like {vid}.f399.mp4."""
    for ext in (".mp4", ".mkv", ".webm"):
        cand = TEMP / f"{video_id}{ext}"
        if cand.exists() and cand.stat().st_size > 10000:
            return cand
    return None


def make_sandbox_source(video_id: str) -> Path | None:
    """Deterministic local testsrc2 clip so mock flow can render end-to-end without network.
    Lives in temp/sandbox/ so it can NEVER collide with a real download at temp/{video_id}.mp4."""
    sandbox_dir = TEMP / "sandbox"
    sandbox_dir.mkdir(exist_ok=True)
    out = sandbox_dir / f"{video_id}.mp4"
    if out.exists() and out.stat().st_size > 10000:
        return out
    run(
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", "testsrc2=size=1920x1080:rate=30:duration=180",
         "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=180",
         "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "128k", "-shortest", str(out)],
        timeout=300,
    )
    return out if out.exists() else None


# ---------------------------------------------------------------------------
# uploads: user sends their own clip (mp4/mov/webm), analysed and cut locally


UPLOAD_EXT = (".mp4", ".mov", ".m4v", ".mkv", ".webm")
UPLOAD_MAX_MB = 2000


def new_upload_id() -> str:
    return "up" + uuid.uuid4().hex[:10]


def upload_meta_path(uid: str) -> Path:
    return UPLOADS / f"{uid}.json"


def upload_source(uid: str) -> Path | None:
    for ext in UPLOAD_EXT:
        cand = UPLOADS / f"{uid}{ext}"
        if cand.exists() and cand.stat().st_size > 10000:
            return cand
    return None


def register_upload(uid: str, ext: str, filename: str) -> dict:
    src = upload_source(uid)
    if src is None:
        raise FileNotFoundError(f"upload {uid} not found")
    info = ffprobe_media(src)
    meta = {
        "id": uid,
        "filename": filename[:200],
        "ext": ext,
        "created": time.time(),
        "bytes": src.stat().st_size,
        **info,
    }
    upload_meta_path(uid).write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return meta


def list_uploads() -> list[dict]:
    out: list[dict] = []
    for p in sorted(UPLOADS.glob("up*.json"), key=lambda q: q.stat().st_mtime, reverse=True):
        try:
            m = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if upload_source(str(m.get("id", ""))):
            out.append(m)
    return out


def delete_upload(uid: str) -> bool:
    src = upload_source(uid)
    if src is None:
        return False
    src.unlink(missing_ok=True)
    upload_meta_path(uid).unlink(missing_ok=True)
    return True


def ffprobe_media(path: Path) -> dict:
    cmd = [FFPROBE, "-v", "error", "-show_entries",
           "format=duration:stream=codec_type,width,height,r_frame_rate", "-of", "json", str(path)]
    r = run(cmd, timeout=60)
    out = {"duration": 0.0, "width": 0, "height": 0, "fps": 0.0, "has_audio": False}
    try:
        j = json.loads(r.stdout or "{}")
        out["duration"] = float(j.get("format", {}).get("duration") or 0)
        for s in j.get("streams", []):
            if s.get("codec_type") == "video" and not out["width"]:
                out["width"] = int(s.get("width") or 0)
                out["height"] = int(s.get("height") or 0)
                num, _, den = str(s.get("r_frame_rate") or "0/1").partition("/")
                try:
                    out["fps"] = round(float(num) / float(den or 1), 3)
                except (ValueError, ZeroDivisionError):
                    out["fps"] = 0.0
            if s.get("codec_type") == "audio":
                out["has_audio"] = True
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        pass
    return out


def analysis_copy(src: Path, uid: str, start: float, dur: float, height: int = 720) -> Path | None:
    """Small, cheap proxy of one chunk for model ingest (audio is what matters)."""
    dst = ANALYSIS / f"{uid}_{int(start)}.mp4"
    if dst.exists() and dst.stat().st_size > 20000:
        return dst
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
           "-ss", f"{start:.2f}", "-t", f"{dur:.2f}", "-i", str(src),
           "-vf", f"scale=-2:{height}", "-r", "24",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "32", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "96k", "-ac", "1", str(dst)]
    r = run(cmd, timeout=1800)
    if r.returncode == 0 and dst.exists() and dst.stat().st_size > 20000:
        return dst
    return None


def resolve_source(video_id: str, opts: dict, log: list[str]) -> Path | None:
    """Uploaded file > already-downloaded source > sandbox > YouTube download."""
    if video_id.startswith("up") and len(video_id) <= 24:
        up = upload_source(video_id)
        if up:
            return up
    if opts.get("sandbox"):
        return make_sandbox_source(video_id)
    existing = source_path(video_id)
    if existing:
        return existing
    return download_source(video_id, bool(opts.get("cookies")), log)


# ---------------------------------------------------------------------------
# face tracking


def detect_face_track_points(src: Path, start: float, end: float, fps_sample: int = 5, log: list[str] | None = None) -> list[tuple[float, float]]:
    """Sample frames via ffmpeg fast input-seek (reliable, hard timeout each) then Haar-detect
    faces on the JPEGs. Returns list of (time_offset, center_x_ratio); center fallback."""
    try:
        import cv2
    except ImportError:
        return [(0.0, 0.5)]
    lg = log if log is not None else []
    sample_dir = TEMP / "_ft"
    sample_dir.mkdir(exist_ok=True)
    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")  # type: ignore[attr-defined]
    pts: list[tuple[float, float]] = []
    t = start
    idx = 0
    while t < end:
        jpg = sample_dir / f"s{idx}.jpg"
        idx += 1
        try:
            r = run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{t:.2f}", "-i", str(src), "-frames:v", "1", "-q:v", "4", str(jpg)], timeout=30)
        except Exception:
            r = None
        if r is not None and r.returncode == 0 and jpg.exists() and jpg.stat().st_size > 1000:
            frame = cv2.imread(str(jpg))
            jpg.unlink()
            if frame is not None:
                h, w = frame.shape[:2]
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                faces = cascade.detectMultiScale(gray, 1.15, 5, minSize=(int(w * 0.08), int(w * 0.08)))
                if len(faces) > 0:
                    x, _y, fw, _fh = max(faces, key=lambda f: f[2] * f[3])
                    pts.append((t - start, (x + fw / 2) / w))
        t += fps_sample
    if not pts:
        lg.append("face-track: no faces in range, using center crop")
        return [(0.0, 0.5)]
    sm: list[tuple[float, float]] = []
    for i, (t, cx) in enumerate(pts):
        w0 = pts[max(0, i - 1)][1]
        w2 = pts[min(len(pts) - 1, i + 1)][1]
        sm.append((t, (w0 + 2 * cx + w2) / 4))
    lg.append(f"face-track: {len(pts)} samples detected")
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


def format_hook_title(text: str, max_line: int = 26, max_lines: int = 2) -> list[str]:
    """Pre-wrap the top title so it NEVER runs off frame. Smart word wrap,
    overflow collapses to the last line with an ellipsis."""
    words = re.sub(r"\s+", " ", str(text or "")).strip().split()
    if not words:
        return []
    lines: list[str] = []
    cur = ""
    for w in words:
        cand = f"{cur} {w}".strip()
        if cur and len(cand) > max_line:
            lines.append(cur)
            cur = w
        else:
            cur = cand
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        rest = " ".join(lines[max_lines - 1 :])
        cut = rest[: max_line - 1]
        if len(rest) > len(cut):
            cut = cut.rsplit(" ", 1)[0] if " " in cut else cut[: max_line - 2]
        lines = lines[: max_lines - 1] + [cut.strip() + "…"]
    return [l for l in lines if l]


def hook_size_factor(lines: list[str], base_size: int) -> int:
    """Shrink the banner when the longest line is long, so it stays inside the margins."""
    if not lines:
        return base_size
    longest = max(len(l) for l in lines)
    if longest <= 16:
        return base_size
    if longest <= 20:
        return int(base_size * 0.93)
    if longest <= 24:
        return int(base_size * 0.85)
    return int(base_size * 0.76)


def _ass_color(hex_bgr: str) -> str:
    return hex_bgr


def build_ass(subs: list[dict], preset: str, video_h: int, margin_v: int, hook: str = "", hook_dur: float = 3.6) -> str:
    p = PRESETS.get(preset, PRESETS["viral-pop"])
    primary = _ass_color(p["primary"])
    secondary = _ass_color(p["secondary"])
    outline = _ass_color(p["outline"])
    fontsize = max(30, int(p["size"] * video_h / 1920))
    hook_lines = format_hook_title(hook)
    hooksize = max(30, hook_size_factor(hook_lines, int(fontsize * 1.05)))
    # safe-area margins: top banner sits below the phone notch zone, captions above the UI zone
    hook_margin = max(150, int(video_h * 0.11))
    side = max(70, int(1080 * 0.085))
    header = f"""[Script Info]
Title: hookline karaoke
ScriptType: v4.00+
PlayResX: 1080
PlayResY: {video_h}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Karaoke,{p['font']},{fontsize},{primary},{secondary},{outline},&H96000000&,1,0,0,0,100,100,0,0,1,4,2,2,{side},{side},{margin_v},1
Style: Hook,{p['font']},{hooksize},{primary},&H00000000&,&H00000000&,&HCC000000&,1,0,0,0,100,100,1.5,0,3,0,0,8,{side},{side},{hook_margin},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    def ts(t: float) -> str:
        h = int(t // 3600)
        m = int((t % 3600) // 60)
        s = t % 60
        return f"{h}:{m:02d}:{s:05.2f}"

    lines = []
    if hook_lines:
        text = "\\N".join(_ass_escape(l) for l in hook_lines)
        lines.append(f"Dialogue: 1,{ts(0.25)},{ts(hook_dur)},Hook,,0,0,0,,{text}")
    for seg in subs:
        start = float(seg.get("start", 0))
        dur = max(0.6, float(seg.get("duration", 2)))
        wlist = seg.get("words")
        if wlist:
            # real per-word timings from the source caption track: the highlight IS the speech
            seg_end = start + dur
            for i in range(0, len(wlist), 3):
                grp = wlist[i:i + 3]
                gs = start + float(grp[0].get("t", 0))
                nxt = wlist[i + 3] if i + 3 < len(wlist) else None
                ge = start + (float(nxt.get("t", 0)) if nxt else min(seg_end, float(grp[-1].get("t", 0)) + float(grp[-1].get("d", 0.4))))
                ge = min(ge, seg_end)
                if ge - gs < 0.25:
                    ge = gs + 0.25
                parts = []
                for j, wd in enumerate(grp):
                    t0 = float(wd.get("t", 0))
                    t1 = float(grp[j + 1].get("t", 0)) if j + 1 < len(grp) else (float(nxt.get("t", 0)) if nxt else t0 + float(wd.get("d", 0.4)))
                    k = max(6, int((t1 - t0) * 100))
                    parts.append(r"{\kf%d}%s" % (k, _ass_escape(str(wd.get("w", "")))))
                lines.append(f"Dialogue: 0,{ts(gs)},{ts(ge)},Karaoke,,0,0,0,," + " ".join(parts))
            continue
        words = str(seg.get("text", "")).strip().split()
        if not words:
            continue
        # group words into chunks of max 4 for readability
        chunks = [words[i : i + 3] for i in range(0, len(words), 3)]
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


def write_ass(video_id: str, clip_key: str, subs: list[dict], preset: str, video_h: int, margin_v: int, hook: str = "") -> Path:
    path = TEMP / f"{video_id}.{clip_key}.ass"
    path.write_text(build_ass(subs, preset, video_h, margin_v, hook), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# render one clip


def merge_overlapping_subs(subs: list[dict]) -> list[dict]:
    """YouTube ASR segments overlap (rolling windows). Stacked ASS events render two
    captions at once. Collapse overlaps into one readable event: extend the window,
    append only genuinely new words."""
    out: list[dict] = []
    for s in sorted(subs, key=lambda x: (float(x.get("start", 0)), -float(x.get("duration", 0)))):
        try:
            st = float(s.get("start", 0))
            du = max(0.3, float(s.get("duration", 1.5)))
        except (TypeError, ValueError):
            continue
        txt = re.sub(r"\s+", " ", str(s.get("text", ""))).strip()
        if not txt:
            continue
        if out and st < float(out[-1]["start"]) + float(out[-1]["duration"]) - 0.05:
            prev = out[-1]
            prev_words = prev["text"].lower().split()
            new_words = txt.split()
            overlap_len = 0
            max_o = min(len(prev_words), len(new_words), 12)
            for k in range(max_o, 0, -1):
                if prev_words[-k:] == [w.lower() for w in new_words[:k]]:
                    overlap_len = k
                    break
            tail = new_words[overlap_len:]
            if tail:
                prev["text"] = (prev["text"] + " " + " ".join(tail)).strip()[:240]
            end_prev = float(prev["start"]) + float(prev["duration"])
            end_new = st + du
            if end_new > end_prev:
                prev["duration"] = round(end_new - float(prev["start"]), 2)
            continue
        out.append({"start": round(st, 3), "duration": round(du, 3), "text": txt})
    return out


def relative_subtitles(subs: list[dict], start: float, dur: float) -> list[dict]:
    """Convert absolute-time transcript segments to clip-relative windows (0..dur).
    Clips each segment to [start, start+dur]; drops non-overlapping ones."""
    rel = []
    for s in subs:
        try:
            ss = float(s.get("start", 0))
            sdur = float(s.get("duration", 2))
        except (TypeError, ValueError):
            continue
        ee = ss + max(0.4, sdur)
        if ee <= start or ss >= start + dur:
            continue
        rel_s = max(0.0, ss - start)
        rel_e = min(dur, ee - start)
        text = str(s.get("text", "")).strip()
        if text and rel_e - rel_s >= 0.3:
            rel.append({"start": rel_s, "duration": rel_e - rel_s, "text": text})
    return merge_overlapping_subs(rel)


def detect_dead_air(src: Path, start: float, end: float, log: list[str]) -> list[tuple[float, float]]:
    """Speech ranges inside [start, end]. Cuts pauses so the clip never idles.
    Conservative: silence >= 0.55s under -32dBFS dropped, 0.15s pad kept."""
    r = run([FFMPEG, "-hide_banner", "-nostats", "-ss", f"{start:.2f}", "-t", f"{end - start:.2f}", "-i", str(src),
             "-af", "silencedetect=noise=-32dB:d=0.55", "-f", "null", "-"], timeout=300)
    txt = (r.stderr or "") + (r.stdout or "")
    sil: list[tuple[float, float]] = []
    cur: float | None = None
    for m in re.finditer(r"silence_(start|end):\s*([0-9.]+)", txt):
        v = float(m.group(2))
        if m.group(1) == "start":
            cur = v
        elif cur is not None:
            sil.append((cur, v))
            cur = None
    if not sil:
        return [(start, end)]
    dur = end - start
    pad = 0.15
    bounds = [(0.0, sil[0][0])] + [(a[1], b[0]) for a, b in zip(sil, sil[1:])] + [(sil[-1][1], dur)]
    keep: list[tuple[float, float]] = []
    for gs, ge in bounds:
        s = max(0.0, gs + pad)
        e = min(dur, ge - pad)
        if e - s >= 1.2:
            keep.append((start + s, start + e))
    if not keep:
        return [(start, end)]
    kept = sum(b - a for a, b in keep)
    removed = dur - kept
    if removed < 1.0 or (len(keep) == 1 and kept / dur > 0.92):
        return [(start, end)]
    log.append(f"tighten: {len(keep)} speech ranges, cut {removed:.1f}s dead air")
    return keep


def _range_offsets(ranges: list[tuple[float, float]]) -> list[float]:
    offs, t = [], 0.0
    for a, b in ranges:
        offs.append(t)
        t += b - a
    return offs


def words_to_rel_events(words: list[dict], ranges: list[tuple[float, float]], max_words: int = 3) -> list[dict]:
    """Absolute per-word timings -> output-relative caption events on the (possibly tightened)
    timeline. This is what makes the highlight track actual speech instead of a metronome."""
    if not words:
        return []
    offs = _range_offsets(ranges)
    mapped: list[tuple[float, str]] = []
    for w in words:
        try:
            t = float(w.get("t", 0))
        except (TypeError, ValueError):
            continue
        txt = str(w.get("w", "")).strip()
        if not txt:
            continue
        for (a, b), base in zip(ranges, offs):
            if a <= t <= b:
                mapped.append((base + t - a, txt))
                break
    mapped.sort(key=lambda x: x[0])
    if not mapped:
        return []
    out: list[list[tuple[float, str]]] = []
    cur: list[tuple[float, str]] = []
    for t, w in mapped:
        if cur and (len(cur) >= max_words or t - cur[-1][0] > 1.4 or t - cur[0][0] > 2.6):
            out.append(cur)
            cur = []
        cur.append((t, w))
    if cur:
        out.append(cur)
    events = []
    for gi, grp in enumerate(out):
        st = grp[0][0]
        nxt_start = out[gi + 1][0][0] if gi + 1 < len(out) else None
        en = grp[-1][0] + max(0.35, (grp[-1][0] - grp[-2][0]) if len(grp) > 1 else 0.45)
        if nxt_start is not None:
            en = min(en, nxt_start)
        if en - st < 0.3:
            en = st + 0.3
        events.append({
            "start": round(st, 3),
            "duration": round(max(0.4, en - st), 3),
            "text": " ".join(w for _, w in grp),
            "words": [{"w": w, "t": round(t - st, 3)} for t, w in grp],
        })
    return events


def remap_captions(segs: list[dict], ranges: list[tuple[float, float]]) -> list[dict]:
    """Absolute caption windows -> output-relative timeline of the tightened cut."""
    out: list[dict] = []
    for (a, b), base in zip(ranges, _range_offsets(ranges)):
        for s in segs:
            try:
                ss, sd = float(s.get("start", 0)), float(s.get("duration", 2))
            except (TypeError, ValueError):
                continue
            se = ss + sd
            if se <= a or ss >= b:
                continue
            n_s, n_e = max(ss, a), min(se, b)
            if n_e - n_s < 0.35:
                continue
            out.append({"start": round(base + (n_s - a), 3), "duration": round(n_e - n_s, 3), "text": str(s.get("text", ""))})
    out.sort(key=lambda x: x["start"])
    return merge_overlapping_subs(out)


def punch_filter(out_w: int, out_h: int, dur: float, amount: float = 0.05) -> str:
    """Slow push-in on the cut: grow the frame per-frame (scale eval=frame) and crop the
    stable window out of it, so the shot breathes without the locked-off tripod feel."""
    if dur <= 2.5:
        return ""
    return (
        f"scale=w='iw*(1+{amount}*t/{dur:.2f})':h='ih*(1+{amount}*t/{dur:.2f})':eval=frame,"
        f"crop={out_w}:{out_h},setsar=1"
    )


NVENC_OK: bool | None = None


def nvenc_available() -> bool:
    global NVENC_OK
    if NVENC_OK is None:
        NVENC_OK = bool(ff_available().get("nvenc"))
    return NVENC_OK


def grade_filter(grade: str) -> str:
    """Color treatment applied before subtitles.
    noir: crushed blacks, slightly desaturated, gentle vignette - the moody 'podcast film' look.
    fade: lifted blacks low-contrast matte. natural: untouched."""
    if grade == "noir":
        return "eq=brightness=-0.03:contrast=1.12:saturation=0.55,curves=master='0/0 0.5/0.4 1/0.9',vignette=PI/4.5"
    if grade == "fade":
        return "eq=brightness=0.02:contrast=0.94:saturation=0.72,curves=master='0/0.06 1/0.94'"
    return ""


_ASR_MODEL: dict[str, Any] = {}


def _asr_model(size: str):
    if size not in _ASR_MODEL:
        from faster_whisper import WhisperModel

        _ASR_MODEL[size] = WhisperModel(size, device="cpu", compute_type="int8")
    return _ASR_MODEL[size]


def words_are_distinct(words: list[dict]) -> bool:
    """True when the timing data actually moves per word. YouTube's json3 tracks often collapse
    several words onto one timestamp - that reads as a jump, not as sync."""
    if len(words) < 6:
        return False
    ts = [float(w.get("t", 0)) for w in words]
    uniq = len({round(t, 2) for t in ts})
    return uniq / len(ts) > 0.75


def asr_words(src: Path, start: float, end: float, log: list[str] | None = None, model_size: str = "base", language: str | None = None) -> list[dict]:
    """Word-level timestamps from the ACTUAL audio (faster-whisper, CPU int8).
    This is what makes captions track speech instead of riding a metronome."""
    lg = log if log is not None else []
    cache = TEMP / f"asr_{src.stem}_{int(start)}-{int(end)}_{model_size}.json"
    if cache.exists():
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    wav = TEMP / f"asr_{src.stem}_{int(start)}-{int(end)}.wav"
    r = run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{start:.2f}", "-t", f"{end - start:.2f}",
             "-i", str(src), "-ac", "1", "-ar", "16000", "-vn", str(wav)], timeout=300)
    if r.returncode != 0 or not wav.exists():
        lg.append("asr: audio extract failed, captions fall back to line timing")
        return []
    try:
        model = _asr_model(model_size)
        segs, _info = model.transcribe(str(wav), word_timestamps=True, vad_filter=True, language=language, beam_size=1)
        words: list[dict] = []
        for s in segs:
            for w in s.words or []:
                txt = str(w.word).strip()
                if txt:
                    words.append({"t": round(start + float(w.start), 3), "w": txt})
        wav.unlink(missing_ok=True)
        if words:
            cache.write_text(json.dumps(words, ensure_ascii=False), encoding="utf-8")
            lg.append(f"asr: {len(words)} word marks aligned ({model_size})")
            return words
        lg.append("asr: no speech detected")
    except Exception as e:
        lg.append(f"asr failed ({type(e).__name__}: {str(e)[:120]}), falling back to caption timing")
        wav.unlink(missing_ok=True)
    return []


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
    preset = opts.get("preset", "mono-condensed")
    subtitle_v = int(opts.get("subtitle_v", 260))
    title_text = str(opts.get("title") or clip.get("title") or "")
    use_nvenc = bool(opts.get("nvenc", True)) and nvenc_available()
    subs: list[dict] = clip.get("subtitles") or []

    key = clip_token(clip["start"], clip["end"]) + f"_{uuid.uuid4().hex[:6]}"
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
    g = grade_filter(str(opts.get("grade", "noir")))
    if g:
        vf += "," + g

    has_audio = run([FFPROBE, "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=index", "-of", "csv=p=0", str(src)], timeout=60).stdout.strip() != ""
    ranges: list[tuple[float, float]] = [(start, end)]
    if bool(opts.get("tighten", True)) and has_audio:
        try:
            ranges = detect_dead_air(src, start, end, log)
        except Exception as e:
            log.append(f"tighten skipped: {type(e).__name__}: {str(e)[:120]}")
            ranges = [(start, end)]
    total = sum(b - a for a, b in ranges)
    multi = len(ranges) > 1

    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y"]
    for a, b in ranges:
        cmd += ["-ss", f"{a:.2f}", "-t", f"{b - a:.2f}", "-i", str(src)]
    if not has_audio:
        # an explicit silent track keeps every output stitchable and platform-safe
        cmd += ["-f", "lavfi", "-t", f"{total:.2f}", "-i", "anullsrc=r=48000:channel_layouts=stereo"]

    hook = title_text if bool(opts.get("hook_title", True)) else ""
    yt_words = clip.get("words") if isinstance(clip.get("words"), list) else []
    rel_subs: list[dict] = []
    if has_audio and bool(opts.get("asr", True)):
        asr = asr_words(src, start, end, log, language=opts.get("asr_lang") or None)
        if asr:
            rel_subs = words_to_rel_events(asr, ranges)
    if not rel_subs and words_are_distinct(yt_words or []):
        rel_subs = words_to_rel_events(list(yt_words or []), ranges)
    if not rel_subs:
        rel_subs = remap_captions(subs, ranges) if multi else relative_subtitles(subs, start, end - start)
    ass_used = bool(rel_subs or hook)
    ass_arg = ""
    if ass_used:
        ass_arg = "ass=" + _ass_filter_arg(write_ass(video_id, key, rel_subs, preset, out_h, subtitle_v, hook))

    punch = punch_filter(out_w, out_h, total) if bool(opts.get("punch_in", True)) and layout != "pip" else ""
    audio_tail = ("loudnorm=I=-14:TP=-1.5:LRA=11," if bool(opts.get("loudnorm", True)) else "") + "aformat=sample_rates=48000:channel_layouts=stereo"

    if multi:
        graph = []
        for i in range(len(ranges)):
            graph.append(f"[{i}:v]{vf},fps=30,setsar=1,format=yuv420p[v{i}]")
            graph.append(f"[{i}:a]aresample=async=1:first_pts=0,atrim=0:{total:.2f}[a{i}]")
        ins = "".join(f"[v{i}][a{i}]" for i in range(len(ranges)))
        graph.append(f"{ins}concat=n={len(ranges)}:v=1:a=1[cv][ca]")
        chain_parts = ([punch] if punch else []) + ([ass_arg] if ass_arg else [])
        graph.append("[cv]" + ",".join(chain_parts) + "[vout]")
        graph.append("[ca]" + audio_tail + "[aout]")
        cmd += ["-filter_complex", ";".join(graph), "-map", "[vout]", "-map", "[aout]"]
        filters = None
    else:
        filters = vf
        if punch:
            filters += "," + punch
        if ass_arg:
            filters += "," + ass_arg
        cmd += ["-vf", filters, "-af", audio_tail]
        if not has_audio:
            cmd += ["-map", "0:v:0", "-map", "1:a:0", "-shortest"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-r", "30", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart", str(out_path)]
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


def _to_bool(v: Any) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)


def normalize_opts(opts: dict) -> dict:
    opts = dict(opts)
    for k in ("sandbox", "nvenc", "cookies", "face_track", "tighten", "punch_in", "hook_title", "loudnorm", "asr"):
        if k in opts:
            opts[k] = _to_bool(opts[k])
    if "grade" in opts:
        g = str(opts["grade"]).lower()
        opts["grade"] = g if g in ("noir", "fade", "natural") else "noir"
    return opts


def new_job(video_id: str, clips: list[dict], opts: dict, kind: str = "batch") -> str:
    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {
        "id": job_id,
        "kind": kind,
        "video_id": video_id,
        "clips": clips,
        "opts": normalize_opts(opts),
        "status": "queued",
        "items": [{"key": clip_token(c.get("start", 0), c.get("end", 0)), "status": "pending", "file": None, "error": None} for c in clips],
        "created": time.time(),
        "zip": None,
        "final": None,
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
        "kind": j.get("kind", "batch"),
        "status": j["status"],
        "total": len(j["items"]),
        "done": done,
        "items": [{"key": i["key"], "status": i["status"], "file": i["file"]} for i in j["items"]],
        "zip": j["zip"],
        "final": Path(j["final"]).name if j.get("final") and Path(j["final"]).exists() else None,
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


# ---------------------------------------------------------------------------
# final cut: stitch selected clips into ONE finished vertical video


TRANSITIONS = ("fade", "fadeblack", "wipeleft", "circleopen", "slideup", "smoothleft", "none")


XFADE_ALIAS = {
    "fade": "fade",
    "fadeblack": "fadeblack",
    "wipeleft": "wipeleft",
    "circleopen": "circleopen",
    "slideup": "slideup",
    "smoothleft": "smoothleft",
}


def clip_token(start: float, end: float) -> str:
    """Stable, filesystem-safe segment id shared by render + final-cut lookup."""
    return f"{int(start)}-{int(end)}"


def _norm_vf(out_w: int, out_h: int, fps: int = 30) -> str:
    """Force every segment onto one canvas so the transition graph never mismatches."""
    return (
        f"scale={out_w}:{out_h}:force_original_aspect_ratio=decrease,"
        f"pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2:color=black@0,"
        f"fps={fps},format=yuv420p,setsar=1"
    )


def assemble_final(parts: list[Path], out: Path, aspect: str, transition: str, xfade: float, log: list[str]) -> Path | None:
    """One finished vertical video from rendered segments.
    Deterministic pipeline: normalize every segment (canvas, fps, audio layout) with a micro
    fade in/out as cut punctuation, then concat via demuxer. Order = parts order, always."""
    if not parts:
        return None
    out_w, out_h = ASPECTS.get(aspect, ASPECTS["9:16"])
    fade = 0.0 if transition == "none" else max(0.05, min(0.25, xfade))
    norm: list[Path] = []
    for i, part in enumerate(parts):
        d = ffprobe_duration(part)
        if d <= 0.5:
            log.append(f"segment {part.name} unreadable, skipping")
            continue
        n = TEMP / f"fc_{out.stem}_{i}.mp4"
        if len(parts) == 1 and fade == 0.0:
            norm.append(part)
            continue
        vf = _norm_vf(out_w, out_h)
        af = "aformat=sample_rates=48000:channel_layouts=stereo"
        if fade > 0:
            fd = min(fade, d / 4)
            vf += f",fade=t=in:st=0:d={fd:.2f},fade=t=out:st={max(0.0, d - fd):.2f}:d={fd:.2f}"
            af += f",afade=t=in:st=0:d={fd:.2f},afade=t=out:st={max(0.0, d - fd):.2f}:d={fd:.2f}"
        r = run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", str(part),
                 "-vf", vf, "-af", af,
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-r", "30",
                 "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2", str(n)], timeout=1200)
        if r.returncode == 0 and n.exists() and n.stat().st_size > 10000:
            norm.append(n)
        else:
            log.append(f"segment normalize failed {part.name}: {(r.stderr or '')[-160:]}")
    if not norm:
        return None
    log.append("parts: " + ", ".join(x.name for x in norm))
    lst = TEMP / f"concat_{uuid.uuid4().hex[:8]}.txt"
    lst.write_text("".join(f"file '{x.as_posix()}'\n" for x in norm), encoding="utf-8")
    r = run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
             "-c", "copy", "-movflags", "+faststart", str(out)], timeout=900)
    if not (r.returncode == 0 and out.exists() and out.stat().st_size > 10000):
        log.append(f"copy concat failed, re-encoding: {(r.stderr or '')[-160:]}")
        r = run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "160k",
                 "-movflags", "+faststart", str(out)], timeout=1800)
    lst.unlink(missing_ok=True)
    for n in norm:
        if n.name.startswith(f"fc_{out.stem}_"):
            n.unlink(missing_ok=True)
    if r.returncode == 0 and out.exists() and out.stat().st_size > 10000:
        log.append(f"final cut assembled ({len(norm)} parts, fades={'off' if fade == 0 else f'{fade:.2f}s'})")
        return out
    log.append(f"final cut FAILED: {(r.stderr or '')[-300:]}")
    return None


def _job_source(j: dict, cookie_mode: bool) -> Path | None:
    """Shared source resolution for both job kinds: upload > cache > sandbox > download."""
    j["status"] = "running"
    video_id = j["video_id"]
    opts = j["opts"]
    src = upload_source(video_id) or source_path(video_id)
    if src is None and opts.get("sandbox"):
        src = make_sandbox_source(video_id)
    elif src is None and not opts.get("sandbox"):
        j["status"] = "downloading"
        src = download_source(video_id, cookie_mode, j["log"])
        if src is not None:
            j["status"] = "running"
    return src


def _render_items(j: dict, src: Path) -> list[Path]:
    """Render every clip in job order, updating items. Returns the successful outputs."""
    src_dur = ffprobe_duration(src)
    outs: list[Path] = []
    for idx, clip in enumerate(j["clips"]):
        item = j["items"][idx]
        item["status"] = "rendering"
        start, end = float(clip.get("start", 0)), float(clip.get("end", 0))
        if src_dur <= 0 or start >= src_dur or end <= start:
            item["status"] = "failed"
            item["error"] = f"clip range {start:.0f}-{end:.0f}s outside source ({src_dur:.0f}s)"
            j["log"].append(item["error"])
            continue
        clip["end"] = min(end, src_dur)
        try:
            out = render_clip(src, j["video_id"], clip, j["opts"], j["log"])
        except Exception as e:
            out = None
            j["log"].append(f"render exception: {type(e).__name__}: {str(e)[:200]}")
        if out:
            item["status"] = "done"
            item["file"] = str(out)
            outs.append(out)
        else:
            item["status"] = "failed"
            item["error"] = "render failed (see log)"
    return outs


def run_job(job_id: str, cookie_mode: bool) -> None:
    """Blocking worker: download once, render each clip, zip. Called via to_thread.
    Every failure path must reach a terminal job status; no job may hang in running."""
    j = JOBS.get(job_id)
    if not j:
        return
    try:
        src = _job_source(j, cookie_mode)
        if src is None:
            j["status"] = "failed"
            for i in j["items"]:
                i["status"] = "failed"
                i["error"] = "source unavailable (download failed)"
            return
        _render_items(j, src)
        zp = build_zip(job_id)
        j["status"] = "done" if all(i["status"] == "done" for i in j["items"]) else ("partial" if zp else "failed")
    except Exception as e:
        j["status"] = "failed"
        j["log"].append(f"job crash: {type(e).__name__}: {str(e)[:200]}")
        for i in j["items"]:
            if i["status"] in ("pending", "rendering"):
                i["status"] = "failed"
                i["error"] = "job crashed"


def run_final_job(job_id: str, cookie_mode: bool) -> None:
    """One finished vertical video: render every selected clip, then stitch with transitions.
    Clip order: chronological (podcast narrative) unless opts.order == 'value'."""
    j = JOBS.get(job_id)
    if not j:
        return
    try:
        src = _job_source(j, cookie_mode)
        if src is None:
            j["status"] = "failed"
            for i in j["items"]:
                i["status"] = "failed"
                i["error"] = "source unavailable (download failed)"
            return
        if str(j["opts"].get("order", "chronological")) == "chronological":
            order = sorted(range(len(j["clips"])), key=lambda i: float(j["clips"][i].get("start", 0)))
            j["clips"] = [j["clips"][i] for i in order]
            j["items"] = [j["items"][i] for i in order]
        outs = _render_items(j, src)
        if not outs:
            j["status"] = "failed"
            j["log"].append("no segment rendered, nothing to assemble")
            return
        opts = j["opts"]
        out = EXPORTS / f"hookline_final_{job_id}.mp4"
        ok = assemble_final(
            outs,
            out,
            str(opts.get("aspect", "9:16")),
            str(opts.get("transition", "fade")),
            float(opts.get("xfade", 0.4)),
            j["log"],
        )
        if ok:
            j["final"] = str(out)
            j["status"] = "done" if len(outs) == len(j["items"]) else "partial"
        else:
            j["status"] = "failed"
    except Exception as e:
        j["status"] = "failed"
        j["log"].append(f"final job crash: {type(e).__name__}: {str(e)[:200]}")
        for i in j["items"]:
            if i["status"] in ("pending", "rendering"):
                i["status"] = "failed"
                i["error"] = "job crashed"


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
        # keep real sources and uploaded clips (re-render friendly); remove slices/ass/frames
        name = p.name.lower()
        is_source = p.suffix.lower() in (".mp4", ".mkv", ".webm") and re.fullmatch(r"[A-Za-z0-9_-]{11}\.[a-z0-9]+", name)
        is_upload = (p.parent == UPLOADS and name.startswith("up")) or (p.parent == ANALYSIS and name.startswith("up"))
        if is_source or is_upload:
            kept += 1
            continue
        freed += p.stat().st_size
        p.unlink()
        removed += 1
    return {"removed": removed, "kept_sources": kept, "freed_bytes": freed}


def validate_cookies(text: str) -> tuple[list[str], list[str]]:
    """Split Netscape cookie text into (valid_lines, bad_lines).
    MozillaCookieJar (and yt-dlp) refuse the whole file unless the first line is the
    '# Netscape HTTP Cookie File' header, and every data line needs 7 tab columns:
    domain, flag, path, secure, expiry, name, value."""
    valid: list[str] = []
    bad: list[str] = []
    body = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff").strip()
    if not body:
        return [], ["empty file"]
    lines = body.split("\n")
    has_header = lines[0].startswith("# Netscape") or lines[0].startswith("# HTTP Cookie File")
    if not has_header:
        bad.append("missing header: first line must be '# Netscape HTTP Cookie File'")
    for line in lines:
        line = line.rstrip()
        if not line or line.startswith("#"):
            valid.append(line)
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            bad.append(line[:80])
            continue
        domain, flag, _path, secure, expiry, name = parts[0], parts[1], parts[2], parts[3], parts[4], parts[5]
        ok = (
            domain and name
            and flag.upper() in ("TRUE", "FALSE")
            and secure.upper() in ("TRUE", "FALSE")
            and (expiry.lstrip("-").isdigit() or expiry.upper() == "FALSE")
        )
        if ok:
            valid.append(line)
        else:
            bad.append(line[:80])
    return valid, bad


def cookies_are_valid() -> bool:
    if not COOKIES.exists():
        return False
    try:
        _v, bad = validate_cookies(COOKIES.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return False
    return not bad


def save_cookies(text: str) -> dict:
    text = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff").strip()
    if not text:
        return {"saved": False, "error": "No cookie lines found. Export the file with a browser extension and paste the whole thing."}
    if not text.startswith("# Netscape") and not text.startswith("# HTTP Cookie File"):
        text = "# Netscape HTTP Cookie File\n" + text
    valid, bad = validate_cookies(text)
    if bad:
        first = bad[0][:60]
        return {"saved": False, "error": f"{len(bad)} baris bukan format Netscape (butuh header + 7 kolom tab: domain, flag, path, secure, expiry, name, value). Contoh baris rusak: {first!r}", "bad": bad[:5]}
    if not any(l and not l.startswith("#") for l in valid):
        return {"saved": False, "error": "No cookie lines found. Export the file with a browser extension and paste the whole thing."}
    body = "\n".join(valid).strip() + "\n"
    COOKIES.write_text(body, encoding="utf-8")
    domains = sorted(set(re.findall(r"(?m)^([a-z0-9.\-]+\.[a-z]+)\t", body.lower())))
    return {"saved": True, "domains": domains[:12], "lines": len([l for l in valid if l and not l.startswith("#")])}


def cookies_status() -> dict:
    if not COOKIES.exists():
        return {"present": False}
    try:
        text = COOKIES.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {"present": True, "valid": False, "error": "unreadable", "lines": 0, "domains": []}
    _v, bad = validate_cookies(text)
    has_header = text.lstrip("\ufeff").startswith("# Netscape")
    domains = sorted(set(re.findall(r"(?m)^([a-z0-9.\-]+\.[a-z]+)\t", text.lower())))
    n = len([l for l in text.splitlines() if l.strip() and not l.startswith("#")])
    out = {"present": True, "valid": bool(has_header and not bad), "lines": n, "domains": domains[:12]}
    if bad:
        out["error"] = f"{len(bad)} baris rusak"
    elif not has_header:
        out["error"] = "header '# Netscape HTTP Cookie File' hilang - simpan ulang"
    return out


def delete_cookies() -> dict:
    if COOKIES.exists():
        COOKIES.unlink()
    return {"present": False}
