export type HealthResponse = {
  ok: boolean
  database: boolean
  ffmpeg: boolean
  keyring: boolean
  versions: Record<string, string | null>
  messages: string[]
}

export type ComplianceStatus = {
  accepted: boolean
  version: string
  accepted_at: string | null
  statement: string
}

export type CookieStatus = {
  configured: boolean
  masked: string | null
  keyring_available: boolean
  message: string | null
}

export type ContainerFormat = 'mp4' | 'mkv'
export type AudioOutputFormat = 'm4a' | 'mp3'

export type SettingsResponse = {
  download_dir: string
  default_container: ContainerFormat
  default_audio_format: AudioOutputFormat
  max_concurrent_downloads: number
}

export type MediaFormat = {
  format_id: string
  label: string
  ext: string | null
  codec: string | null
  quality: string | null
  resolution: string | null
  bitrate: number | null
  fps: number | null
  filesize: number | null
  requires_auth: boolean
}

export type PreviewResponse = {
  url: string
  title: string
  uploader: string | null
  duration: number | null
  thumbnail: string | null
  webpage_url: string | null
  videos: MediaFormat[]
  audios: MediaFormat[]
}

export type TaskResponse = {
  id: string
  url: string
  title: string | null
  status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'
  progress: number
  stage: string
  message: string | null
  output_dir: string
  output_path: string | null
  created_at: string
  updated_at: string
  completed_at: string | null
  error: string | null
  options: Record<string, unknown>
}

export type OpenFolderResponse = {
  opened: boolean
  path: string
}

export type ClearTasksResponse = {
  cleared: number
}

export type TaskCreatePayload = {
  url: string
  title?: string
  video_format_id?: string
  audio_format_id?: string
  audio_output_format?: AudioOutputFormat
  download_cover?: boolean
  thumbnail_url?: string
  merge: boolean
  container: ContainerFormat
  output_dir?: string
  custom_filename?: string
}

const jsonHeaders = { 'Content-Type': 'application/json' }

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init)
  const contentType = response.headers.get('content-type') || ''
  const payload = contentType.includes('application/json') ? await response.json() : await response.text()
  if (!response.ok) {
    const detail = typeof payload === 'object' && payload && 'detail' in payload ? payload.detail : payload
    throw new Error(String(detail || response.statusText))
  }
  return payload as T
}

export const api = {
  health: () => request<HealthResponse>('/api/health'),
  getCompliance: () => request<ComplianceStatus>('/api/compliance'),
  acceptCompliance: () =>
    request<ComplianceStatus>('/api/compliance', {
      method: 'POST',
      headers: jsonHeaders,
      body: JSON.stringify({ accepted: true })
    }),
  cookieStatus: () => request<CookieStatus>('/api/auth/bilibili-cookie'),
  saveCookie: (cookie: string) =>
    request<CookieStatus>('/api/auth/bilibili-cookie', {
      method: 'POST',
      headers: jsonHeaders,
      body: JSON.stringify({ cookie })
    }),
  deleteCookie: () =>
    request<CookieStatus>('/api/auth/bilibili-cookie', {
      method: 'DELETE'
    }),
  getSettings: () => request<SettingsResponse>('/api/settings'),
  updateSettings: (settings: Partial<SettingsResponse>) =>
    request<SettingsResponse>('/api/settings', {
      method: 'POST',
      headers: jsonHeaders,
      body: JSON.stringify(settings)
    }),
  preview: (url: string) =>
    request<PreviewResponse>('/api/preview', {
      method: 'POST',
      headers: jsonHeaders,
      body: JSON.stringify({ url })
    }),
  listTasks: () => request<TaskResponse[]>('/api/tasks'),
  clearTasks: () =>
    request<ClearTasksResponse>('/api/tasks', {
      method: 'DELETE'
    }),
  createTask: (payload: TaskCreatePayload) =>
    request<TaskResponse>('/api/tasks', {
      method: 'POST',
      headers: jsonHeaders,
      body: JSON.stringify(payload)
    }),
  cancelTask: (taskId: string) =>
    request<TaskResponse>(`/api/tasks/${taskId}/cancel`, {
      method: 'POST'
    }),
  retryTask: (taskId: string) =>
    request<TaskResponse>(`/api/tasks/${taskId}/retry`, {
      method: 'POST'
    }),
  openTaskFolder: (taskId: string) =>
    request<OpenFolderResponse>(`/api/tasks/${taskId}/open-folder`, {
      method: 'POST'
    })
}

export function taskSocketUrl(taskId: string) {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.host}/ws/tasks/${taskId}`
}

export function thumbnailProxyUrl(url: string) {
  return `/api/media/thumbnail?url=${encodeURIComponent(url)}`
}
