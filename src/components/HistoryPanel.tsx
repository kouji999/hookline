import { useMemo, useState } from 'react'
import type { HistoryEntry } from '../types'
import { useI18n } from '../i18n'
import { IconClose, IconTrash } from './Icons'

function ago(ts: number, lang: string): string {
  const d = Math.floor((Date.now() - ts) / 1000)
  const out = (n: number, unit: string) => `${n}${unit}`
  if (lang === 'id') {
    if (d >= 86400) return out(Math.floor(d / 86400), ' hr')
    if (d >= 3600) return out(Math.floor(d / 3600), ' jam')
    if (d >= 60) return out(Math.floor(d / 60), ' mnt')
    return out(d, ' dtk')
  }
  if (d >= 86400) return out(Math.floor(d / 86400), 'd')
  if (d >= 3600) return out(Math.floor(d / 3600), 'h')
  if (d >= 60) return out(Math.floor(d / 60), 'm')
  return out(d, 's')
}

export function HistoryPanel({
  entries,
  onLoad,
  onDelete,
  onClear,
  onClose,
}: {
  entries: HistoryEntry[]
  onLoad: (e: HistoryEntry) => void
  onDelete: (key: string) => void
  onClear: () => void
  onClose: () => void
}) {
  const { tr, lang } = useI18n()
  const [q, setQ] = useState('')
  const [confirm, setConfirm] = useState(false)

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase()
    if (!needle) return entries
    return entries.filter((e) => `${e.title} ${e.author} ${e.video_id}`.toLowerCase().includes(needle))
  }, [entries, q])

  return (
    <aside className="history" aria-label={tr('hist.title')}>
      <header className="hist-head">
        <h3 className="section-title">{tr('hist.title')}</h3>
        <button className="icon-btn" onClick={onClose} aria-label="close">
          <IconClose />
        </button>
      </header>
      <input type="search" className="hist-search" placeholder={tr('hist.search')} value={q} onChange={(e) => setQ(e.target.value)} />
      {filtered.length === 0 ? (
        <p className="hist-empty">{tr('hist.empty')}</p>
      ) : (
        <ul className="hist-list">
          {filtered.map((e) => (
            <li key={e.key}>
              <button className="hist-item" onClick={() => onLoad(e)} disabled={e.clip_count === 0}>
                <span className="hist-title">{e.title}</span>
                <span className="hist-meta mono">
                  {e.clip_count} · {ago(e.created_at, lang)}
                </span>
              </button>
              <button className="icon-btn del" onClick={() => onDelete(e.key)} aria-label={tr('hist.delete')}>
                <IconTrash />
              </button>
            </li>
          ))}
        </ul>
      )}
      {entries.length > 0 && (
        <footer className="hist-foot">
          {confirm ? (
            <>
              <span className="hist-confirm">{tr('hist.confirm')}</span>
              <button className="btn small" onClick={onClear}>
                OK
              </button>
              <button className="btn small ghost" onClick={() => setConfirm(false)}>
                <IconClose />
              </button>
            </>
          ) : (
            <button className="btn small ghost" onClick={() => setConfirm(true)}>
              <IconTrash /> {tr('hist.clear')}
            </button>
          )}
        </footer>
      )}
    </aside>
  )
}
