export interface TranscriptSegment {
  start: number
  duration: number
  text: string
}

export interface Clip {
  start: number
  end: number
  title: string
  reason: string
  quote: string
  score: number
}

export interface VideoMeta {
  video_id: string
  title: string
  author: string
}

export type HeatPoint = [start: number, score: number, end: number]

export interface AnalysisResult {
  video_id: string
  title: string
  author: string
  mock: boolean
  total_seconds: number
  signal_mode: 'youtube-real' | 'mock' | 'estimated'
  clips: Clip[]
  model: string
  elapsed_ms: number
  heatmap: HeatPoint[]
  created_at: number
}

export interface StageEvent {
  stage: 'transcript' | 'signal' | 'analyze'
  status: 'run' | 'retry' | 'done' | 'discovered'
  model?: string
  models?: string[]
  attempt?: number
  error?: string
  source?: string
  mode?: string
  segments?: number
  points?: number
  clips?: number
}

export interface AnalyzeRequest {
  url: string
  duration: string
  api_key: string
  custom_prompt?: string
  target_clip_count: number
  subtitles?: string
}

export interface StreamHandlers {
  onMeta?: (videoId: string) => void
  onTitle?: (m: VideoMeta) => void
  onStage?: (e: StageEvent) => void
  onDone?: (r: AnalysisResult) => void
  onError?: (e: { stage: string; message: string }) => void
}

export interface HistoryEntry {
  key: string
  video_id: string
  title: string
  author: string
  created_at: number
  clip_count: number
  duration: string
}

// ── v2 render studio ──────────────────────────────────────────

export type Aspect = '9:16' | '1:1' | '4:3' | '16:9'
export type Layout = 'fullscreen' | 'split' | 'pip'
export type Backdrop = 'blur' | 'black'

export interface RenderOpts {
  aspect: Aspect
  layout: Layout
  backdrop: Backdrop
  face_track: boolean
  preset: string
  subtitle_v: number
  title: string
  nvenc: boolean
  cookies: boolean
  sandbox: boolean
}

export interface Capabilities {
  ffmpeg: boolean
  ffmpeg_version: string
  nvenc: boolean
  cv2: boolean
  ytdlp: boolean
  presets: string[]
  aspects: string[]
  layouts: string[]
  backdrops: string[]
}

export interface RenderItem {
  key: string
  status: 'pending' | 'rendering' | 'done' | 'failed'
  file: string | null
  error?: string | null
}

export interface RenderProgress {
  id: string
  status: 'queued' | 'downloading' | 'running' | 'done' | 'partial' | 'failed'
  total: number
  done: number
  items: RenderItem[]
  zip: string | null
  log: string[]
}
