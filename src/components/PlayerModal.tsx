import { useEffect } from 'react'
import { useI18n } from '../i18n'
import { IconClose } from './Icons'

export function PlayerModal({ videoId, start, title, onClose }: { videoId: string; start: number; title: string; onClose: () => void }) {
  const { tr } = useI18n()

  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [onClose])

  const s = Math.max(0, Math.floor(start))
  const src = `https://www.youtube-nocookie.com/embed/${videoId}?start=${s}&autoplay=1&rel=0`

  return (
    <div className="modal-backdrop" onClick={onClose} role="dialog" aria-modal="true" aria-label={title}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <header className="modal-head">
          <span className="modal-title">{title}</span>
          <button className="icon-btn" onClick={onClose} aria-label={tr('cmd.stop')}>
            <IconClose />
          </button>
        </header>
        <div className="modal-body">
          <iframe src={src} title={title} allow="autoplay; encrypted-media; picture-in-picture" allowFullScreen />
        </div>
        <footer className="modal-foot">{tr('player.hint')}</footer>
      </div>
    </div>
  )
}
