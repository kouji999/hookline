import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { Capabilities, Clip, RenderOpts, RenderProgress } from '../types'
import { useI18n } from '../i18n'
import { fmt } from '../lib/time'
import { loadStudio, saveStudio } from '../lib/storage'
import {
  clearTemp, clipFrameUrl, cookiesDelete, cookiesGet, cookiesSave,
  pollRaw, pollRender, renderedFileUrl, startRawDownload, startRenderBatch, tempInfo, zipUrl,
} from '../lib/render'
import { IconClose, IconSpinner } from './Icons'

const PRESET_LABELS: Record<string, string> = {
  'viral-pop': 'Viral Pop',
  'beast-punch': 'Beast Punch',
  'cyber-violet': 'Cyber Violet',
  'fire-red': 'Fire Red',
  'electric-cyan': 'Electric Cyan',
  'golden-aura': 'Golden Aura',
  'clean-minimal': 'Clean Minimal',
}

export function ClipStudioModal({
  videoId,
  clips,
  initialIndex,
  mock,
  caps,
  onClose,
}: {
  videoId: string
  clips: Clip[]
  initialIndex: number
  mock: boolean
  caps: Capabilities | null
  onClose: () => void
}) {
  const { tr } = useI18n()
  const [opts, setOpts] = useState<RenderOpts>(() => loadStudio(mock))
  const [sel, setSel] = useState<Set<number>>(() => new Set([initialIndex]))
  const [job, setJob] = useState<RenderProgress | null>(null)
  const [frame, setFrame] = useState<string | null>(null)
  const [cookiesText, setCookiesText] = useState('')
  const [cookiesInfo, setCookiesInfo] = useState<{ present: boolean; domains?: string[]; lines?: number } | null>(null)
  const [rawState, setRawState] = useState<string | null>(null)
  const [temp, setTemp] = useState<{ files: number; bytes: number } | null>(null)
  const jobIdRef = useRef<string | null>(null)

  const patch = (p: Partial<RenderOpts>) => {
    setOpts((o) => {
      const n = { ...o, ...p }
      saveStudio(n)
      return n
    })
  }

  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [onClose])

  useEffect(() => {
    let stop = false
    void (async () => {
      const f = await clipFrameUrl(videoId, clips[initialIndex]?.start ?? 0).catch(() => null)
      if (!stop) setFrame(f)
      setCookiesInfo(await cookiesGet().catch(() => null))
      setTemp(await tempInfo().catch(() => null))
      const saved = localStorage.getItem(`rewatch.job.${videoId}`)
      if (saved && !stop) {
        jobIdRef.current = saved
        const p = await pollRender(saved).catch(() => null)
        if (p && !stop) setJob(p)
      }
    })()
    return () => {
      stop = true
    }
  }, [videoId, initialIndex, clips])

  useEffect(() => {
    if (!job || (job.status !== 'queued' && job.status !== 'downloading' && job.status !== 'running')) return
    const t = window.setInterval(async () => {
      const id = jobIdRef.current
      if (!id) return
      const p = await pollRender(id).catch(() => null)
      if (!p) return
      setJob(p)
      if (p.status === 'done' || p.status === 'partial' || p.status === 'failed') {
        localStorage.removeItem(`rewatch.job.${videoId}`)
        setFrame(await clipFrameUrl(videoId, clips[initialIndex]?.start ?? 0).catch(() => null))
        setTemp(await tempInfo().catch(() => null))
      }
    }, 1500)
    return () => window.clearInterval(t)
  }, [job, videoId, clips, initialIndex])

  const startRender = useCallback(
    async (all: boolean) => {
      const chosen = all ? clips : clips.filter((_, i) => sel.has(i))
      if (chosen.length === 0) return
      const id = await startRenderBatch(videoId, chosen, opts)
      jobIdRef.current = id
      localStorage.setItem(`rewatch.job.${videoId}`, id)
      setJob({ id, status: 'queued', total: chosen.length, done: 0, items: chosen.map((c) => ({ key: `${c.start}-${c.end}`, status: 'pending' as const, file: null })), zip: null, log: [] })
    },
    [clips, sel, videoId, opts]
  )

  const startRaw = useCallback(async () => {
    setRawState('queued')
    const id = await startRawDownload(videoId, opts.cookies)
    const t = window.setInterval(async () => {
      const s = await pollRaw(id)
      if (!s) return
      setRawState(s.status)
      if (s.status === 'done' || s.status === 'failed') {
        window.clearInterval(t)
        setFrame(await clipFrameUrl(videoId, clips[initialIndex]?.start ?? 0).catch(() => null))
      }
    }, 2000)
  }, [videoId, opts.cookies, clips, initialIndex])

  const saveCookies = useCallback(async () => {
    if (!cookiesText.trim()) return
    await cookiesSave(cookiesText)
    setCookiesText('')
    setCookiesInfo(await cookiesGet())
  }, [cookiesText])

  const fmtBytes = (b: number) => (b > 1048576 ? `${(b / 1048576).toFixed(1)} MB` : b > 1024 ? `${(b / 1024).toFixed(0)} KB` : `${b} B`)

  const jobBusy = job && (job.status === 'queued' || job.status === 'downloading' || job.status === 'running')
  const renderDisabled = !caps?.ffmpeg

  const aspectRatio = useMemo(() => {
    switch (opts.aspect) {
      case '9:16':
        return '9 / 16'
      case '1:1':
        return '1 / 1'
      case '4:3':
        return '4 / 3'
      default:
        return '16 / 9'
    }
  }, [opts.aspect])

  return (
    <div className="modal-backdrop" onClick={onClose} role="dialog" aria-modal="true" aria-label={tr('studio.title')}>
      <div className="modal studio-modal" onClick={(e) => e.stopPropagation()}>
        <header className="modal-head">
          <span className="modal-title">{tr('studio.title')}</span>
          <span className={`cap-chip ${caps?.ffmpeg ? 'ok' : 'bad'}`} title={caps?.ffmpeg_version || ''}>
            {caps?.ffmpeg ? tr('studio.cap.ok') : tr('studio.cap.fail')}
          </span>
          <button className="icon-btn" onClick={onClose} aria-label="close">
            <IconClose />
          </button>
        </header>

        <div className="studio-grid">
          <section className="studio-controls">
            <div className="ctl">
              <label className="field-label">{tr('studio.aspect')}</label>
              <div className="seg-group tight">
                {(caps?.aspects ?? ['9:16', '1:1', '4:3', '16:9']).map((a) => (
                  <button key={a} type="button" className={opts.aspect === a ? 'on' : ''} onClick={() => patch({ aspect: a as RenderOpts['aspect'] })}>
                    <span className="mono">{a}</span>
                  </button>
                ))}
              </div>
            </div>
            <div className="ctl">
              <label className="field-label">{tr('studio.layout')}</label>
              <div className="seg-group tight">
                {['fullscreen', 'split', 'pip'].map((l) => (
                  <button key={l} type="button" className={opts.layout === l ? 'on' : ''} onClick={() => patch({ layout: l as RenderOpts['layout'] })}>
                    {l}
                  </button>
                ))}
              </div>
            </div>
            {opts.aspect === '16:9' && (
              <div className="ctl">
                <label className="field-label">{tr('studio.backdrop')}</label>
                <div className="seg-group tight">
                  {['blur', 'black'].map((b) => (
                    <button key={b} type="button" className={opts.backdrop === b ? 'on' : ''} onClick={() => patch({ backdrop: b as RenderOpts['backdrop'] })}>
                      {b}
                    </button>
                  ))}
                </div>
              </div>
            )}
            <div className="ctl toggles">
              <label className="check">
                <input type="checkbox" checked={opts.face_track} onChange={(e) => patch({ face_track: e.target.checked })} disabled={!caps?.cv2} />
                {tr('studio.face')} {caps?.cv2 ? '' : '(no opencv)'}
              </label>
              <label className="check">
                <input type="checkbox" checked={opts.nvenc} onChange={(e) => patch({ nvenc: e.target.checked })} disabled={!caps?.nvenc} />
                {tr('studio.nvenc')} {caps?.nvenc ? '' : '(n/a)'}
              </label>
              <label className="check">
                <input type="checkbox" checked={opts.cookies} onChange={(e) => patch({ cookies: e.target.checked })} />
                {tr('studio.cookies')}
              </label>
            </div>
            <div className="ctl">
              <label className="field-label">{tr('studio.preset')}</label>
              <select className="select" value={opts.preset} onChange={(e) => patch({ preset: e.target.value })}>
                {(caps?.presets ?? Object.keys(PRESET_LABELS)).map((p) => (
                  <option key={p} value={p}>
                    {PRESET_LABELS[p] ?? p}
                  </option>
                ))}
              </select>
            </div>
            <div className="ctl">
              <label className="field-label">
                {tr('studio.subtitleV')} <span className="mono">{opts.subtitle_v}</span>
              </label>
              <input type="range" min={80} max={900} step={10} value={opts.subtitle_v} onChange={(e) => patch({ subtitle_v: Number(e.target.value) })} />
            </div>
            <div className="ctl actions">
              <button className="btn primary" onClick={() => void startRender(false)} disabled={renderDisabled || !!jobBusy || sel.size === 0}>
                {tr('studio.render')} <span className="mono">({tr('studio.selected', { n: sel.size })})</span>
              </button>
              <button className="btn ghost" onClick={() => void startRender(true)} disabled={renderDisabled || !!jobBusy}>
                {tr('studio.renderAll')} <span className="mono">({clips.length})</span>
              </button>
              <button className="btn ghost" onClick={() => void startRaw()} disabled={rawState === 'queued' || rawState === 'running'}>
                {tr('raw.start')} {rawState ? <span className="mono">[{rawState}]</span> : null}
              </button>
            </div>
          </section>

          <section className="studio-preview">
            <div className="phone" style={{ aspectRatio }}>
              {frame ? <img src={frame} alt="frame preview" /> : <div className="phone-empty mono">{tr('studio.aspect')} preview</div>}
            </div>
          </section>

          <section className="studio-queue">
            <h4 className="section-title">{tr('studio.queue')}</h4>
            {job ? (
              <ul className="queue-list">
                {job.items.map((it) => (
                  <li key={it.key} className={`q-${it.status}`}>
                    <span className="mono q-range">{it.key.replace('-', ' → ')}s</span>
                    <span className={`q-status s-${it.status}`}>
                      {it.status === 'rendering' ? <IconSpinner /> : null} {tr(`render.${it.status}`)}
                    </span>
                    {it.file ? (
                      <a className="mono q-dl" href={renderedFileUrl(it.file)} download>
                        .mp4
                      </a>
                    ) : null}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="hist-empty">{tr('hist.empty')}</p>
            )}
            {job && (job.status === 'done' || job.status === 'partial') && job.zip ? (
              <a className="btn primary zip-btn" href={zipUrl(job.id)} download>
                {tr('studio.zip')}
              </a>
            ) : null}
            {jobBusy ? (
              <div className="queue-progress">
                <div className="qp-bar" style={{ width: `${Math.round((job!.done / Math.max(1, job!.total)) * 100)}%` }} />
              </div>
            ) : null}
            <ul className="clip-pick">
              {clips.slice(0, 12).map((c, i) => (
                <li key={`${c.start}-${i}`}>
                  <label>
                    <input type="checkbox" checked={sel.has(i)} onChange={() => setSel((s) => {
                      const n = new Set(s)
                      if (n.has(i)) n.delete(i)
                      else n.add(i)
                      return n
                    })} />
                    <span className="mono">{fmt(c.start)}-{fmt(c.end)}</span> <span className="pick-title">{c.title}</span>
                  </label>
                </li>
              ))}
            </ul>
          </section>

          <section className="studio-tools">
            <h4 className="section-title">Cookies</h4>
            <p className="tools-hint">{tr('cookies.desc')}</p>
            <p className="mono cookies-state">{cookiesInfo?.present ? tr('cookies.present', { n: cookiesInfo.lines ?? 0, d: (cookiesInfo.domains ?? []).slice(0, 3).join(', ') }) : tr('cookies.none')}</p>
            <textarea className="transcript-box" rows={4} placeholder="# Netscape HTTP Cookie File" value={cookiesText} onChange={(e) => setCookiesText(e.target.value)} />
            <div className="ctl actions inline">
              <button className="btn small" onClick={() => void saveCookies()} disabled={!cookiesText.trim()}>
                {tr('cookies.save')}
              </button>
              <button
                className="btn small ghost"
                onClick={async () => {
                  await cookiesDelete()
                  setCookiesInfo(await cookiesGet())
                }}
              >
                {tr('cookies.remove')}
              </button>
            </div>
            <h4 className="section-title">Temp</h4>
            <p className="mono cookies-state">{temp ? tr('temp.usage', { n: temp.files, b: fmtBytes(temp.bytes) }) : ''}</p>
            <button
              className="btn small ghost"
              onClick={async () => {
                const r = await clearTemp()
                setTemp(await tempInfo())
                setRawState(r ? `cleared ${r.removed}` : null)
              }}
            >
              {tr('temp.clear')}
            </button>
          </section>
        </div>

        <footer className="modal-foot">
          {job?.status === 'downloading' ? 'downloading source' : mock ? `${tr('mock.badge')} sandbox render` : tr('player.hint')}
        </footer>
      </div>
    </div>
  )
}
