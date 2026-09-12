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
npm run dev
```

Frontend `http://localhost:5173` · Backend `http://localhost:8000` · Swagger `http://localhost:8000/docs`

### Workflow

1. **Analyze** — paste a YouTube URL, add your free [Google AI Studio](https://aistudio.google.com/) key (or `mock` for sandbox), pick clip length, run.
2. **Review** — click the heatmap to seek, scan ranked moments, copy timestamps as plain ranges, titled notes, or ready-made YouTube chapters.
3. **Render** — open any moment in the Clip Studio, choose framing/caption preset, batch render, download the ZIP.
4. **Revisit** — everything is stored locally: search, reload past analyses without burning quota.

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/health` · `GET /api/capabilities` | status, render-engine detection |
| `GET /api/video-title?video_id=` | oEmbed metadata |
| `POST /api/analyze` | SSE analysis pipeline |
| `POST /api/render-batch` · `GET /api/render-progress/{id}` | batch render + live queue |
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
