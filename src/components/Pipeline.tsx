import { useI18n } from '../i18n'
import { IconAlert, IconCheck, IconSpinner } from './Icons'

export type StageKey = 'resolve' | 'transcript' | 'signal' | 'analyze'
export type StageStatus = 'wait' | 'active' | 'done' | 'retry' | 'fail'

export interface StageState {
  status: StageStatus
  detail?: string
}

const ORDER: StageKey[] = ['resolve', 'transcript', 'signal', 'analyze']

export function Pipeline({ stages }: { stages: Record<StageKey, StageState> }) {
  const { tr } = useI18n()
  const anyActive = ORDER.some((k) => stages[k].status === 'active' || stages[k].status === 'retry')

  return (
    <section className="pipeline" aria-label={tr('stages.title')} aria-live="polite">
      <h3 className="section-title">{tr('stages.title')}</h3>
      <ol className="stage-list">
        {ORDER.map((k, i) => {
          const s = stages[k]
          return (
            <li key={k} className={`stage s-${s.status}`}>
              <span className="stage-icon">
                {s.status === 'done' ? <IconCheck /> : s.status === 'fail' ? <IconAlert /> : s.status === 'active' || s.status === 'retry' ? <IconSpinner /> : <span className="dash" />}
              </span>
              <span className="stage-label">
                {String(i + 1).padStart(2, '0')} {tr(`stages.${k}`)}
              </span>
              <span className={`stage-detail mono${anyActive && s.status === 'active' ? ' shimmer' : ''}`}>{s.detail ?? ''}</span>
            </li>
          )
        })}
      </ol>
    </section>
  )
}
