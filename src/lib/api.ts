import type { AnalyzeRequest, StreamHandlers } from '../types'

export async function streamAnalyze(req: AnalyzeRequest, h: StreamHandlers, signal: AbortSignal): Promise<void> {
  const res = await fetch('/api/analyze', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
    signal,
  })
  if (!res.ok || !res.body) {
    throw new Error(`backend error ${res.status}`)
  }
  const reader = res.body.getReader()
  const dec = new TextDecoder()
  let buf = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buf += dec.decode(value, { stream: true })
    for (;;) {
      const idx = buf.indexOf('\n\n')
      if (idx < 0) break
      const rawEvent = buf.slice(0, idx)
      buf = buf.slice(idx + 2)
      let event = 'message'
      let data = ''
      for (const line of rawEvent.split('\n')) {
        if (line.startsWith('event: ')) event = line.slice(7).trim()
        else if (line.startsWith('data: ')) data += line.slice(6)
      }
      if (!data) continue
      let obj: Record<string, unknown>
      try {
        obj = JSON.parse(data) as Record<string, unknown>
      } catch {
        continue
      }
      if (event === 'meta') h.onMeta?.(String(obj.video_id ?? ''))
      else if (event === 'title') h.onTitle?.(obj as never)
      else if (event === 'stage') h.onStage?.(obj as never)
      else if (event === 'done') h.onDone?.(obj as never)
      else if (event === 'error') h.onError?.(obj as never)
    }
  }
}
