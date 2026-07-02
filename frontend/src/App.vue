<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  CheckCircle2,
  CircleX,
  Download,
  FileImage,
  FolderCog,
  FolderOpen,
  KeyRound,
  Music2,
  Play,
  RefreshCw,
  RotateCcw,
  Search,
  Settings,
  ShieldCheck,
  Trash2,
  Video
} from '@lucide/vue'
import { api, taskSocketUrl, thumbnailProxyUrl, type AudioOutputFormat, type ContainerFormat, type CookieStatus, type HealthResponse, type MediaFormat, type PreviewResponse, type SettingsResponse, type TaskCreatePayload, type TaskResponse } from './api'
import { BATCH_LIMIT, buildBatchTaskPayload, buildDefaultBatchTaskOptions, canBuildDefaultBatchTask, canSubmitBatchTask, prepareBatchInputs, type BatchTaskOptions } from './batch'

const FALLBACK_COMPLIANCE_STATEMENT = '本工具仅供学习、研究和个人合理使用，严禁用于侵犯版权或商业传播，用户须自行承担法律责任。'

type WorkMode = 'single' | 'batch'
type BatchRowStatus = 'pending' | 'parsing' | 'ready' | 'duplicate' | 'failed' | 'submitted'

type BatchRow = {
  id: string
  input: string
  status: BatchRowStatus
  message: string
  preview: PreviewResponse | null
  options: BatchTaskOptions | null
  expanded: boolean
  thumbnailFailed: boolean
}

const health = ref<HealthResponse | null>(null)
const compliance = ref({ accepted: false, statement: FALLBACK_COMPLIANCE_STATEMENT, version: '', accepted_at: null as string | null })
const cookie = ref<CookieStatus | null>(null)
const settings = ref<SettingsResponse>({
  download_dir: '',
  default_container: 'mp4',
  default_audio_format: 'm4a',
  max_concurrent_downloads: 1
})
const tasks = ref<TaskResponse[]>([])
const preview = ref<PreviewResponse | null>(null)
const workMode = ref<WorkMode>('single')
const url = ref('')
const videoFormatId = ref('')
const audioFormatId = ref('')
const audioOutputFormat = ref<AudioOutputFormat>('m4a')
const includeVideo = ref(false)
const includeAudio = ref(false)
const includeCover = ref(false)
const singleCustomFilename = ref('')
const batchText = ref('')
const batchRows = ref<BatchRow[]>([])
const parsingBatch = ref(false)
const submittingBatch = ref(false)
const busy = ref(false)
const creatingTask = ref(false)
const clearingTasks = ref(false)
const acceptingCompliance = ref(false)
const cookieDrawer = ref(false)
const settingsDrawer = ref(false)
const cookieInput = ref('')
const coverFailed = ref(false)
const taskThumbnailFailures = ref<Record<string, boolean>>({})
const complianceLoaded = ref(false)
const complianceAcceptedInPage = ref(false)
const sockets = new Map<string, WebSocket>()
const containerOptions: ContainerFormat[] = ['mp4', 'mkv']
const audioOutputOptions: AudioOutputFormat[] = ['m4a', 'mp3']
const modeOptions = [
  { label: '单链接', value: 'single' },
  { label: '批量', value: 'batch' }
]

const wantsVideo = computed(() => includeVideo.value && Boolean(videoFormatId.value))
const wantsAudio = computed(() => includeAudio.value && Boolean(audioFormatId.value))
const wantsCover = computed(() => includeCover.value && Boolean(preview.value?.thumbnail))
const hasRequiredAudioForVideo = computed(() => !wantsVideo.value || wantsAudio.value)
const finishedTaskCount = computed(() => tasks.value.filter((task) => ['completed', 'failed', 'cancelled'].includes(task.status)).length)
const complianceDialogVisible = computed(() => complianceLoaded.value && !complianceAcceptedInPage.value)
const batchActive = computed(() => parsingBatch.value || submittingBatch.value)
const batchSubmittableRows = computed(() => batchRows.value.filter((row) => canSubmitBatchRow(row)))
const canSubmitBatch = computed(() => complianceAcceptedInPage.value && batchSubmittableRows.value.length > 0 && !batchActive.value)
const batchSummary = computed(() => {
  const counts = batchRows.value.reduce<Record<BatchRowStatus, number>>(
    (result, row) => {
      result[row.status] += 1
      return result
    },
    { pending: 0, parsing: 0, ready: 0, duplicate: 0, failed: 0, submitted: 0 }
  )
  return `可提交 ${batchSubmittableRows.value.length} / 失败 ${counts.failed} / 重复 ${counts.duplicate} / 已提交 ${counts.submitted}`
})
const canSubmit = computed(() => {
  return workMode.value === 'single' && complianceAcceptedInPage.value && preview.value && hasRequiredAudioForVideo.value && (wantsVideo.value || wantsAudio.value || wantsCover.value) && !creatingTask.value
})
const singleVideoQualityHint = computed(() => videoQualityHint(preview.value))

function healthClass(flag?: boolean) {
  return flag ? 'ok' : 'warn'
}

function statusType(status: TaskResponse['status']) {
  if (status === 'completed') return 'success'
  if (status === 'failed') return 'danger'
  if (status === 'cancelled') return 'info'
  if (status === 'running') return 'warning'
  return ''
}

function stageLabel(stage: string) {
  const labels: Record<string, string> = {
    queued: '排队中',
    preparing: '准备中',
    downloading: '下载中',
    merging: '合并中',
    completed: '已完成',
    failed: '失败',
    cancelled: '已取消'
  }
  return labels[stage] || stage
}

function batchStatusType(status: BatchRowStatus) {
  if (status === 'ready' || status === 'submitted') return 'success'
  if (status === 'failed') return 'danger'
  if (status === 'duplicate') return 'info'
  if (status === 'parsing') return 'warning'
  return ''
}

function batchStatusLabel(status: BatchRowStatus) {
  const labels: Record<BatchRowStatus, string> = {
    pending: '待解析',
    parsing: '解析中',
    ready: '可提交',
    duplicate: '重复',
    failed: '失败',
    submitted: '已提交'
  }
  return labels[status]
}

function formatSize(value: number | null) {
  if (!value) return '未知'
  const units = ['B', 'KB', 'MB', 'GB']
  let size = value
  let index = 0
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024
    index += 1
  }
  return `${size.toFixed(index === 0 ? 0 : 1)} ${units[index]}`
}

function formatDuration(value: number | null) {
  if (!value) return '未知'
  const totalSeconds = Math.floor(value)
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return `${minutes}:${String(seconds).padStart(2, '0')}`
}

function formatTimestamp(value: string | null) {
  if (!value) return '未记录'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false
  }).format(date)
}

function formatRow(item: MediaFormat) {
  const details = [item.ext, item.resolution, item.bitrate ? `${Math.round(item.bitrate)}K` : '', formatSize(item.filesize)]
  return `${item.label} ${details.filter(Boolean).join(' / ')}`
}

function mediaHeight(item: MediaFormat) {
  for (const source of [item.resolution, item.quality, item.label]) {
    if (!source) continue
    const resolutionMatch = source.match(/[xX]\s*(\d{3,4})/)
    if (resolutionMatch) return Number(resolutionMatch[1])
    const qualityMatch = source.match(/(^|\D)(\d{3,4})\s*[pP]/)
    if (qualityMatch) return Number(qualityMatch[2])
  }
  return 0
}

function maxVideoHeight(items: MediaFormat[]) {
  return items.reduce((height, item) => Math.max(height, mediaHeight(item)), 0)
}

function videoQualityHint(target: PreviewResponse | null) {
  if (!target?.videos.length) return ''
  const height = maxVideoHeight(target.videos)
  if (!height || height >= 720) return ''
  if (cookie.value?.configured) {
    return '当前只解析到低清晰度。请确认 Cookie 未过期，或该资源/账号权限可能限制高清。'
  }
  return '当前只解析到低清晰度。导入有效 B站 Cookie 后重新解析，可能显示 720P/1080P。'
}

function batchRowTitle(row: BatchRow) {
  return row.preview?.title || row.input
}

function batchWantsVideo(row: BatchRow) {
  return Boolean(row.options?.includeVideo && row.options.videoFormatId)
}

function batchWantsAudio(row: BatchRow) {
  return Boolean(row.options?.includeAudio && row.options.audioFormatId)
}

function batchWantsCover(row: BatchRow) {
  return Boolean(row.options?.includeCover && row.preview?.thumbnail)
}

function batchRowLocked(row: BatchRow) {
  return batchActive.value || row.status === 'submitted'
}

function batchRowEditable(row: BatchRow) {
  return Boolean(row.preview && row.options && (row.status === 'ready' || row.status === 'submitted'))
}

function canSubmitBatchRow(row: BatchRow) {
  return Boolean(row.status === 'ready' && row.preview && row.options && canSubmitBatchTask(row.preview, row.options))
}

function batchRowSummary(row: BatchRow) {
  if (!row.preview || !row.options || (row.status !== 'ready' && row.status !== 'submitted')) return row.message
  const resources = []
  if (batchWantsVideo(row)) resources.push('视频+音频')
  if (!batchWantsVideo(row) && batchWantsAudio(row)) resources.push('音频')
  if (batchWantsCover(row)) resources.push('封面')
  const filename = row.options.customFilename.trim()
  const parts = [resources.length ? resources.join(' / ') : '未选择资源']
  if (filename) parts.push(`文件名：${filename}`)
  return parts.join(' · ')
}

function coverSrc(thumbnail: string | null) {
  return thumbnail ? thumbnailProxyUrl(thumbnail) : ''
}

function pageUrl(target: PreviewResponse | null) {
  return target?.webpage_url || target?.url || ''
}

function batchRowPageUrl(row: BatchRow) {
  return pageUrl(row.preview)
}

function taskPageUrl(task: TaskResponse) {
  return task.url
}

function taskThumbnail(task: TaskResponse) {
  const thumbnail = task.options.thumbnail_url
  return typeof thumbnail === 'string' && thumbnail ? thumbnail : ''
}

function taskThumbnailSrc(task: TaskResponse) {
  const thumbnail = taskThumbnail(task)
  return thumbnail && !taskThumbnailFailures.value[task.id] ? thumbnailProxyUrl(thumbnail) : ''
}

function ensureAudioForVideo(showMessage = false) {
  if (!includeVideo.value || !preview.value?.audios.length) return
  if (!audioFormatId.value) {
    audioFormatId.value = preview.value.audios[0]?.format_id || ''
  }
  if (!includeAudio.value) {
    includeAudio.value = true
    if (showMessage) {
      ElMessage.info('下载视频需要音频流，已自动选择音频。')
    }
  }
}

function ensureBatchAudioForVideo(row: BatchRow, showMessage = false) {
  if (!row.options?.includeVideo || !row.preview?.audios.length) return
  if (!row.options.audioFormatId) {
    row.options.audioFormatId = row.preview.audios[0]?.format_id || ''
  }
  if (!row.options.includeAudio) {
    row.options.includeAudio = true
    if (showMessage) {
      ElMessage.info('下载视频需要音频流，已自动选择音频。')
    }
  }
}

function handleBatchVideoChange(row: BatchRow) {
  if (!row.options) return
  if (row.options.includeVideo) {
    if (!row.options.videoFormatId) {
      row.options.videoFormatId = row.preview?.videos[0]?.format_id || ''
    }
    ensureBatchAudioForVideo(row, true)
    return
  }
}

function handleBatchAudioChange(row: BatchRow) {
  if (!row.options) return
  if (!row.options.includeAudio && batchWantsVideo(row) && row.preview?.audios.length) {
    row.options.includeAudio = true
    row.options.audioFormatId = row.options.audioFormatId || row.preview.audios[0]?.format_id || ''
    ElMessage.warning('下载视频需要音频流，否则文件会没有声音。已保留音频并合并。')
  }
}

function handleBatchVideoFormatChange(row: BatchRow) {
  if (!row.options?.includeVideo) return
  if (!row.options.videoFormatId) {
    row.options.videoFormatId = row.preview?.videos[0]?.format_id || ''
  }
  ensureBatchAudioForVideo(row)
}

function handleBatchAudioFormatChange(row: BatchRow) {
  if (!row.options) return
  if (!row.options.audioFormatId && batchWantsVideo(row) && row.preview?.audios.length) {
    row.options.audioFormatId = row.preview.audios[0]?.format_id || ''
    row.options.includeAudio = true
    ElMessage.warning('下载视频需要音频流，已恢复默认音频。')
  }
}

async function loadBase() {
  await loadComplianceStatus()
  const [healthData, cookieData, settingsData, taskData] = await Promise.all([api.health(), api.cookieStatus(), api.getSettings(), api.listTasks()])
  health.value = healthData
  cookie.value = cookieData
  settings.value = settingsData
  tasks.value = taskData
}

async function loadComplianceStatus() {
  try {
    const complianceData = await api.getCompliance()
    compliance.value = complianceData
    complianceAcceptedInPage.value = complianceData.accepted
  } catch (error) {
    compliance.value = {
      ...compliance.value,
      accepted: false,
      statement: compliance.value.statement || FALLBACK_COMPLIANCE_STATEMENT
    }
    complianceAcceptedInPage.value = false
    throw error
  } finally {
    complianceLoaded.value = true
  }
}

async function acceptCompliance() {
  acceptingCompliance.value = true
  try {
    compliance.value = await api.acceptCompliance()
    complianceAcceptedInPage.value = compliance.value.accepted
    ElMessage.success('已确认')
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '确认失败')
  } finally {
    acceptingCompliance.value = false
  }
}

async function parseUrl() {
  if (!url.value.trim()) {
    ElMessage.warning('请输入链接')
    return
  }
  busy.value = true
  try {
    preview.value = await api.preview(url.value.trim())
    coverFailed.value = false
    videoFormatId.value = preview.value.videos[0]?.format_id || ''
    audioFormatId.value = preview.value.audios[0]?.format_id || ''
    audioOutputFormat.value = settings.value.default_audio_format
    includeVideo.value = Boolean(videoFormatId.value)
    includeAudio.value = Boolean(audioFormatId.value)
    includeCover.value = false
    ElMessage.success('解析完成')
  } catch (error) {
    preview.value = null
    coverFailed.value = false
    audioOutputFormat.value = settings.value.default_audio_format
    includeVideo.value = false
    includeAudio.value = false
    includeCover.value = false
    ElMessage.error(error instanceof Error ? error.message : '解析失败')
  } finally {
    busy.value = false
  }
}

async function saveCookie() {
  try {
    cookie.value = await api.saveCookie(cookieInput.value)
    cookieInput.value = ''
    cookieDrawer.value = false
    ElMessage.success('Cookie 已保存')
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '保存失败')
  }
}

async function deleteCookie() {
  try {
    cookie.value = await api.deleteCookie()
    ElMessage.success('Cookie 已删除')
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '删除失败')
  }
}

async function saveSettings() {
  try {
    settings.value = await api.updateSettings(settings.value)
    settingsDrawer.value = false
    ElMessage.success('设置已保存')
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '保存失败')
  }
}

async function createTask() {
  if (!canSubmit.value || !preview.value) return
  if (wantsVideo.value && !wantsAudio.value) {
    ElMessage.warning('下载视频需要同时选择音频流。')
    return
  }
  creatingTask.value = true
  const videoFormat = wantsVideo.value ? videoFormatId.value : undefined
  const audioFormat = wantsAudio.value ? audioFormatId.value : undefined
  const downloadCover = wantsCover.value
  try {
    const payload: TaskCreatePayload = {
      url: preview.value.url,
      title: preview.value.title,
      video_format_id: videoFormat,
      audio_format_id: audioFormat,
      audio_output_format: audioOutputFormat.value,
      download_cover: downloadCover,
      thumbnail_url: preview.value.thumbnail || undefined,
      merge: Boolean(videoFormat && audioFormat),
      container: videoFormat ? settings.value.default_container : 'mp4',
      output_dir: settings.value.download_dir,
      custom_filename: singleCustomFilename.value.trim() || undefined
    }
    const task = await api.createTask(payload)
    upsertTask(task)
    attachSocket(task.id)
    ElMessage.success('任务已提交')
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '提交失败')
  } finally {
    creatingTask.value = false
  }
}

function prepareBatchRows() {
  const result = prepareBatchInputs(batchText.value)
  if (!result.items.length) {
    ElMessage.warning('未识别到可解析的 B站链接或 ID')
    return false
  }
  const submittedRows = new Map(
    batchRows.value.filter((row) => row.status === 'submitted').map((row) => [row.input.trim().toLowerCase(), row])
  )
  batchRows.value = result.items.map((input, index) => {
    const submittedRow = submittedRows.get(input.trim().toLowerCase())
    if (submittedRow) return submittedRow
    return {
      id: `${Date.now()}-${index}-${input}`,
      input,
      status: 'pending',
      message: '等待解析',
      preview: null,
      options: null,
      expanded: false,
      thumbnailFailed: false
    }
  })
  const notes = []
  if (result.skippedDuplicates) notes.push(`跳过重复 ${result.skippedDuplicates} 条`)
  if (result.overflow) notes.push(`超出上限 ${result.overflow} 条`)
  if (notes.length) ElMessage.info(`已识别 ${result.items.length} 条，${notes.join('，')}`)
  return true
}

async function parseBatchRows() {
  if (!prepareBatchRows()) return
  parsingBatch.value = true
  const normalizedUrls = new Set<string>()
  for (const row of batchRows.value) {
    if (row.status === 'submitted') {
      if (row.preview) normalizedUrls.add(row.preview.url.toLowerCase())
      continue
    }
    row.status = 'parsing'
    row.message = '解析中'
    row.preview = null
    row.options = null
    row.expanded = false
    row.thumbnailFailed = false
    try {
      const data = await api.preview(row.input)
      const normalizedUrl = data.url.toLowerCase()
      if (normalizedUrls.has(normalizedUrl)) {
        row.status = 'duplicate'
        row.message = '规范化链接重复，已跳过'
        continue
      }
      normalizedUrls.add(normalizedUrl)
      if (!canBuildDefaultBatchTask(data)) {
        row.status = 'failed'
        row.message = '未解析到可下载音频流'
        continue
      }
      row.preview = data
      row.options = buildDefaultBatchTaskOptions(data, settings.value.default_container, settings.value.default_audio_format)
      row.thumbnailFailed = false
      row.status = 'ready'
      row.message = '已解析'
    } catch (error) {
      row.status = 'failed'
      row.message = error instanceof Error ? error.message : '解析失败'
    }
  }
  parsingBatch.value = false
}

function removeBatchRow(row: BatchRow) {
  batchRows.value = batchRows.value.filter((item) => item.id !== row.id)
}

function clearBatchRows() {
  batchRows.value = []
}

async function submitBatchTasks() {
  if (!canSubmitBatch.value) return
  submittingBatch.value = true
  let submitted = 0
  try {
    for (const row of batchRows.value) {
      if (row.status !== 'ready' || !row.preview) continue
      if (!canSubmitBatchRow(row) || !row.options) continue
      const payload = buildBatchTaskPayload(row.preview, settings.value, row.options)
      try {
        const task = await api.createTask(payload)
        upsertTask(task)
        attachSocket(task.id)
        submitted += 1
        row.status = 'submitted'
        row.message = '已提交'
      } catch (error) {
        row.status = 'failed'
        row.message = error instanceof Error ? error.message : '提交失败'
      }
    }
    ElMessage.success(`已提交 ${submitted} 个任务`)
  } finally {
    submittingBatch.value = false
  }
}

async function cancelTask(task: TaskResponse) {
  const updated = await api.cancelTask(task.id)
  upsertTask(updated)
}

async function retryTask(task: TaskResponse) {
  const updated = await api.retryTask(task.id)
  upsertTask(updated)
  attachSocket(updated.id)
}

async function openTaskFolder(task: TaskResponse) {
  try {
    const result = await api.openTaskFolder(task.id)
    ElMessage.success(`已打开目录：${result.path}`)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '打开目录失败')
  }
}

async function clearTaskList() {
  if (!finishedTaskCount.value) {
    ElMessage.info('没有可清除的已结束任务')
    return
  }
  try {
    await ElMessageBox.confirm('仅清除已完成、失败、已取消的任务记录；不会删除本地下载文件，也不会影响正在下载的任务。', '清空任务记录', {
      confirmButtonText: '清空',
      cancelButtonText: '取消',
      type: 'warning'
    })
  } catch {
    return
  }

  clearingTasks.value = true
  try {
    const result = await api.clearTasks()
    tasks.value = tasks.value.filter((task) => !['completed', 'failed', 'cancelled'].includes(task.status))
    ElMessage.success(`已清除 ${result.cleared} 条任务记录`)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '清除失败')
  } finally {
    clearingTasks.value = false
  }
}

function upsertTask(task: TaskResponse) {
  const index = tasks.value.findIndex((item) => item.id === task.id)
  if (index >= 0) {
    tasks.value[index] = task
    return
  }
  tasks.value.unshift(task)
}

function attachSocket(taskId: string) {
  if (sockets.has(taskId)) return
  const socket = new WebSocket(taskSocketUrl(taskId))
  sockets.set(taskId, socket)
  socket.onmessage = (event) => {
    const task = JSON.parse(event.data) as TaskResponse
    upsertTask(task)
    if (['completed', 'failed', 'cancelled'].includes(task.status)) {
      socket.close()
      sockets.delete(taskId)
    }
  }
  socket.onclose = () => sockets.delete(taskId)
}

onMounted(async () => {
  try {
    await loadBase()
    tasks.value.filter((task) => task.status === 'running' || task.status === 'queued').forEach((task) => attachSocket(task.id))
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '服务不可用')
  }
})

watch(includeVideo, (enabled) => {
  if (enabled) {
    ensureAudioForVideo(true)
  }
})

watch(includeAudio, (enabled) => {
  if (!enabled && wantsVideo.value && preview.value?.audios.length) {
    includeAudio.value = true
    ElMessage.warning('下载视频需要音频流，否则文件会没有声音。已保留音频并合并。')
  }
})

watch(audioFormatId, (formatId) => {
  if (!formatId && includeVideo.value && preview.value?.audios.length) {
    audioFormatId.value = preview.value.audios[0]?.format_id || ''
    ElMessage.warning('下载视频需要音频流，已恢复默认音频。')
  }
})
</script>

<template>
  <main class="app-shell">
    <section class="topbar">
      <div>
        <h1>B站下载器</h1>
        <p>本地实用增强 v2.1</p>
      </div>
      <div class="top-actions">
        <el-tooltip content="刷新状态">
          <el-button :icon="RefreshCw" circle @click="loadBase" />
        </el-tooltip>
        <el-tooltip content="Cookie">
          <el-button :icon="KeyRound" circle @click="cookieDrawer = true" />
        </el-tooltip>
        <el-tooltip content="设置">
          <el-button :icon="Settings" circle @click="settingsDrawer = true" />
        </el-tooltip>
      </div>
    </section>

    <section class="status-strip">
      <span :class="healthClass(health?.database)">SQLite</span>
      <span :class="healthClass(Boolean(health?.versions.yt_dlp))">yt-dlp</span>
      <span :class="healthClass(health?.ffmpeg)">FFmpeg</span>
      <span :class="healthClass(health?.keyring)">钥匙串</span>
      <span :class="healthClass(complianceAcceptedInPage)">合规确认</span>
      <span :class="healthClass(cookie?.configured)">Cookie</span>
    </section>

    <section v-if="health?.messages.length" class="notice-band">
      <CircleX :size="18" />
      <span>{{ health.messages.join(' ') }}</span>
    </section>

    <section class="workspace">
      <div class="panel parser-panel">
        <div class="panel-title">
          <span>链接解析</span>
          <el-segmented v-model="workMode" :options="modeOptions" />
        </div>

        <template v-if="workMode === 'single'">
          <div class="url-row">
            <el-input
              v-model="url"
              :disabled="!complianceAcceptedInPage"
              size="large"
              placeholder="https://www.bilibili.com/video/BV..."
              clearable
              @keyup.enter="parseUrl"
            />
            <el-button :icon="Search" type="primary" size="large" :loading="busy" :disabled="!complianceAcceptedInPage" @click="parseUrl">
              解析
            </el-button>
          </div>

          <div v-if="preview" class="preview-layout">
            <img v-if="preview.thumbnail && !coverFailed" class="cover" :src="coverSrc(preview.thumbnail)" alt="" @error="coverFailed = true" />
            <div v-else class="cover cover-placeholder">暂无封面</div>
            <div class="video-meta">
              <h2>
                <a class="title-link" :href="pageUrl(preview)" target="_blank" rel="noreferrer">{{ preview.title }}</a>
              </h2>
              <dl>
                <div><dt>UP主</dt><dd>{{ preview.uploader || '未知' }}</dd></div>
                <div><dt>时长</dt><dd>{{ formatDuration(preview.duration) }}</dd></div>
                <div><dt>视频流</dt><dd>{{ preview.videos.length }}</dd></div>
                <div><dt>音频流</dt><dd>{{ preview.audios.length }}</dd></div>
              </dl>
            </div>
          </div>
        </template>

        <template v-else>
          <div class="batch-inputs">
            <el-input
              v-model="batchText"
              type="textarea"
              :rows="7"
              :disabled="!complianceAcceptedInPage || batchActive"
              placeholder="https://www.bilibili.com/video/BV..."
            />
            <p class="batch-input-hint">建议每行一个链接或 ID；也支持逗号、空格或普通文本中自动识别 B站链接 / BV / av / ep / ss。</p>
            <div class="batch-actions">
              <el-button :icon="RefreshCw" type="primary" :loading="parsingBatch" :disabled="!complianceAcceptedInPage || batchActive" @click="parseBatchRows">
                解析批量
              </el-button>
              <el-button :icon="Trash2" text type="danger" :disabled="batchActive || !batchRows.length" @click="clearBatchRows">清空</el-button>
            </div>
          </div>

          <div class="batch-meta">
            <span>上限 {{ BATCH_LIMIT }}</span>
            <span>{{ batchSummary }}</span>
          </div>

          <div v-if="batchRows.length" class="batch-list">
            <article v-for="row in batchRows" :key="row.id" class="batch-row">
              <div class="batch-thumbnail" :class="{ empty: !row.preview?.thumbnail || row.thumbnailFailed }">
                <img v-if="row.preview?.thumbnail && !row.thumbnailFailed" :src="coverSrc(row.preview.thumbnail)" alt="" @error="row.thumbnailFailed = true" />
                <FileImage v-else :size="18" />
              </div>
              <div class="batch-row-main">
                <div class="batch-row-title">
                  <strong>
                    <a v-if="batchRowPageUrl(row)" class="title-link" :href="batchRowPageUrl(row)" target="_blank" rel="noreferrer">{{ batchRowTitle(row) }}</a>
                    <span v-else>{{ batchRowTitle(row) }}</span>
                  </strong>
                  <el-tag :type="batchStatusType(row.status)" size="small">{{ batchStatusLabel(row.status) }}</el-tag>
                </div>
                <p>{{ batchRowSummary(row) }}</p>
              </div>
              <div class="batch-row-actions">
                <el-tooltip content="资源设置">
                  <el-button :icon="Settings" circle :disabled="!batchRowEditable(row)" aria-label="资源设置" @click="row.expanded = !row.expanded" />
                </el-tooltip>
                <el-tooltip content="移除">
                  <el-button :icon="CircleX" circle :disabled="batchActive" aria-label="移除批量行" @click="removeBatchRow(row)" />
                </el-tooltip>
              </div>
              <div v-if="row.expanded && row.preview && row.options" class="batch-row-settings">
                <div class="batch-setting-grid">
                  <section class="batch-setting-group">
                    <div class="batch-setting-head">
                      <span><Video :size="16" />视频</span>
                      <el-checkbox v-model="row.options.includeVideo" :disabled="batchRowLocked(row) || !row.preview.videos.length" @change="handleBatchVideoChange(row)">下载</el-checkbox>
                    </div>
                    <el-select v-model="row.options.videoFormatId" placeholder="选择视频流" filterable :disabled="batchRowLocked(row) || !row.options.includeVideo || !row.preview.videos.length" @change="handleBatchVideoFormatChange(row)">
                      <el-option v-for="item in row.preview.videos" :key="item.format_id" :label="formatRow(item)" :value="item.format_id" />
                    </el-select>
                    <div class="inline-setting">
                      <span>输出格式</span>
                      <el-segmented v-model="row.options.container" :options="containerOptions" :disabled="batchRowLocked(row) || !row.options.includeVideo || !row.options.videoFormatId" />
                    </div>
                    <el-alert v-if="videoQualityHint(row.preview)" type="info" :closable="false" :title="videoQualityHint(row.preview)" />
                  </section>

                  <section class="batch-setting-group">
                    <div class="batch-setting-head">
                      <span><Music2 :size="16" />音频</span>
                      <el-checkbox v-model="row.options.includeAudio" :disabled="batchRowLocked(row) || !row.preview.audios.length || batchWantsVideo(row)" @change="handleBatchAudioChange(row)">
                        {{ batchWantsVideo(row) ? '随视频' : '下载' }}
                      </el-checkbox>
                    </div>
                    <el-select v-model="row.options.audioFormatId" placeholder="选择音频流" filterable :disabled="batchRowLocked(row) || !row.options.includeAudio || !row.preview.audios.length" @change="handleBatchAudioFormatChange(row)">
                      <el-option v-for="item in row.preview.audios" :key="item.format_id" :label="formatRow(item)" :value="item.format_id" />
                    </el-select>
                    <div class="inline-setting">
                      <span>输出格式</span>
                      <el-segmented v-model="row.options.audioOutputFormat" :options="audioOutputOptions" :disabled="batchRowLocked(row) || !row.options.includeAudio || !row.preview.audios.length || (batchWantsVideo(row) && batchWantsAudio(row))" />
                    </div>
                  </section>

                  <section class="batch-setting-group">
                    <div class="batch-setting-head">
                      <span><FileImage :size="16" />封面</span>
                      <el-checkbox v-model="row.options.includeCover" :disabled="batchRowLocked(row) || !row.preview.thumbnail">下载</el-checkbox>
                    </div>
                    <div class="cover-selection" :class="{ muted: !row.options.includeCover || !row.preview.thumbnail }">
                      {{ !row.preview.thumbnail ? '无封面' : row.options.includeCover ? '封面图片' : '未选封面' }}
                    </div>
                  </section>
                </div>

                <el-form class="batch-row-form" label-position="top">
                  <el-form-item label="自定义文件名">
                    <el-input v-model="row.options.customFilename" :disabled="batchRowLocked(row)" />
                  </el-form-item>
                </el-form>
              </div>
            </article>
          </div>
        </template>
      </div>

      <div class="panel options-panel">
        <div class="panel-title">
          <span>{{ workMode === 'single' ? '资源选择' : '批量提交' }}</span>
        </div>

        <template v-if="workMode === 'single'">
          <div class="resource-list">
            <section class="resource-option">
              <div class="resource-option-head">
                <span><Video :size="16" />视频</span>
                <el-checkbox v-model="includeVideo" :disabled="!preview?.videos.length">下载</el-checkbox>
              </div>
              <el-select v-model="videoFormatId" placeholder="选择视频流" filterable clearable :disabled="!includeVideo || !preview?.videos.length">
                <el-option v-for="item in preview?.videos || []" :key="item.format_id" :label="formatRow(item)" :value="item.format_id" />
              </el-select>
              <div class="inline-setting">
                <span>输出格式</span>
                <el-segmented v-model="settings.default_container" :options="containerOptions" :disabled="!includeVideo || !videoFormatId" />
              </div>
              <el-alert v-if="singleVideoQualityHint" type="info" :closable="false" :title="singleVideoQualityHint" />
            </section>

            <section class="resource-option">
              <div class="resource-option-head">
                <span><Music2 :size="16" />音频</span>
                <el-checkbox v-model="includeAudio" :disabled="!preview?.audios.length || wantsVideo">
                  {{ wantsVideo ? '随视频' : '下载' }}
                </el-checkbox>
              </div>
              <el-select v-model="audioFormatId" placeholder="选择音频流" filterable clearable :disabled="!includeAudio || !preview?.audios.length">
                <el-option v-for="item in preview?.audios || []" :key="item.format_id" :label="formatRow(item)" :value="item.format_id" />
              </el-select>
              <el-alert v-if="wantsVideo && !wantsAudio" type="warning" :closable="false" title="当前未选择音频流，无法生成有声视频。" />
              <div class="inline-setting">
                <span>输出格式</span>
                <el-segmented v-model="audioOutputFormat" :options="audioOutputOptions" :disabled="!includeAudio || !preview?.audios.length || (wantsVideo && wantsAudio)" />
              </div>
            </section>

            <section class="resource-option">
              <div class="resource-option-head">
                <span><FileImage :size="16" />封面</span>
                <el-checkbox v-model="includeCover" :disabled="!preview?.thumbnail">下载</el-checkbox>
              </div>
              <div class="cover-selection" :class="{ muted: !includeCover || !preview?.thumbnail }">
                {{ !preview?.thumbnail ? '无封面' : includeCover ? '封面图片' : '未选封面' }}
              </div>
            </section>
          </div>

          <el-form label-position="top">
            <el-form-item label="自定义文件名">
              <el-input v-model="singleCustomFilename" />
            </el-form-item>
          </el-form>

          <el-button :icon="Download" type="success" size="large" :disabled="!canSubmit" :loading="creatingTask" @click="createTask">
            下载
          </el-button>
        </template>

        <template v-else>
          <p class="batch-submit-note">默认使用视频 + 音频，封面不选；可展开单行调整。</p>
          <el-button :icon="Download" type="success" size="large" :disabled="!canSubmitBatch" :loading="submittingBatch" @click="submitBatchTasks">
            提交批量
          </el-button>
        </template>
      </div>
    </section>

    <section class="tasks-band">
      <div class="section-head">
        <h2>任务队列</h2>
        <div class="section-actions">
          <el-button :icon="Trash2" text type="danger" :loading="clearingTasks" :disabled="!finishedTaskCount" @click="clearTaskList">清空记录</el-button>
          <el-button :icon="RefreshCw" text @click="loadBase">刷新</el-button>
        </div>
      </div>
      <div v-if="!tasks.length" class="empty-state">暂无任务</div>
      <div v-else class="task-list">
        <article v-for="task in tasks" :key="task.id" class="task-row">
          <div class="task-thumbnail" :class="{ empty: !taskThumbnailSrc(task) }">
            <img v-if="taskThumbnailSrc(task)" :src="taskThumbnailSrc(task)" alt="" @error="taskThumbnailFailures[task.id] = true" />
            <FileImage v-else :size="18" />
          </div>
          <div class="task-main">
            <div class="task-title">
              <strong>
                <a class="title-link" :href="taskPageUrl(task)" target="_blank" rel="noreferrer">{{ task.title || task.url }}</a>
              </strong>
              <el-tag :type="statusType(task.status)" size="small">{{ stageLabel(task.stage) }}</el-tag>
            </div>
            <div class="task-meta">
              <span>下载时间 {{ formatTimestamp(task.created_at) }}</span>
              <span v-if="task.completed_at">完成时间 {{ formatTimestamp(task.completed_at) }}</span>
            </div>
            <el-progress :percentage="Math.round(task.progress)" :status="task.status === 'failed' ? 'exception' : task.status === 'completed' ? 'success' : undefined" />
            <p>{{ task.error || task.message || task.output_path || task.output_dir }}</p>
          </div>
          <div class="task-actions">
            <el-tooltip
              content="打开目录"
              placement="bottom"
              :show-after="120"
              :hide-after="0"
              :enterable="false"
              :persistent="false"
              transition="task-tooltip-quick"
            >
              <el-button
                :icon="FolderOpen"
                circle
                aria-label="打开下载目录"
                :disabled="task.status !== 'completed'"
                @click="openTaskFolder(task)"
              />
            </el-tooltip>
            <el-tooltip
              content="取消"
              placement="bottom"
              :show-after="120"
              :hide-after="0"
              :enterable="false"
              :persistent="false"
              transition="task-tooltip-quick"
            >
              <el-button
                :icon="CircleX"
                circle
                aria-label="取消任务"
                :disabled="task.status !== 'queued' && task.status !== 'running'"
                @click="cancelTask(task)"
              />
            </el-tooltip>
            <el-tooltip
              content="重试"
              placement="bottom"
              :show-after="120"
              :hide-after="0"
              :enterable="false"
              :persistent="false"
              transition="task-tooltip-quick"
            >
              <el-button
                :icon="RotateCcw"
                circle
                aria-label="重试任务"
                :disabled="task.status !== 'failed' && task.status !== 'cancelled'"
                @click="retryTask(task)"
              />
            </el-tooltip>
          </div>
        </article>
      </div>
    </section>

    <el-dialog :model-value="complianceDialogVisible" :show-close="false" :close-on-click-modal="false" :close-on-press-escape="false" width="min(520px, 92vw)" class="compliance-dialog">
      <template #header>
        <div class="dialog-title"><ShieldCheck :size="20" />合规声明</div>
      </template>
      <p>{{ compliance.statement }}</p>
      <template #footer>
        <el-button :icon="CheckCircle2" type="primary" :loading="acceptingCompliance" @click="acceptCompliance">同意并使用</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="cookieDrawer" title="Cookie" size="420px">
      <div class="drawer-stack">
        <el-alert v-if="cookie?.configured" type="success" :closable="false" :title="cookie.masked || 'Cookie 已配置'" />
        <el-alert v-else type="warning" :closable="false" :title="cookie?.message || 'Cookie 未配置'" />
        <el-input v-model="cookieInput" type="textarea" :rows="8" placeholder="SESSDATA=...; bili_jct=...; DedeUserID=...;" />
        <p class="cookie-help">可粘贴完整 Cookie 请求头；至少建议包含 SESSDATA、bili_jct、DedeUserID。</p>
        <div class="drawer-actions">
          <el-button :icon="Trash2" :disabled="!cookie?.configured" @click="deleteCookie">删除</el-button>
          <el-button :icon="KeyRound" type="primary" :disabled="!cookieInput.trim()" @click="saveCookie">保存</el-button>
        </div>
      </div>
    </el-drawer>

    <el-drawer v-model="settingsDrawer" title="设置" size="460px">
      <el-form label-position="top" class="drawer-stack">
        <el-form-item label="保存路径">
          <el-input v-model="settings.download_dir">
            <template #prefix><FolderCog :size="16" /></template>
          </el-input>
        </el-form-item>
        <el-form-item label="默认视频格式">
          <el-segmented v-model="settings.default_container" :options="containerOptions" />
        </el-form-item>
        <el-form-item label="默认音频格式">
          <el-segmented v-model="settings.default_audio_format" :options="audioOutputOptions" />
        </el-form-item>
        <el-form-item label="同时下载任务数">
          <el-input-number v-model="settings.max_concurrent_downloads" :min="1" :max="4" />
          <p class="field-help">范围 1-4，建议 1-2；只控制同时运行的下载任务数量，不影响链接解析或单个任务内部下载。</p>
        </el-form-item>
        <el-button :icon="Play" type="primary" @click="saveSettings">保存</el-button>
      </el-form>
    </el-drawer>
  </main>
</template>
