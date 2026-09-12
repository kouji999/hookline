import type { Clip } from '../types'

export function fmt(sec: number): string {
  const s = Math.max(0, Math.round(sec))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const r = s % 60
  const mm = h > 0 ? String(m).padStart(2, '0') : String(m)
  return `${h > 0 ? h + ':' : ''}${mm}:${String(r).padStart(2, '0')}`
}

export function fmtRange(c: Clip): string {
  return `${fmt(c.start)} - ${fmt(c.end)}`
}

export function extractVideoId(url: string): string | null {
  const m = url.match(/(?:v=|\/shorts\/|\/embed\/|youtu\.be\/)([A-Za-z0-9_-]{11})/)
  if (m) return m[1]
  const t = url.trim()
  return /^[A-Za-z0-9_-]{11}$/.test(t) ? t : null
}

export function plainLines(clips: Clip[]): string {
  return clips.map((c) => fmtRange(c)).join('\n')
}

export function titledLines(clips: Clip[]): string {
  return clips.map((c) => `${fmtRange(c)} | ${c.title}`).join('\n')
}

export function chapterLines(clips: Clip[]): string {
  return [...clips].sort((a, b) => a.start - b.start).map((c) => `${fmt(c.start)} ${c.title}`).join('\n')
}

export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    try {
      const ta = document.createElement('textarea')
      ta.value = text
      ta.style.position = 'fixed'
      ta.style.opacity = '0'
      document.body.appendChild(ta)
      ta.select()
      document.execCommand('copy')
      ta.remove()
      return true
    } catch {
      return false
    }
  }
}
