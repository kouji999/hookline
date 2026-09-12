import type { AnalysisResult, HistoryEntry, RenderOpts } from '../types'

const K_HISTORY = 'hookline.history.v1'
const K_LANG = 'hookline.lang'
const K_CFG = 'hookline.cfg.v1'
const R_PREFIX = 'hookline.result.'

export function loadHistory(): HistoryEntry[] {
  try {
    return JSON.parse(localStorage.getItem(K_HISTORY) || '[]') as HistoryEntry[]
  } catch {
    return []
  }
}

export function saveHistory(list: HistoryEntry[]): void {
  try {
    localStorage.setItem(K_HISTORY, JSON.stringify(list.slice(0, 60)))
  } catch {
    /* quota */
  }
}

export function saveResult(r: AnalysisResult): void {
  try {
    localStorage.setItem(R_PREFIX + r.video_id, JSON.stringify(r))
  } catch {
    /* drop silently, history entry still valid */
  }
}

export function loadResult(videoId: string): AnalysisResult | null {
  try {
    const raw = localStorage.getItem(R_PREFIX + videoId)
    return raw ? (JSON.parse(raw) as AnalysisResult) : null
  } catch {
    return null
  }
}

export function dropResult(videoId: string): void {
  try {
    localStorage.removeItem(R_PREFIX + videoId)
  } catch {
    /* noop */
  }
}

export function loadLang(): 'en' | 'id' {
  return localStorage.getItem(K_LANG) === 'id' ? 'id' : 'en'
}

export function saveLang(l: 'en' | 'id'): void {
  localStorage.setItem(K_LANG, l)
}

export function loadCfg(): { duration: string; clipCount: number; showPlayer: boolean } {
  try {
    const c = JSON.parse(localStorage.getItem(K_CFG) || '{}') as Partial<{ duration: string; clipCount: number; showPlayer: boolean }>
    return { duration: c.duration ?? '30s', clipCount: c.clipCount ?? 8, showPlayer: c.showPlayer ?? true }
  } catch {
    return { duration: '30s', clipCount: 8, showPlayer: true }
  }
}

export function saveCfg(c: { duration: string; clipCount: number; showPlayer: boolean }): void {
  try {
    localStorage.setItem(K_CFG, JSON.stringify(c))
  } catch {
    /* noop */
  }
}

const K_STUDIO = 'hookline.studio.v1'

export function loadStudio(sandboxDefault: boolean): RenderOpts {
  const fallback: RenderOpts = {
    aspect: '9:16',
    layout: 'fullscreen',
    backdrop: 'blur',
    face_track: false,
    preset: 'viral-pop',
    subtitle_v: 260,
    title: '',
    nvenc: true,
    cookies: false,
    sandbox: sandboxDefault,
  }
  try {
    const raw = localStorage.getItem(K_STUDIO)
    if (raw) {
      const p = JSON.parse(raw) as Partial<RenderOpts>
      return { ...fallback, ...p }
    }
  } catch {
    /* fallthrough */
  }
  return fallback
}

export function saveStudio(p: RenderOpts): void {
  try {
    localStorage.setItem(K_STUDIO, JSON.stringify(p))
  } catch {
    /* noop */
  }
}
