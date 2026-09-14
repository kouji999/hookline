import { useRef, useState } from 'react'
import { useI18n } from '../i18n'
import { extractVideoId } from '../lib/time'
import type { UploadMeta } from '../lib/upload'
import { IconChevron } from './Icons'

const DURATIONS = ['15s', '30s', '60s']
const VIDEO_ACCEPT = '.mp4,.mov,.m4v,.mkv,.webm'

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
  upload,
  uploadPct,
  onPickUpload,
  onClearUpload,
  maxUploadMb,
}: {
  values: CmdValues
  onChange: (patch: Partial<CmdValues>) => void
  onSubmit: () => void
  running: boolean
  onStop: () => void
  serverKey: boolean
  upload: UploadMeta | null
  uploadPct: number | null
  onPickUpload: (file: File) => void
  onClearUpload: () => void
  maxUploadMb: number
}) {
  const { tr } = useI18n()
  const [showTranscript, setShowTranscript] = useState(false)
  const [dragOver, setDragOver] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)
  const vid = values.url.trim() ? extractVideoId(values.url) : null
  const urlInvalid = values.url.trim().length > 0 && !vid
  const ready = upload ? true : Boolean(vid)
  const fmtBytes = (b: number) => (b > 1048576 ? `${(b / 1048576).toFixed(0)} MB` : `${(b / 1024).toFixed(0)} KB`)

  return (
    <form
      className="command"
      onSubmit={(e) => {
        e.preventDefault()
        if (!running) onSubmit()
      }}
    >
      <div className="source-toggle" role="group" aria-label={tr('cmd.source')}>
        <button type="button" className={!upload ? 'on' : ''} aria-pressed={!upload} onClick={() => onClearUpload()} disabled={running || uploadPct !== null}>
          {tr('cmd.source.link')}
        </button>
        <button type="button" className={upload ? 'on' : ''} aria-pressed={!!upload} onClick={() => fileRef.current?.click()} disabled={running || uploadPct !== null}>
          {tr('cmd.source.file')}
        </button>
        <input
          ref={fileRef}
          type="file"
          accept={VIDEO_ACCEPT}
          className="visually-hidden"
          onChange={(e) => {
            const f = e.target.files?.[0]
            if (f) onPickUpload(f)
            e.target.value = ''
          }}
        />
      </div>

      {upload ? (
        <div
          className={`upload-card${dragOver ? ' over' : ''}`}
          onDragOver={(e) => {
            e.preventDefault()
            setDragOver(true)
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => {
            e.preventDefault()
            setDragOver(false)
            const f = e.dataTransfer.files?.[0]
            if (f) onPickUpload(f)
          }}
        >
          <div className="upload-info">
            <b>{upload.filename}</b>
            <span className="mono">
              {Math.round(upload.duration)}s · {upload.width}x{upload.height} · {fmtBytes(upload.bytes)}
              {upload.has_audio ? '' : ` · ${tr('upload.mute')}`}
            </span>
          </div>
          <button type="button" className="btn small ghost" onClick={onClearUpload} disabled={running}>
            {tr('upload.replace')}
          </button>
        </div>
      ) : (
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
          {uploadPct !== null && <span className="mono upload-pct">{uploadPct}%</span>}
          <span className="field-hint">{tr('cmd.url.hint', { mb: maxUploadMb })}</span>
        </label>
      )}
      {uploadPct !== null && (
        <div className="queue-progress" role="progressbar" aria-valuenow={uploadPct} aria-valuemin={0} aria-valuemax={100}>
          <div className="qp-bar" style={{ width: `${uploadPct}%` }} />
        </div>
      )}

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
          <button type="submit" className="btn primary" disabled={!ready || urlInvalid || uploadPct !== null}>
            {upload ? tr('cmd.run.clip') : tr('cmd.run')}
          </button>
        )}
        <label className={`check sandbox${values.sandbox ? ' on' : ''}`}>
          <input type="checkbox" checked={values.sandbox} onChange={(e) => onChange({ sandbox: e.target.checked })} disabled={running || !!upload} />
          {tr('cmd.sandbox')}
          <span className="sb-hint mono">{tr('cmd.sandbox.hint')}</span>
        </label>
        <span className={`hint engine ${serverKey && !(values.sandbox && !upload) ? 'ok' : ''}`}>
          {upload ? (serverKey ? tr('cmd.engine.live') : tr('cmd.engine.none')) : values.sandbox ? tr('cmd.engine.sandbox') : serverKey ? tr('cmd.engine.live') : tr('cmd.engine.none')}
        </span>
      </div>
    </form>
  )
}
