import type { Capabilities, Clip, FinalCutOpts, RenderOpts, RenderProgress, Subtitle } from '../types'

export async function getCapabilities(): Promise<Capabilities> {
  const r = await fetch('/api/capabilities')
  if (!r.ok) throw new Error(`capabilities ${r.status}`)
  return (await r.json()) as Capabilities
}

export function clipSubtitles(c: Clip): Subtitle[] {
  if (c.subtitles && c.subtitles.length > 0) return c.subtitles
  const dur = Math.max(1, c.end - c.start)
  return c.quote?.trim() ? [{ start: c.start, duration: Math.min(4, dur / 2), text: c.quote.trim() }] : []
}

export async function startRenderBatch(videoId: string, clips: Clip[], opts: RenderOpts): Promise<string> {
  const payload = clips.map((c) => ({ ...c, subtitles: clipSubtitles(c) }))
  const r = await fetch('/api/render-batch', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      video_id: videoId,
      clips: payload,
      opts: { ...opts },
    }),
  })
  if (!r.ok) throw new Error(`render-batch ${r.status}`)
  const j = (await r.json()) as { job_id?: string; error?: string }
  if (!j.job_id) throw new Error(j.error || 'render-batch rejected')
  return j.job_id
}

export async function startFinalCut(videoId: string, clips: Clip[], opts: RenderOpts, final: FinalCutOpts): Promise<string> {
  const payload = clips.map((c) => ({ ...c, subtitles: clipSubtitles(c) }))
  const r = await fetch('/api/final-cut', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ video_id: videoId, clips: payload, opts: { ...opts, ...final } }),
  })
  if (!r.ok) throw new Error(`final-cut ${r.status}`)
  const j = (await r.json()) as { job_id?: string; error?: string }
  if (!j.job_id) throw new Error(j.error || 'final-cut rejected')
  return j.job_id
}

export async function pollRender(jobId: string): Promise<RenderProgress | null> {
  const r = await fetch(`/api/render-progress/${jobId}`)
  if (!r.ok) return null
  return (await r.json()) as RenderProgress
}

export function renderedFileUrl(name: string): string {
  const base = name.split(/[\\/]/).pop() || name
  return `/api/download-rendered/${encodeURIComponent(base)}`
}

export function zipUrl(jobId: string): string {
  return `/api/download-batch-zip/${jobId}`
}

export async function startRawDownload(videoId: string, cookies: boolean): Promise<string> {
  const r = await fetch('/api/download-raw-video', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ video_id: videoId, cookies }),
  })
  if (!r.ok) throw new Error(`raw ${r.status}`)
  return ((await r.json()) as { dl_id: string }).dl_id
}

export async function pollRaw(dlId: string): Promise<{ status: string; file?: string | null } | null> {
  const r = await fetch(`/api/download-raw-status/${dlId}`)
  if (!r.ok) return null
  return (await r.json()) as { status: string; file?: string | null }
}

export async function clipFrameUrl(videoId: string, t: number): Promise<string | null> {
  const r = await fetch(`/api/clip-frame?video_id=${encodeURIComponent(videoId)}&t=${t}`)
  if (!r.ok) return null
  const j = (await r.json()) as { available: boolean; path?: string }
  return j.available && j.path ? j.path : null
}

export interface CookiesState {
  present: boolean
  valid?: boolean
  domains?: string[]
  lines?: number
  error?: string
}

export async function cookiesGet(): Promise<CookiesState> {
  const r = await fetch('/api/cookies')
  if (!r.ok) return { present: false }
  return (await r.json()) as CookiesState
}

export async function cookiesSave(text: string): Promise<{ saved: boolean; domains: string[]; lines: number }> {
  const r = await fetch('/api/cookies', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text }),
  })
  const j = (await r.json().catch(() => ({}))) as { detail?: string; domains?: string[]; lines?: number }
  if (!r.ok) throw new Error(j.detail || `cookies rejected (${r.status})`)
  return { saved: true, domains: j.domains ?? [], lines: j.lines ?? 0 }
}

export async function cookiesDelete(): Promise<void> {
  await fetch('/api/cookies', { method: 'DELETE' })
}

export async function tempInfo(): Promise<{ files: number; bytes: number }> {
  const r = await fetch('/api/temp-storage-info')
  if (!r.ok) return { files: 0, bytes: 0 }
  return (await r.json()) as { files: number; bytes: number }
}

export async function clearTemp(): Promise<{ removed: number; freed_bytes: number }> {
  const r = await fetch('/api/clear-temp', { method: 'POST' })
  if (!r.ok) return { removed: 0, freed_bytes: 0 }
  return (await r.json()) as { removed: number; freed_bytes: number }
}
