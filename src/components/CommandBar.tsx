import { useState } from 'react'
import { useI18n } from '../i18n'
import { extractVideoId } from '../lib/time'
import { IconChevron } from './Icons'

const DURATIONS = ['15s', '30s', '60s']

export interface CmdValues {
  url: string
  duration: string
  focus: string
  count: number
  transcript: string
  sandbox: boolean
}

export function CommandBar({
  values,
  onChange,
  onSubmit,
  running,
  onStop,
  serverKey,
}: {
  values: CmdValues
  onChange: (patch: Partial<CmdValues>) => void
  onSubmit: () => void
  running: boolean
  onStop: () => void
  serverKey: boolean
}) {
  const { tr } = useI18n()
  const [showTranscript, setShowTranscript] = useState(false)
  const vid = values.url.trim() ? extractVideoId(values.url) : null
  const urlInvalid = values.url.trim().length > 0 && !vid

  return (
    <form
      className="command"
      onSubmit={(e) => {
        e.preventDefault()
        if (!running) onSubmit()
      }}
    >
      <div className="cmd-row">
        <label className="field grow">
          <span className="field-label">{tr('cmd.url')}</span>
          <input
            type="text"
            inputMode="url"
            placeholder={tr('cmd.url.placeholder')}
            value={values.url}
            onChange={(e) => onChange({ url: e.target.value })}
            aria-invalid={urlInvalid}
            disabled={running}
          />
          {vid && <span className="field-chip mono">{vid}</span>}
        </label>
        <fieldset className="seg">
          <legend className="field-label">{tr('cmd.duration')}</legend>
          <div className="seg-group">
            {DURATIONS.map((d) => (
              <button
                key={d}
                type="button"
                className={values.duration === d ? 'on' : ''}
                onClick={() => onChange({ duration: d })}
                aria-pressed={values.duration === d}
                disabled={running}
              >
                <span className="mono">{d}</span>
              </button>
            ))}
          </div>
        </fieldset>
      </div>

      <label className="field">
        <span className="field-label">{tr('cmd.focus')}</span>
        <input
          type="text"
          placeholder={tr('cmd.focus.placeholder')}
          value={values.focus}
          onChange={(e) => onChange({ focus: e.target.value })}
          disabled={running}
        />
      </label>

      <div className="cmd-meta">
        <label className="field count">
          <span className="field-label">{tr('cmd.count')}</span>
          <input
            type="number"
            min={1}
            max={20}
            value={values.count}
            onChange={(e) => onChange({ count: Math.min(20, Math.max(1, Number(e.target.value) || 1)) })}
            disabled={running}
            className="mono"
          />
        </label>
        <button type="button" className="link-toggle" onClick={() => setShowTranscript((s) => !s)} aria-expanded={showTranscript}>
          <IconChevron />
          {tr('cmd.transcript.toggle')}
        </button>
      </div>
      {showTranscript && (
        <textarea
          className="transcript-box"
          rows={5}
          placeholder={tr('cmd.transcript.placeholder')}
          value={values.transcript}
          onChange={(e) => onChange({ transcript: e.target.value })}
          disabled={running}
        />
      )}

      <div className="cmd-actions">
        {running ? (
          <button type="button" className="btn ghost" onClick={onStop}>
            {tr('cmd.stop')}
          </button>
        ) : (
          <button type="submit" className="btn primary" disabled={!values.url.trim() || urlInvalid}>
            {tr('cmd.run')}
          </button>
        )}
        <label className={`check sandbox${values.sandbox ? ' on' : ''}`}>
          <input type="checkbox" checked={values.sandbox} onChange={(e) => onChange({ sandbox: e.target.checked })} disabled={running} />
          {tr('cmd.sandbox')}
          <span className="sb-hint mono">{tr('cmd.sandbox.hint')}</span>
        </label>
        <span className={`hint engine ${serverKey && !values.sandbox ? 'ok' : ''}`}>
          {values.sandbox ? tr('cmd.engine.sandbox') : serverKey ? tr('cmd.engine.live') : tr('cmd.engine.none')}
        </span>
      </div>
    </form>
  )
}
