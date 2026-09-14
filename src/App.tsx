import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { I18nProvider, useI18n } from './i18n'
import { Header } from './components/Header'
import { CommandBar, type CmdValues } from './components/CommandBar'
import { Pipeline, type StageKey, type StageState } from './components/Pipeline'
import { Heatmap } from './components/Heatmap'
import { ClipCard } from './components/ClipCard'
import { PlayerModal } from './components/PlayerModal'
import { HistoryPanel } from './components/HistoryPanel'
import { ClipStudioModal } from './components/ClipStudioModal'
import { streamAnalyze } from './lib/api'
import { uploadVideo, type UploadMeta } from './lib/upload'
import { getCapabilities } from './lib/render'
import {
  dropResult, loadCfg, loadHistory, loadLang, loadResult,
  saveCfg, saveHistory, saveResult,
} from './lib/storage'
import { chapterLines, copyText, plainLines, titledLines } from './lib/time'
import type { AnalysisResult, Capabilities, Clip, HistoryEntry, StageEvent, StreamHandlers } from './types'
import { IconCheck } from './components/Icons'

type CopyFmt = 'plain' | 'titled' | 'chapters'

const IDLE_STAGES: Record<StageKey, StageState> = {
  resolve: { status: 'wait' },
  transcript: { status: 'wait' },
  signal: { status: 'wait' },
  analyze: { status: 'wait' },
}

function Shell() {
  const { tr, lang } = useI18n()
  const [cmd, setCmd] = useState<CmdValues>({ url: '', duration: '30s', focus: '', count: 8, transcript: '', sandbox: false })
  const [running, setRunning] = useState(false)
  const [stages, setStages] = useState<Record<StageKey, StageState>>(IDLE_STAGES)
  const [result, setResult] = useState<AnalysisResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [toast, setToast] = useState<string | null>(null)
  const [copyMenu, setCopyMenu] = useState(false)
  const [history, setHistory] = useState<HistoryEntry[]>([])
  const [historyOpen, setHistoryOpen] = useState(false)
  const [player, setPlayer] = useState<{ videoId: string; start: number; title: string } | null>(null)
  const [studio, setStudio] = useState<number | null>(null)
  const [caps, setCaps] = useState<Capabilities | null>(null)
  const [copyFmt, setCopyFmt] = useState<CopyFmt>('plain')
  const [upload, setUpload] = useState<UploadMeta | null>(null)
  const [uploadPct, setUploadPct] = useState<number | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  useEffect(() => {
    setHistory(loadHistory())
    setCmd((c) => ({ ...c, duration: loadCfg().duration, count: loadCfg().clipCount }))
    void getCapabilities()
      .then((c) => {
        setCaps(c)
        if (!c.server_key) setCmd((p) => ({ ...p, sandbox: true }))
      })
      .catch(() => setCaps(null))
  }, [])

  useEffect(() => {
    if (!toast) return
    const t = window.setTimeout(() => setToast(null), 2400)
    return () => window.clearTimeout(t)
  }, [toast])

  useEffect(() => {
    if (!copyMenu) return
    const h = (e: MouseEvent) => {
      if (!(e.target as HTMLElement).closest('.copy-menu')) setCopyMenu(false)
    }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [copyMenu])

  const patchCmd = useCallback((patch: Partial<CmdValues>) => {
    setCmd((c) => {
      const next = { ...c, ...patch }
      if (patch.duration !== undefined || patch.count !== undefined) {
        saveCfg({ duration: next.duration, clipCount: next.count, showPlayer: true })
      }
      return next
    })
  }, [])

  const setStage = (k: StageKey, s: StageState) => setStages((prev) => ({ ...prev, [k]: s }))

  const runAnalyze = useCallback(async () => {
    if (running) return
    setRunning(true)
    setError(null)
    setResult(null)
    setStages(IDLE_STAGES)
    const ctl = new AbortController()
    abortRef.current = ctl

    const reset: Record<StageKey, StageState> = {
      resolve: { status: 'active' },
      transcript: { status: 'wait' },
      signal: { status: 'wait' },
      analyze: { status: 'wait' },
    }
    setStages(reset)

    const handlers: StreamHandlers = {
      onMeta: () => setStage('resolve', { status: 'done' }),
      onTitle: () => undefined,
      onStage: (ev: StageEvent) => {
        if (ev.stage === 'transcript') {
          setStage('transcript', { status: ev.status === 'done' ? 'done' : 'active', detail: `${ev.source ?? ''}${ev.segments ? ` · ${ev.segments}` : ''}`.trim() })
          setStage('signal', { status: 'active' })
        } else if (ev.stage === 'signal') {
          setStage('signal', { status: 'done', detail: ev.mode ?? '' })
          setStage('analyze', { status: 'active' })
        } else if (ev.stage === 'analyze') {
          if (ev.status === 'done') setStage('analyze', { status: 'done', detail: `${ev.model ?? ''}${ev.clips ? ` · ${ev.clips}` : ''}`.trim() })
          else if (ev.status === 'retry') setStage('analyze', { status: 'retry', detail: `retry ${ev.model ?? ''}` })
          else if (ev.status === 'discovered') setStage('analyze', { status: 'active', detail: (ev.models ?? []).slice(0, 3).join(' · ') })
        }
      },
      onDone: (r: AnalysisResult) => {
        const full = { ...r, created_at: Date.now() }
        setResult(full)
        saveResult(full)
        const entry: HistoryEntry = {
          key: `${full.video_id}:${full.created_at}`,
          video_id: full.video_id,
          title: full.title,
          author: full.author,
          created_at: full.created_at,
          clip_count: full.clips.length,
          duration: cmd.duration,
        }
        setHistory((h) => {
          const next = [entry, ...h].slice(0, 60)
          saveHistory(next)
          return next
        })
        setToast(tr('res.meta', { n: full.clips.length, s: Math.round(full.total_seconds), m: full.model, t: full.elapsed_ms }))
      },
      onError: (e) => {
        const stage: StageKey = e.stage === 'transcript' ? 'transcript' : 'analyze'
        setStage(stage, { status: 'fail', detail: '' })
        if (e.code === 'no_key') setError(tr('err.nokey'))
        else if (e.code === 'no_value') setError(tr('err.novalue'))
        else setError(e.stage === 'transcript' ? tr('err.transcript') : tr('err.analyze'))
      },
    }

    try {
      await streamAnalyze(
        {
          url: upload ? '' : cmd.url.trim(),
          duration: cmd.duration,
          api_key: cmd.sandbox ? 'mock' : '',
          custom_prompt: cmd.focus.trim() || undefined,
          target_clip_count: cmd.count,
          subtitles: cmd.transcript.trim() || undefined,
          language: lang,
          upload_id: upload?.id,
        },
        handlers,
        ctl.signal
      )
    } catch (e) {
      if ((e as Error).name !== 'AbortError') {
        setError(tr('err.generic'))
      }
    } finally {
      setRunning(false)
      abortRef.current = null
    }
  }, [cmd, running, tr, lang, upload])

  const stopAnalyze = useCallback(() => abortRef.current?.abort(), [])

  const pickUpload = useCallback(
    async (file: File) => {
      setError(null)
      setUploadPct(0)
      try {
        const meta = await uploadVideo(file, (p) => setUploadPct(p))
        setUpload(meta)
        setCmd((c) => ({ ...c, sandbox: false }))
        setToast(tr('toast.uploaded', { n: meta.filename, d: Math.round(meta.duration) }))
      } catch (e) {
        setError((e as Error).message || tr('err.upload'))
      } finally {
        setUploadPct(null)
      }
    },
    [tr]
  )

  const clearUpload = useCallback(() => setUpload(null), [])

  const doCopy = useCallback(
    async (clips: Clip[], fmtName: CopyFmt, all: boolean) => {
      const text = fmtName === 'plain' ? plainLines(clips) : fmtName === 'titled' ? titledLines(clips) : chapterLines(clips)
      const ok = await copyText(text)
      if (ok) setToast(all ? tr('toast.copied', { n: clips.length }) : tr('toast.one'))
    },
    [tr]
  )

  const loadFromHistory = useCallback((entry: HistoryEntry) => {
    const r = loadResult(entry.video_id)
    if (r) {
      setResult(r)
      setError(null)
      setStages(IDLE_STAGES)
      setHistoryOpen(false)
    }
  }, [])

  const deleteHistoryEntry = useCallback((key: string) => {
    const vid = key.split(':')[0]
    setHistory((h) => {
      const next = h.filter((e) => e.key !== key)
      saveHistory(next)
      return next
    })
    if (!history.some((e) => e.key !== key && e.video_id === vid)) dropResult(vid)
  }, [history])

  const clearHistory = useCallback(() => {
    setHistory([])
    saveHistory([])
    history.forEach((e) => dropResult(e.video_id))
  }, [history])

  const signalLabel = useMemo(() => {
    if (!result) return ''
    if (result.signal_mode === 'youtube-real') return tr('sig.youtube')
    if (result.signal_mode === 'estimated') return tr('sig.estimated')
    return tr('sig.mock')
  }, [result, tr])

  return (
    <div className="app">
      <Header onToggleHistory={() => setHistoryOpen((s) => !s)} historyOpen={historyOpen} />
      <main className={`layout${historyOpen ? ' with-history' : ''}`}>
        <div className="col-main">
          <CommandBar
            values={cmd}
            onChange={patchCmd}
            onSubmit={runAnalyze}
            running={running}
            onStop={stopAnalyze}
            serverKey={caps?.server_key ?? false}
            upload={upload}
            uploadPct={uploadPct}
            onPickUpload={(f) => void pickUpload(f)}
            onClearUpload={clearUpload}
            maxUploadMb={caps?.max_upload_mb ?? 2000}
          />
          <Pipeline stages={stages} />
          {error && (
            <div className="error-box" role="alert">
              {error}
            </div>
          )}
          {result && (
            <section className="results" aria-live="polite">
              <header className="res-head">
                <div>
                  <h3 className="res-title">{result.title}</h3>
                  <span className="res-meta mono">
                    {tr('res.meta', { n: result.clips.length, s: Math.round(result.total_seconds), m: result.model, t: result.elapsed_ms })}
                    {' · '}
                    <b className="sig-chip">{signalLabel}</b>
                    {result.mock && <b className="mock-chip">{tr('mock.badge')}</b>}
                  </span>
                </div>
                <div className="copy-menu">
                  <button className="btn small ghost" onClick={() => setCopyMenu((s) => !s)} aria-expanded={copyMenu}>
                    {tr('res.copyAll')} ▾
                  </button>
                  {copyMenu && (
                    <div className="copy-pop" role="menu">
                      {(['plain', 'titled', 'chapters'] as CopyFmt[]).map((f) => (
                        <button key={f} role="menuitem" className={copyFmt === f ? 'on' : ''} onClick={() => { setCopyFmt(f); setCopyMenu(false); void doCopy(result.clips, f, true) }}>
                          {copyFmt === f && <IconCheck />}
                          {tr(`res.fmt.${f}`)}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              </header>
              <Heatmap points={result.heatmap} total={result.total_seconds} clips={result.clips} onSeek={(t) => setPlayer({ videoId: result.video_id, start: t, title: result.title })} />
              <div className="clip-grid">
                {result.clips.map((c, i) => (
                  <ClipCard
                    key={`${c.start}-${i}`}
                    clip={c}
                    rank={i + 1}
                    onPlay={() => setPlayer({ videoId: result.video_id, start: c.start, title: c.title })}
                    onStudio={() => setStudio(i)}
                    onCopy={() => void doCopy([c], 'plain', false)}
                  />
                ))}
              </div>
            </section>
          )}
        </div>
        {historyOpen && (
          <HistoryPanel
            entries={history}
            onLoad={loadFromHistory}
            onDelete={deleteHistoryEntry}
            onClear={clearHistory}
            onClose={() => setHistoryOpen(false)}
          />
        )}
      </main>
      {toast && <div className="toast">{toast}</div>}
      {player && <PlayerModal videoId={player.videoId} start={player.start} title={player.title} uploaded={result?.uploaded} onClose={() => setPlayer(null)} />}
      {result && studio !== null && (
          <ClipStudioModal videoId={result.video_id} clips={result.clips} initialIndex={studio} mock={result.mock} uploaded={result.uploaded} caps={caps} onClose={() => setStudio(null)} />
      )}
    </div>
  )
}

export default function App() {
  return (
    <I18nProvider initial={loadLang()}>
      <Shell />
    </I18nProvider>
  )
}
