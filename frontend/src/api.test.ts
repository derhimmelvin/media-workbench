import { afterEach, describe, expect, it, vi } from 'vitest'
import { taskSocketUrl, thumbnailProxyUrl } from './api'

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
