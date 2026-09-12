import { useEffect, useRef, useState } from 'react'
import type { Clip, HeatPoint } from '../types'
import { fmt } from '../lib/time'

interface Hover {
  x: number
  t: number
  score: number
}

export function Heatmap({
  points,
  total,
  clips,
  onSeek,
}: {
  points: HeatPoint[]
  total: number
  clips: Clip[]
  onSeek: (t: number) => void
}) {
  const wrapRef = useRef<HTMLDivElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [size, setSize] = useState({ w: 0, h: 132 })
  const [hover, setHover] = useState<Hover | null>(null)

  useEffect(() => {
    const el = wrapRef.current
    if (!el) return
    const ro = new ResizeObserver(() => {
      setSize({ w: el.clientWidth, h: 132 })
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  useEffect(() => {
    const cv = canvasRef.current
    if (!cv || size.w === 0) return
    const dpr = window.devicePixelRatio || 1
    cv.width = size.w * dpr
    cv.height = size.h * dpr
    const ctx = cv.getContext('2d')
    if (!ctx) return
    ctx.scale(dpr, dpr)
    const W = size.w
    const H = size.h
    const PAD_B = 22
    const draw = H - PAD_B
    const xAt = (t: number) => (total > 0 ? (t / total) * W : 0)

    ctx.clearRect(0, 0, W, H)

    // grid + time axis
    ctx.strokeStyle = 'rgba(120,135,150,.14)'
    ctx.fillStyle = 'rgba(138,150,163,.85)'
    ctx.font = '10px "JetBrains Mono", monospace'
    ctx.textAlign = 'center'
    ctx.lineWidth = 1
    const tickEvery = total > 5400 ? 1200 : total > 2400 ? 600 : total > 900 ? 300 : 60
    for (let t = 0; t <= total; t += tickEvery) {
      const x = Math.min(W - 1, Math.max(0, xAt(t)))
      ctx.beginPath()
      ctx.moveTo(x, 8)
      ctx.lineTo(x, draw)
      ctx.stroke()
      ctx.fillText(fmt(t), x, H - 8)
    }

    if (points.length === 0) return

    // area
    const grad = ctx.createLinearGradient(0, 0, 0, draw)
    grad.addColorStop(0, 'rgba(203,242,60,.55)')
    grad.addColorStop(1, 'rgba(203,242,60,.04)')
    ctx.beginPath()
    ctx.moveTo(0, draw)
    for (const p of points) {
      ctx.lineTo(xAt(p[0]), draw - p[1] * (draw - 12))
    }
    ctx.lineTo(W, draw)
    ctx.closePath()
    ctx.fillStyle = grad
    ctx.fill()

    // stroke line
    ctx.beginPath()
    points.forEach((p, i) => {
      const x = xAt(p[0])
      const y = draw - p[1] * (draw - 12)
      if (i === 0) ctx.moveTo(x, y)
      else ctx.lineTo(x, y)
    })
    ctx.strokeStyle = '#cbf23c'
    ctx.lineWidth = 1.6
    ctx.stroke()

    // clip markers
    clips.forEach((c, i) => {
      const x = xAt(c.start)
      ctx.strokeStyle = 'rgba(232,237,242,.34)'
      ctx.setLineDash([3, 3])
      ctx.beginPath()
      ctx.moveTo(x, 8)
      ctx.lineTo(x, draw)
      ctx.stroke()
      ctx.setLineDash([])
      const y = draw - c.score * (draw - 12)
      ctx.fillStyle = '#cbf23c'
      ctx.beginPath()
      ctx.arc(x, y, 3, 0, Math.PI * 2)
      ctx.fill()
      ctx.font = '600 9px "JetBrains Mono", monospace'
      ctx.textAlign = 'left'
      ctx.fillText(String(i + 1), Math.min(W - 12, x + 5), Math.max(12, y - 6))
    })

    // hover crosshair
    if (hover) {
      ctx.strokeStyle = 'rgba(232,237,242,.5)'
      ctx.beginPath()
      ctx.moveTo(hover.x, 8)
      ctx.lineTo(hover.x, draw)
      ctx.stroke()
    }
  }, [points, total, clips, size, hover])

  const handleMove = (e: React.MouseEvent) => {
    const rect = canvasRef.current?.getBoundingClientRect()
    if (!rect || points.length === 0) return
    const x = e.clientX - rect.left
    const t = (x / rect.width) * total
    let best = points[0]
    let bd = Infinity
    for (const p of points) {
      const d = Math.abs(p[0] - t)
      if (d < bd) {
        bd = d
        best = p
      }
    }
    setHover({ x: (best[0] / total) * rect.width, t: best[0], score: best[1] })
  }

  return (
    <div className="heatmap-wrap">
      <div
        ref={wrapRef}
        className="heatmap"
        onMouseMove={handleMove}
        onMouseLeave={() => setHover(null)}
        onClick={(e) => {
          const rect = canvasRef.current?.getBoundingClientRect()
          if (!rect || total <= 0) return
          onSeek(((e.clientX - rect.left) / rect.width) * total)
        }}
      >
        <canvas ref={canvasRef} style={{ width: size.w, height: size.h }} />
        {hover && (
          <div className="heat-tip mono" style={{ left: Math.min(Math.max(hover.x + 10, 8), size.w - 96) }}>
            {fmt(hover.t)} · {(hover.score * 100).toFixed(0)}%
          </div>
        )}
      </div>
    </div>
  )
}
