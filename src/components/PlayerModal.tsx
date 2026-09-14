import { useEffect, useRef } from 'react'
import { useI18n } from '../i18n'
import { IconClose } from './Icons'

export function PlayerModal({
  videoId,
  start,
  title,
  uploaded,
  onClose,
}: {
  videoId: string
  start: number
  title: string
  uploaded?: boolean
  onClose: () => void
}) {
  const { tr } = useI18n()
  const videoRef = useRef<HTMLVideoElement>(null)

  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [onClose])

  useEffect(() => {
    if (!uploaded) return
    const el = videoRef.current
    if (!el) return
    const seek = () => {
      el.currentTime = Math.max(0, start)
    }
    if (el.readyState >= 1) seek()
    else el.addEventListener('loadedmetadata', seek, { once: true })
  }, [uploaded, start])

  const s = Math.max(0, Math.floor(start))
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
          {uploaded ? (
            <video ref={videoRef} src={`/api/source-video/${encodeURIComponent(videoId)}`} controls autoPlay playsInline />
          ) : (
            <iframe src={`https://www.youtube-nocookie.com/embed/${videoId}?start=${s}&autoplay=1&rel=0`} title={title} allow="autoplay; encrypted-media; picture-in-picture" allowFullScreen />
          )}
        </div>
        <footer className="modal-foot">{uploaded ? tr('player.hint.clip') : tr('player.hint')}</footer>
      </div>
    </div>
  )
}
