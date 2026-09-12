import type { Clip } from '../types'
import { fmt, fmtRange } from '../lib/time'
import { useI18n } from '../i18n'
import { IconCopy, IconPlay } from './Icons'

function IconStudio() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden="true">
      <rect x="2.2" y="1.8" width="11.6" height="12.4" rx="2" />
      <path d="M5.2 11V7m2.8 4V5m2.8 6V8.4" strokeLinecap="round" />
    </svg>
  )
}

export function ClipCard({ clip, rank, onPlay, onCopy, onStudio }: { clip: Clip; rank: number; onPlay: () => void; onCopy: () => void; onStudio: () => void }) {
  const { tr } = useI18n()
  const pct = Math.round(clip.score * 100)
  return (
    <article className="clip-card" style={{ animationDelay: `${Math.min(rank, 12) * 35}ms` }}>
      <header className="clip-head">
        <span className="clip-rank mono">{String(rank).padStart(2, '0')}</span>
        <h4 className="clip-title">{clip.title}</h4>
        <span className="clip-range mono chip">{fmtRange(clip)}</span>
      </header>
      <div className="score-row" title={`attention score ${pct}%`}>
        <div className="score-track" role="img" aria-label={`score ${pct} percent`}>
          <div className="score-fill" style={{ width: `${pct}%` }} />
        </div>
        <span className="score-num mono">{pct}</span>
      </div>
      <blockquote className="clip-quote">“{clip.quote}”</blockquote>
      <p className="clip-reason">{clip.reason}</p>
      <footer className="clip-actions">
        <button className="btn small" onClick={onPlay}>
          <IconPlay /> {tr('clip.play')} <span className="mono">{fmt(clip.start)}</span>
        </button>
        <button className="btn small ghost" onClick={onStudio}>
          <IconStudio /> {tr('studio.open')}
        </button>
        <button className="btn small ghost" onClick={onCopy}>
          <IconCopy /> {tr('clip.copy')}
        </button>
      </footer>
    </article>
  )
}
