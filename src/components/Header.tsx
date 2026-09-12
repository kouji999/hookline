import { useI18n, type Lang } from '../i18n'
import { LogoMark } from './Icons'
import { saveLang } from '../lib/storage'

export function Header({ onToggleHistory, historyOpen }: { onToggleHistory: () => void; historyOpen: boolean }) {
  const { lang, setLang, tr } = useI18n()

  const switchTo = (l: Lang) => {
    setLang(l)
    saveLang(l)
  }

  return (
    <header className="topbar">
      <div className="brand">
        <LogoMark />
        <div className="brand-text">
          <span className="brand-name">rewatch</span>
          <span className="brand-tag">{tr('header.tagline')}</span>
        </div>
      </div>
      <div className="topbar-actions">
        <div className="lang-toggle" role="group" aria-label="Language">
          <button className={lang === 'en' ? 'on' : ''} onClick={() => switchTo('en')} aria-pressed={lang === 'en'}>
            EN
          </button>
          <button className={lang === 'id' ? 'on' : ''} onClick={() => switchTo('id')} aria-pressed={lang === 'id'}>
            ID
          </button>
        </div>
        <button className="ghost-btn" onClick={onToggleHistory} aria-pressed={historyOpen}>
          <LogoHistory />
          {tr('hist.title')}
        </button>
      </div>
    </header>
  )
}

function LogoHistory() {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden="true">
      <path d="M2.5 8a5.5 5.5 0 1 0 1.6-3.9M2.5 2.8V5h2.2" strokeLinecap="round" />
      <path d="M8 5.2V8l2 1.6" strokeLinecap="round" />
    </svg>
  )
}
