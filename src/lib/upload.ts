export interface UploadMeta {
  id: string
  filename: string
  duration: number
  width: number
  height: number
  has_audio: boolean
  bytes: number
}

export async function uploadVideo(file: File, onProgress?: (pct: number) => void): Promise<UploadMeta> {
  const form = new FormData()
  form.append('file', file)
  return await new Promise<UploadMeta>((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', '/api/upload')
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress?.(Math.round((e.loaded / e.total) * 100))
    }
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText) as UploadMeta)
        } catch {
          reject(new Error('upload: invalid response'))
        }
      } else {
        let msg = `upload failed (${xhr.status})`
        try {
          const j = JSON.parse(xhr.responseText) as { detail?: string }
          if (j.detail) msg = j.detail
        } catch {
          /* keep generic message */
        }
        reject(new Error(msg))
      }
    }
    xhr.onerror = () => reject(new Error('upload: network error'))
    xhr.send(form)
  })
}

export async function deleteUpload(id: string): Promise<void> {
  await fetch(`/api/uploads/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

export async function listUploads(): Promise<{ uploads: UploadMeta[]; max_mb: number }> {
  const r = await fetch('/api/uploads')
  if (!r.ok) return { uploads: [], max_mb: 0 }
  return (await r.json()) as { uploads: UploadMeta[]; max_mb: number }
}
