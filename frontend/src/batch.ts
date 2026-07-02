import type { AudioOutputFormat, ContainerFormat, PreviewResponse, SettingsResponse, TaskCreatePayload } from './api'

export const BATCH_LIMIT = 50

const BILIBILI_INPUT_PATTERN =
  /https?:\/\/(?:[^\s"'<>，,]*\.)?(?:bilibili\.com|b23\.tv)[^\s"'<>，,]*|(?:BV[0-9A-Za-z]{6,}|av\d+|ep\d+|ss\d+)/gi

export type PreparedBatchInputs = {
  items: string[]
  skippedDuplicates: number
  overflow: number
}

export type BatchTaskOptions = {
  includeVideo: boolean
  videoFormatId: string
  includeAudio: boolean
  audioFormatId: string
  audioOutputFormat: AudioOutputFormat
  includeCover: boolean
  container: ContainerFormat
  customFilename: string
}

function cleanBatchToken(value: string) {
  return value.replace(/^[("'[{<]+/g, '').replace(/[)"'\]}>，,。；;]+$/g, '').trim()
}

export function extractBatchInputs(text: string) {
  return Array.from(text.matchAll(BILIBILI_INPUT_PATTERN), (match) => cleanBatchToken(match[0])).filter(Boolean)
}

export function prepareBatchInputs(text: string, limit = BATCH_LIMIT): PreparedBatchInputs {
  const seen = new Set<string>()
  const items: string[] = []
  let skippedDuplicates = 0
  let overflow = 0

  for (const input of extractBatchInputs(text)) {
    const key = input.toLowerCase()
    if (seen.has(key)) {
      skippedDuplicates += 1
      continue
    }
    seen.add(key)
    if (items.length >= limit) {
      overflow += 1
      continue
    }
    items.push(input)
  }

  return { items, skippedDuplicates, overflow }
}

export function canBuildDefaultBatchTask(preview: PreviewResponse) {
  return Boolean(preview.audios[0])
}

export function buildDefaultBatchTaskOptions(
  preview: PreviewResponse,
  container: ContainerFormat,
  audioOutputFormat: AudioOutputFormat
): BatchTaskOptions {
  const videoFormatId = preview.videos[0]?.format_id || ''
  const audioFormatId = preview.audios[0]?.format_id || ''
  const includeVideo = Boolean(videoFormatId)
  const includeAudio = Boolean(audioFormatId)

  return {
    includeVideo,
    videoFormatId,
    includeAudio,
    audioFormatId,
    audioOutputFormat,
    includeCover: false,
    container: includeVideo ? container : 'mp4',
    customFilename: ''
  }
}

export function canSubmitBatchTask(preview: PreviewResponse, options: BatchTaskOptions) {
  const wantsVideo = options.includeVideo && Boolean(options.videoFormatId)
  const wantsAudio = options.includeAudio && Boolean(options.audioFormatId)
  const wantsCover = options.includeCover && Boolean(preview.thumbnail)

  return (!wantsVideo || wantsAudio) && (wantsVideo || wantsAudio || wantsCover)
}

export function buildBatchTaskPayload(
  preview: PreviewResponse,
  settings: SettingsResponse,
  options: BatchTaskOptions
): TaskCreatePayload {
  const wantsVideo = options.includeVideo && Boolean(options.videoFormatId)
  const wantsAudio = options.includeAudio && Boolean(options.audioFormatId)
  const wantsCover = options.includeCover && Boolean(preview.thumbnail)

  return {
    url: preview.url,
    title: preview.title,
    video_format_id: wantsVideo ? options.videoFormatId : undefined,
    audio_format_id: wantsAudio ? options.audioFormatId : undefined,
    audio_output_format: options.audioOutputFormat,
    download_cover: wantsCover,
    thumbnail_url: preview.thumbnail || undefined,
    merge: wantsVideo && wantsAudio,
    container: wantsVideo ? options.container : 'mp4',
    output_dir: settings.download_dir,
    custom_filename: options.customFilename.trim() || undefined
  }
}
