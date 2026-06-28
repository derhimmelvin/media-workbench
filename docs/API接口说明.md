# API接口说明

本文描述当前 v1 后端 API。后端默认运行在：

```text
http://127.0.0.1:8000
```

前端开发服务通过 Vite 将 `/api` 和 `/ws` 代理到后端。

## 1. 通用约定

- 请求和响应默认使用 JSON。
- 错误响应使用 FastAPI 默认格式：`{"detail": "..."}`
- 当前只支持 B站链接、BV号、av号、ep号、ss号。
- 任务状态通过 HTTP 查询和 WebSocket 推送两种方式获得。

## 2. 健康检查

### `GET /api/health`

返回运行依赖状态。

响应示例：

```json
{
  "ok": true,
  "database": true,
  "ffmpeg": true,
  "keyring": true,
  "versions": {
    "python": "3.11.x",
    "yt_dlp": "2026.x"
  },
  "messages": []
}
```

字段说明：

- `database`：SQLite 是否可用。
- `ffmpeg`：FFmpeg 是否可用。
- `keyring`：系统钥匙串是否可用。
- `versions`：运行时版本信息。
- `messages`：不可用依赖或提示信息。

## 3. 合规声明

### `GET /api/compliance`

读取当前合规确认状态。

响应示例：

```json
{
  "accepted": false,
  "version": "v1",
  "accepted_at": null,
  "statement": "本工具仅供学习、研究和个人合理使用..."
}
```

### `POST /api/compliance`

确认合规声明。

请求示例：

```json
{
  "accepted": true
}
```

## 4. Cookie

### `GET /api/auth/bilibili-cookie`

读取 B站 Cookie 配置状态。

响应示例：

```json
{
  "configured": true,
  "masked": "SESSDATA=...; bili_jct=...",
  "keyring_available": true,
  "message": null
}
```

### `POST /api/auth/bilibili-cookie`

保存 B站 Cookie。

请求示例：

```json
{
  "cookie": "SESSDATA=...; bili_jct=..."
}
```

安全约束：

- Cookie 明文只保存到系统钥匙串。
- SQLite 只保存是否配置和脱敏摘要。
- 钥匙串不可用时返回 503，不允许明文落盘。

### `DELETE /api/auth/bilibili-cookie`

删除已保存的 B站 Cookie。

## 5. 设置

### `GET /api/settings`

读取设置。

响应示例：

```json
{
  "download_dir": "/Users/yifan/Desktop/projects/mvp/downloads",
  "default_container": "mp4",
  "max_concurrent_downloads": 1
}
```

### `POST /api/settings`

更新设置。

请求示例：

```json
{
  "download_dir": "/Users/yifan/Desktop/projects/mvp/downloads",
  "default_container": "mkv",
  "max_concurrent_downloads": 1
}
```

当前约束：

- `default_container` 只能是 `mp4` 或 `mkv`。
- `max_concurrent_downloads` 允许保存，但当前执行器仍串行执行任务。

## 6. 资源预览

### `POST /api/preview`

解析链接并返回资源列表。

请求示例：

```json
{
  "url": "https://www.bilibili.com/video/BV..."
}
```

响应示例：

```json
{
  "url": "https://www.bilibili.com/video/BV...",
  "title": "视频标题",
  "uploader": "UP主",
  "duration": 172,
  "thumbnail": "https://...",
  "webpage_url": "https://www.bilibili.com/video/BV...",
  "videos": [
    {
      "format_id": "80",
      "label": "1080P · avc1 mp4",
      "ext": "mp4",
      "codec": "avc1",
      "quality": "1080P",
      "resolution": "1920x1080",
      "bitrate": null,
      "fps": 30,
      "filesize": null,
      "requires_auth": false
    }
  ],
  "audios": [
    {
      "format_id": "30280",
      "label": "192K · m4a",
      "ext": "m4a",
      "codec": "mp4a",
      "quality": null,
      "resolution": null,
      "bitrate": 192,
      "fps": null,
      "filesize": 3456789,
      "requires_auth": false
    }
  ]
}
```

## 7. 封面代理

### `GET /api/media/thumbnail?url=...`

代理读取封面图片，解决前端直接加载 B站封面时可能出现的访问或防盗链问题。

响应：

- Body：图片二进制内容。
- Header：`Cache-Control: public, max-age=86400`
- Header：`X-Thumbnail-Source: 原始封面地址`

## 8. 任务

### `POST /api/tasks`

创建下载任务。

请求示例：下载视频和音频并合并为 MP4。

```json
{
  "url": "https://www.bilibili.com/video/BV...",
  "title": "视频标题",
  "video_format_id": "80",
  "audio_format_id": "30280",
  "audio_output_format": "m4a",
  "download_cover": false,
  "thumbnail_url": "https://...",
  "merge": true,
  "container": "mp4",
  "output_dir": null
}
```

请求示例：仅下载 MP3 音频。

```json
{
  "url": "https://www.bilibili.com/video/BV...",
  "title": "视频标题",
  "audio_format_id": "30280",
  "audio_output_format": "mp3",
  "download_cover": false,
  "merge": false,
  "container": "mp4"
}
```

请求示例：仅下载封面。

```json
{
  "url": "https://www.bilibili.com/video/BV...",
  "title": "视频标题",
  "download_cover": true,
  "thumbnail_url": "https://...",
  "merge": false,
  "container": "mp4"
}
```

后端校验：

- 未同意合规声明时返回 403。
- 未选择视频、音频、封面任一资源时返回 400。
- 选择视频但没有选择音频时返回 400。
- 选择封面但没有 `thumbnail_url` 时返回 400。

任务响应示例：

```json
{
  "id": "uuid",
  "url": "https://www.bilibili.com/video/BV...",
  "title": "视频标题",
  "status": "queued",
  "progress": 0,
  "stage": "queued",
  "message": "等待下载",
  "output_dir": "/Users/yifan/Desktop/projects/mvp/downloads",
  "output_path": null,
  "created_at": "2026-06-28T10:00:00+08:00",
  "updated_at": "2026-06-28T10:00:00+08:00",
  "completed_at": null,
  "error": null,
  "options": {
    "video_format_id": "80",
    "audio_format_id": "30280",
    "audio_output_format": "m4a",
    "download_cover": false,
    "thumbnail_url": "https://...",
    "merge": true,
    "container": "mp4"
  }
}
```

### `GET /api/tasks`

返回最近 100 条任务，按创建时间倒序。

### `DELETE /api/tasks`

清空已结束任务记录。

响应示例：

```json
{
  "cleared": 2
}
```

说明：

- 只删除 `completed`、`failed`、`cancelled` 状态的任务。
- 不删除本地下载文件。
- 不影响正在运行或等待中的任务。

### `GET /api/tasks/{task_id}`

读取单个任务。

### `POST /api/tasks/{task_id}/cancel`

取消任务。

### `POST /api/tasks/{task_id}/retry`

重试失败或取消的任务。

### `POST /api/tasks/{task_id}/open-folder`

打开已完成任务的输出目录。

约束：

- 只有 `completed` 状态任务允许打开目录。
- 如果目录不存在，返回 404。
- 如果系统无法打开目录，返回 503。

响应示例：

```json
{
  "opened": true,
  "path": "/Users/yifan/Desktop/projects/mvp/downloads"
}
```

## 9. WebSocket

### `WS /ws/tasks/{task_id}`

连接后，后端会先推送当前任务快照。任务状态变化时继续推送完整 `TaskResponse`。

阶段字段：

- `queued`：等待中。
- `preparing`：准备下载。
- `downloading`：下载中。
- `merging`：下载完成，正在处理或合并。
- `completed`：完成。
- `failed`：失败。
- `cancelled`：已取消。

状态字段：

- `queued`
- `running`
- `completed`
- `failed`
- `cancelled`

## 10. 数据表

当前 SQLite 关键表：

- `settings`：下载目录、默认容器、最大并发预留字段、Cookie 脱敏摘要等设置。
- `compliance_consent`：合规确认状态。
- `tasks`：任务主体数据。
- `task_events`：任务状态事件历史。

