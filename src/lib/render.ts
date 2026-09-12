import type { Capabilities, Clip, RenderOpts, RenderProgress } from '../types'

export async function getCapabilities(): Promise<Capabilities> {
  const r = await fetch('/api/capabilities')
  if (!r.ok) throw new Error(`capabilities ${r.status}`)
  return (await r.json()) as Capabilities
}

export async function startRenderBatch(videoId: string, clips: Clip[], opts: RenderOpts): Promise<string> {
  const r = await fetch('/api/render-batch', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      video_id: videoId,
      clips: clips.map((c) => ({ ...c, subtitles: [] })),
      opts: { ...opts },
    }),
  })
  if (!r.ok) throw new Error(`render-batch ${r.status}`)
  const j = (await r.json()) as { job_id: string }
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

export async function cookiesGet(): Promise<{ present: boolean; domains?: string[]; lines?: number }> {
  const r = await fetch('/api/cookies')
  if (!r.ok) return { present: false }
  return (await r.json()) as { present: boolean; domains?: string[]; lines?: number }
}

export async function cookiesSave(text: string): Promise<{ domains: string[]; lines: number }> {
  const r = await fetch('/api/cookies', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text }),
  })
  return (await r.json()) as { domains: string[]; lines: number }
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
