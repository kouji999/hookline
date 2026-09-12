# REWATCH

> **AI Attention Signal Console + 9:16 Clip Studio** — temukan momen paling banyak di-rewatch di video YouTube mana pun, lalu render jadi viral clips vertikal. Dibangun oleh **Raliq Hidayat BM3**.

[![React](https://img.shields.io/badge/React-19-blue.svg)](https://react.dev/)
[![FastAPI](https://img.shields.io/badge/FastAPI-active-green.svg)](https://fastapi.tiangolo.com/)
[![FFmpeg](https://img.shields.io/badge/FFmpeg-render_engine-007800.svg)](https://ffmpeg.org/)
[![Google Gemini](https://img.shields.io/badge/Google_Gemini-flash_chain-orange.svg)](https://aistudio.google.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

## Apa bedanya dengan AI clipper lain

Kebanyakan tool cuma membaca kata-kata (transcript). Rewatch pakai **dua sinyal sekaligus**, lalu bisa langsung **memproduksi video**:

```
URL YouTube
  ├─ SIGNAL 1: attention curve
  │    ├─ probe most-replayed markers asli dari player YouTube (innertube)
  │    └─ fallback: AI attention estimate dari densitas transcript (ber-badge, jujur)
  ├─ SIGNAL 2: transcript (youtube-transcript-api / manual SRT / teks)
  ↓
  Gemini dynamic model discovery (list key-specific models, newest Flash first; fallback chain 3.6 → 2.5 → 2.0 → 1.5) — prompt berbobot sinyal
  ↓
  SSE stream: resolve → transcript → signal → analyze → done
  ↓
  v1 Dashboard: heatmap canvas + ranked clips + preview player + copy 3 format + history
  ↓
  v2 Clip Studio: slice 9:16/1:1/4:3 + karaoke ASS captions + face tracking
     + NVENC GPU render + batch queue + ZIP export + cookies manager + raw downloader
```

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | React 19, TypeScript, Vite, Canvas API, vanilla CSS design tokens |
| Backend | Python 3.10+, FastAPI, Uvicorn, SSE streaming |
| Signal | innertube probe (best-effort), AI estimate fallback |
| Transcript | youtube-transcript-api + parser SRT/teks manual |
| AI | google-genai SDK, dynamic model discovery + fallback chain |
| Render | FFmpeg (x264 + NVENC auto-fallback), libass karaoke captions, OpenCV face tracking, yt-dlp |
| Storage | localStorage (history, key, preferensi studio) |

## Quick Start

### Prerequisites
- Git, Node.js v18+, Python v3.10+
- FFmpeg: taruh di `tools/ffmpeg/bin/` (auto-detect), atau system PATH, atau set `REWATCH_FFMPEG`
- Opsional: GPU NVIDIA untuk NVENC (auto-fallback ke CPU), OpenCV untuk face tracking

### Install

```bash
git clone <repo-url> && cd rewatch
npm install
python -m pip install -r backend/requirements.txt
```

### Run

```bash
npm run dev
```

Frontend `http://localhost:5173` · Backend `http://localhost:8000` · Swagger `http://localhost:8000/docs`

## Cara Pakai

### 1. Analisis
1. Paste URL YouTube (watch / youtu.be / shorts / embed / 11-char ID).
2. Isi **Gemini API key** gratis dari [Google AI Studio](https://aistudio.google.com/) — tanpa kartu kredit. Atau ketik `mock` untuk **sandbox mode** tanpa key.
3. Pilih durasi klip (15/30/60s), jumlah klip, opsional focus prompt ("cari momen lucu", "tips trading").
4. Klik **Analyze signals** → progress per-tahap live.
5. Hasil: heatmap interaktif (klik = seek), clip cards terurut skor atensi, copy timestamp 3 format (polos / +judul / YouTube chapters), history lokal persist.

### 2. Clip Studio (render)
1. Dari hasil analisis, klik **Studio** di clip card mana pun.
2. Atur: aspect (9:16/1:1/4:3/16:9 + backdrop), layout (fullscreen/split/pip), face tracking, preset caption karaoke (7 gaya viral), posisi caption, NVENC.
3. Pilih klip (checkbox) → **Render selection** atau **Render all** → monitor queue real-time → **Download ZIP**.
4. Tools ekstra: cookies manager (anti-bot YouTube), raw video downloader, temp cache cleaner.

### Sandbox render
Tanpa download YouTube: render pakai sumber sintetis lokal (testsrc2). Ketik `mock` di API key, buka Studio, render — pipeline penuh tetap jalan (crop + karaoke + encode).

## API

| Endpoint | Fungsi |
|---|---|
| `GET /api/health` | status + versi |
| `GET /api/capabilities` | deteksi ffmpeg/nvenc/opencv + daftar preset |
| `GET /api/video-title?video_id=` | oEmbed title |
| `POST /api/analyze` | SSE pipeline analisis |
| `POST /api/render-batch` | mulai render job (batch) |
| `GET /api/render-progress/{id}` | poll progress job |
| `GET /api/download-batch-zip/{id}` | unduh ZIP hasil render |
| `GET /api/clip-frame?video_id=&t=` | frame JPEG untuk preview framing |
| `POST/GET/DELETE /api/cookies` | cookies manager |
| `POST /api/download-raw-video` | unduh video mentah |
| `GET /api/temp-storage-info` + `POST /api/clear-temp` | cache management |

## Commands

| Command | Fungsi |
|---|---|
| `npm run dev` | Vite + FastAPI concurrently |
| `npm run build` | tsc + vite build produksi |
| `npm run preview` | preview build |
| `npm run test` | semua test (vitest + pytest) |
| `npm run test:frontend` | unit test frontend (vitest, 7 test) |
| `npm run test:backend` | unit test backend (pytest, 29 test) |

## Testing

- **Backend (`backend/tests/test_units.py`)**: 29 unit test pytest — video id parsing, SRT/plain transcript parsing, sanitize caption, signal estimates, Gemini prompt + JSON parsing (fenced/unfenced/invalid), clip scoring, mock data, aspect/preset tables, crop filter math, ASS generation (BOM guard, brace escaping, relative path), cookies lifecycle.
- **Frontend (`src/lib/__tests__/time.test.ts`)**: 7 unit test vitest — URL parsing, format waktu (m:ss → h:mm:ss), 3 format copy (plain/titled/chapters, sort correctness).
- **E2E (Playwright, script pribadi)**: mock flow penuh, studio render → ZIP, error path (invalid URL, invalid key), mobile 375px, zero console error.

## Troubleshooting

- **FFmpeg not found** → chip merah di Studio. Taruh binary di `tools/ffmpeg/bin/` atau install ke PATH, restart backend.
- **transcript unavailable** → subtitle video mati: paste transcript manual (SRT/teks) di panel opsi.
- **429 quota** → fallback chain otomatis coba semua model Flash; ganti key dari akun Google lain.
- **render failed (ass)** → pastikan font Arial tersedia (bawaan Windows). Di Linux install `fonts-liberation`.
- **YouTube bot check saat download** → paste cookies Netscape di Studio → Cookies.
- **port bentrok** → 5173/8000 dipakai proses lama: tutup via Task Manager, atau ubah port di `vite.config.ts` / perintah uvicorn.

## Roadmap

- [x] v1: analyzer — signal + AI + heatmap + copy 3 format + history
- [x] v2: Clip Studio — 9:16 render, karaoke ASS, face tracking, NVENC, batch + ZIP, cookies, raw downloader
- [ ] v3: Whisper fallback, moving face-track crop (sendcmd timeline), proxy field, export preset edit Premiere/Resolve

## License

MIT © 2026 **Raliq Hidayat BM3**

**Author:** Raliq Hidayat BM3
