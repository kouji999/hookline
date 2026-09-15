# HOOKLINE

> **Viral moment intelligence for YouTube creators.** Find the moments viewers re-watch most, understand why with Gemini AI, and render ready-to-post vertical clips — in one console. Built by **Raliq Hidayat BM3**.

[![React](https://img.shields.io/badge/React-19-blue.svg)](https://react.dev/)
[![FastAPI](https://img.shields.io/badge/FastAPI-active-green.svg)](https://fastapi.tiangolo.com/)
[![FFmpeg](https://img.shields.io/badge/FFmpeg-render_engine-007800.svg)](https://ffmpeg.org/)
[![Google Gemini](https://img.shields.io/badge/Google_Gemini-flash_chain-orange.svg)](https://aistudio.google.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

## Why it's different

Most AI clippers read words. Hookline reads **behavior**:

```
YouTube URL
  ├─ SIGNAL 1: attention curve
  │    ├─ real most-replayed markers probed from the YouTube player (innertube)
  │    └─ honest fallback: AI attention estimate, clearly badged
  ├─ SIGNAL 2: transcript (youtube-transcript-api / manual SRT / plain text)
  ↓
  Gemini analysis with dynamic model discovery (newest Flash your key can use)
  ↓
  Live SSE pipeline: resolve → transcript → signal → analyze → done
  ↓
  Console: interactive retention heatmap, ranked moments, preview player,
  three timestamp formats, local history
  ↓
  Clip Studio: 9:16 / 1:1 / 4:3 / 16:9 framing, karaoke ASS captions (7 presets),
  OpenCV face tracking, NVENC batch rendering, one-click ZIP, cookies manager
```

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | React 19, TypeScript, Vite, Canvas API, vanilla CSS design tokens |
| Backend | Python 3.10+, FastAPI, Uvicorn, SSE streaming |
| Signal | innertube probe (best-effort), AI estimate fallback |
| Transcript | youtube-transcript-api + SRT/plain-text parser |
| AI | google-genai SDK, dynamic model discovery + fallback chain |
| Render | FFmpeg (NVENC with x264 fallback), libass karaoke, OpenCV, yt-dlp |
| Storage | localStorage (history, key, preferences) — nothing leaves your machine |

## Quick Start

### Prerequisites
Git · Node.js v18+ · Python v3.10+ · FFmpeg (drop binaries into `tools/ffmpeg/bin/`, or install to PATH, or set `HOOKLINE_FFMPEG`)

### Install & run

```bash
git clone https://github.com/kouji999/hookline.git && cd hookline
npm install
python -m pip install -r backend/requirements.txt
```

Create `backend/.env` with your server key (never committed, git-ignored):

```
GEMINI_API_KEY=*** key from aistudio.google.com>
```

```bash
npm run dev
```

Frontend `http://localhost:5173` · Backend `http://localhost:8000` · Swagger `http://localhost:8000/docs`

### Workflow

1. **Analyze** — paste a YouTube URL (or switch to **Upload clip** and send your own video file), optionally tell the AI what to look for, hit *Find viral moments*. The key lives server-side; the UI never asks for one. Toggle **Sandbox** for a full demo run with zero API calls.
2. **Review** — click the heatmap to seek, scan ranked moments (substance score, kind, caption count), copy timestamps as plain ranges, titled notes, or ready-made YouTube chapters.
3. **Studio** — open any moment in the Clip Studio: pick framing/caption preset, batch render individual clips + ZIP, or press *Assemble finished video* to get **one ready-to-post vertical edit** of the selected moments (chronological or best-first).

### The finished edit pipeline

Each selected moment is rendered with:

- **Word-level karaoke subtitles** — real per-word timings pulled from the source caption track (json3), highlight follows speech
- **Dead-air trim** — silencedetect cuts pauses so the clip never idles; captions re-timed onto the tightened track
- **Punch-in push** — slow per-frame zoom on the cut, no tripod stiffness
- **Hook title** — the moment's takeaway burned top-center for the first 3 s
- **Loudness normalize** — ffmpeg `loudnorm` to ≈ -14 LUFS / -1.5 dBTP, plus a silent track when the source has none so every cut stays stitchable
- **Fade punctuation + concat** — segments normalized to one canvas/fps/audio layout, deterministic order
4. **Revisit** — everything is stored locally: search, reload past analyses without burning quota.

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/health` · `GET /api/capabilities` | status, render-engine detection |
| `GET /api/video-title?video_id=` | oEmbed metadata |
| `POST /api/analyze` | SSE analysis pipeline (YouTube URL or `upload_id`) |
| `POST /api/upload` · `GET /api/uploads` · `DELETE /api/uploads/{id}` | local clip upload (multipart, 2 GB cap) |
| `GET /api/source-video/{video_id}` | seekable preview of the local source (upload or download) |
| `POST /api/render-batch` · `POST /api/final-cut` · `GET /api/render-progress/{id}` | batch render + one finished edit, live queue |
| `GET /api/download-batch-zip/{id}` · `GET /api/download-rendered/{file}` | exports |
| `GET /api/clip-frame?video_id=&t=` | real frame for framing preview |
| `GET/POST/DELETE /api/cookies` | YouTube cookies manager |
| `POST /api/download-raw-video` | raw source downloader |
| `GET /api/temp-storage-info` · `POST /api/clear-temp` | cache maintenance |

## Commands

| Command | Purpose |
|---|---|
| `npm run dev` | Vite + FastAPI concurrently |
| `npm run build` / `npm run preview` | production build / preview |
| `npm test` | full suite (vitest + pytest) |
| `npm run test:frontend` | 7 unit tests (time/format lib) |
| `npm run test:backend` | 29 unit tests (parsing, ASS, scoring, cookies) |

## Steering the AI (the prompt field)

*"What should the AI look for?"* is a selection directive, not a vibe. A prompt that works:

```
audiens pemula yang mau belajar investasi. ambil HANYA momen dengan satu ide utuh:
angka konkret, framework, atau langkah yang bisa langsung dipakai.
utamakannya: definisi + contoh nyata, kesalahan umum + cara menghindarinya, data + interpretasi.
buang: salam, basa-basi, promosi, opini tanpa isi.
judul clip maksimal 6 kata, pakai bahasa indonesia.
```

Four parts: **audience → hard requirement → preferred shapes → rejects** (+ optional title/language rule). The model must return a verbatim quote from the transcript and timestamps inside the window it was given; anything it invents is dropped by the substance filter (`source_confidence`), so steering with concrete "must contain" rules beats adjectives like "kayu" or "viral".

Clip length is set separately (15/30/60 s). For long podcasts the analyzer walks the video in ~12-minute windows, so timestamps stay honest even on hour-plus sources.

## Troubleshooting

- **FFmpeg chip red in Studio** → binaries missing; see Prerequisites, then restart backend.
- **No transcript** → captions disabled on that video; paste SRT/text manually in the options panel.
- **429 quota** → the fallback chain rotates Flash models automatically; or create a second free key.
- **YouTube bot check on download** → paste Netscape cookies in Studio → Cookies.
- **Port in use** → close stale `node`/`python` processes or change ports in `vite.config.ts` / uvicorn command.

## Roadmap

- [x] v1 — attention-signal analyzer: heatmap, ranked moments, timestamps, history
- [x] v2 — Clip Studio: vertical rendering, karaoke captions, face tracking, batch + ZIP
- [x] v2.1 — dynamic Gemini model discovery (verified live on gemini-3.8-flash)
- [ ] v3 — moving face-track timeline, Whisper fallback, Premiere/Resolve export presets

## License

MIT © 2026 **Raliq Hidayat BM3**

**Author:** Raliq Hidayat BM3
