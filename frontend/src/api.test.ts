import { afterEach, describe, expect, it, vi } from 'vitest'
import { taskSocketUrl, thumbnailProxyUrl } from './api'
import { buildBatchTaskPayload, buildDefaultBatchTaskOptions, canSubmitBatchTask, prepareBatchInputs } from './batch'
import type { PreviewResponse, SettingsResponse } from './api'

describe('taskSocketUrl', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('builds a websocket URL for a task', () => {
    vi.stubGlobal('window', {
      location: {
        protocol: 'http:',
        host: '127.0.0.1:5173'
      }
    })

    expect(taskSocketUrl('task-1')).toBe('ws://127.0.0.1:5173/ws/tasks/task-1')
  })

  it('builds a proxied thumbnail URL', () => {
    expect(thumbnailProxyUrl('http://i0.hdslb.com/bfs/archive/cover.jpg')).toBe(
      '/api/media/thumbnail?url=http%3A%2F%2Fi0.hdslb.com%2Fbfs%2Farchive%2Fcover.jpg'
    )
  })
})

describe('prepareBatchInputs', () => {
  it('extracts supported Bilibili inputs and skips raw duplicates', () => {
    const result = prepareBatchInputs(`
      https://www.bilibili.com/video/BV1aY4y1D7XG/?spm_id_from=abc
      BV1aY4y1D7XG
      av12345, ep67890, ss24680
      av12345
    `)

    expect(result.items).toEqual([
      'https://www.bilibili.com/video/BV1aY4y1D7XG/?spm_id_from=abc',
      'BV1aY4y1D7XG',
      'av12345',
      'ep67890',
      'ss24680'
    ])
    expect(result.skippedDuplicates).toBe(1)
  })

  it('enforces the batch limit after de-duplication', () => {
    const text = Array.from({ length: 55 }, (_, index) => `BV${String(index).padStart(10, '0')}`).join('\n')
    const result = prepareBatchInputs(text)

    expect(result.items).toHaveLength(50)
    expect(result.overflow).toBe(5)
  })
})

describe('batch task options', () => {
  function previewFixture(): PreviewResponse {
    return {
      url: 'https://www.bilibili.com/video/BV1xx',
      title: 'Example',
      uploader: 'UP',
      duration: 120,
      thumbnail: 'https://i0.hdslb.com/bfs/archive/cover.jpg',
      webpage_url: 'https://www.bilibili.com/video/BV1xx',
      videos: [
        {
          format_id: 'video-1080',
          label: '1080P',
          ext: 'mp4',
          codec: 'avc1',
          quality: '1080P',
          resolution: '1920x1080',
          bitrate: 2000,
          fps: 30,
          filesize: null,
          requires_auth: false
        }
      ],
      audios: [
        {
          format_id: 'audio-30280',
          label: '192K',
          ext: 'm4a',
          codec: 'mp4a',
          quality: '192K',
          resolution: null,
          bitrate: 192,
          fps: null,
          filesize: null,
          requires_auth: false
        }
      ],
      subtitles: [
        {
          id: 'normal:zh-Hans',
          language: 'zh-Hans',
          label: '中文（简体）',
          source: 'normal',
          formats: ['srt']
        }
      ]
    }
  }

  it('initializes per-row defaults from preview data', () => {
    const settings: SettingsResponse = {
      download_dir: '/tmp/downloads',
      default_container: 'mkv',
      default_audio_format: 'mp3',
      max_concurrent_downloads: 1
    }
    const preview = previewFixture()

    expect(buildDefaultBatchTaskOptions(preview, settings.default_container, settings.default_audio_format)).toMatchObject({
      includeVideo: true,
      videoFormatId: 'video-1080',
      includeAudio: true,
      audioFormatId: 'audio-30280',
      audioOutputFormat: 'mp3',
      includeCover: false,
      includeSubtitles: false,
      subtitleTrackIds: ['normal:zh-Hans'],
      subtitleFormat: 'srt',
      container: 'mkv',
      customFilename: ''
    })
  })

  it('builds payload from a single row override', () => {
    const settings: SettingsResponse = {
      download_dir: '/tmp/downloads',
      default_container: 'mkv',
      default_audio_format: 'm4a',
      max_concurrent_downloads: 1
    }
    const preview = previewFixture()
    const options = {
      ...buildDefaultBatchTaskOptions(preview, 'mp4', 'm4a'),
      includeVideo: false,
      videoFormatId: '',
      includeCover: true,
      customFilename: '单行文件名'
    }

    expect(canSubmitBatchTask(preview, options)).toBe(true)
    expect(buildBatchTaskPayload(preview, settings, options)).toMatchObject({
      url: preview.url,
      title: 'Example',
      video_format_id: undefined,
      audio_format_id: 'audio-30280',
      audio_output_format: 'm4a',
      download_cover: true,
      thumbnail_url: 'https://i0.hdslb.com/bfs/archive/cover.jpg',
      subtitle_track_ids: [],
      subtitle_format: 'srt',
      merge: false,
      container: 'mp4',
      output_dir: '/tmp/downloads',
      custom_filename: '单行文件名'
    })
  })

  it('keeps the thumbnail URL in payload even when cover is not selected', () => {
    const settings: SettingsResponse = {
      download_dir: '/tmp/downloads',
      default_container: 'mkv',
      default_audio_format: 'm4a',
      max_concurrent_downloads: 1
    }
    const preview = previewFixture()
    const options = {
      ...buildDefaultBatchTaskOptions(preview, 'mkv', 'm4a'),
      includeCover: false
    }

    expect(buildBatchTaskPayload(preview, settings, options)).toMatchObject({
      download_cover: false,
      thumbnail_url: 'https://i0.hdslb.com/bfs/archive/cover.jpg'
    })
  })

  it('always merges video and audio rows', () => {
    const settings: SettingsResponse = {
      download_dir: '/tmp/downloads',
      default_container: 'mkv',
      default_audio_format: 'm4a',
      max_concurrent_downloads: 1
    }
    const preview = previewFixture()
    const options = buildDefaultBatchTaskOptions(preview, 'mkv', 'm4a')

    expect(canSubmitBatchTask(preview, options)).toBe(true)
    expect(buildBatchTaskPayload(preview, settings, options)).toMatchObject({
      video_format_id: 'video-1080',
      audio_format_id: 'audio-30280',
      merge: true,
      container: 'mkv'
    })
  })

  it('rejects video-only row options before submit', () => {
    const preview = previewFixture()
    const options = {
      ...buildDefaultBatchTaskOptions(preview, 'mp4', 'm4a'),
      includeAudio: false,
      audioFormatId: ''
    }

    expect(canSubmitBatchTask(preview, options)).toBe(false)
  })

  it('builds payload for subtitle-only rows', () => {
    const settings: SettingsResponse = {
      download_dir: '/tmp/downloads',
      default_container: 'mkv',
      default_audio_format: 'm4a',
      max_concurrent_downloads: 1
    }
    const preview = previewFixture()
    const options = {
      ...buildDefaultBatchTaskOptions(preview, 'mkv', 'm4a'),
      includeVideo: false,
      videoFormatId: '',
      includeAudio: false,
      audioFormatId: '',
      includeSubtitles: true,
      subtitleTrackIds: ['normal:zh-Hans'],
      subtitleFormat: 'txt' as const
    }

    expect(canSubmitBatchTask(preview, options)).toBe(true)
    expect(buildBatchTaskPayload(preview, settings, options)).toMatchObject({
      video_format_id: undefined,
      audio_format_id: undefined,
      download_cover: false,
      subtitle_track_ids: ['normal:zh-Hans'],
      subtitle_format: 'txt',
      merge: false,
      container: 'mp4'
    })
  })
})
