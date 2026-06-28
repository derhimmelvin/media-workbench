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
import { api, taskSocketUrl, thumbnailProxyUrl, type ContainerFormat, type CookieStatus, type HealthResponse, type MediaFormat, type PreviewResponse, type SettingsResponse, type TaskResponse } from './api'

const health = ref<HealthResponse | null>(null)
const compliance = ref({ accepted: false, statement: '', version: '', accepted_at: null as string | null })
const cookie = ref<CookieStatus | null>(null)
const settings = ref<SettingsResponse>({
  download_dir: '',
  default_container: 'mp4',
  max_concurrent_downloads: 1
})
const tasks = ref<TaskResponse[]>([])
const preview = ref<PreviewResponse | null>(null)
const url = ref('')
const videoFormatId = ref('')
const audioFormatId = ref('')
const audioOutputFormat = ref<'m4a' | 'mp3'>('m4a')
const includeVideo = ref(false)
const includeAudio = ref(false)
const includeCover = ref(false)
const merge = ref(true)
const busy = ref(false)
const creatingTask = ref(false)
const clearingTasks = ref(false)
const acceptingCompliance = ref(false)
const cookieDrawer = ref(false)
const settingsDrawer = ref(false)
const cookieInput = ref('')
const coverFailed = ref(false)
const complianceLoaded = ref(false)
const complianceAcceptedInPage = ref(false)
const sockets = new Map<string, WebSocket>()
const containerOptions: ContainerFormat[] = ['mp4', 'mkv']

const wantsVideo = computed(() => includeVideo.value && Boolean(videoFormatId.value))
const wantsAudio = computed(() => includeAudio.value && Boolean(audioFormatId.value))
const wantsCover = computed(() => includeCover.value && Boolean(preview.value?.thumbnail))
const canMerge = computed(() => wantsVideo.value && wantsAudio.value)
const hasRequiredAudioForVideo = computed(() => !wantsVideo.value || wantsAudio.value)
const showMergeSetting = computed(() => canMerge.value)
const showVideoContainerSetting = computed(() => wantsVideo.value)
const finishedTaskCount = computed(() => tasks.value.filter((task) => ['completed', 'failed', 'cancelled'].includes(task.status)).length)
const complianceDialogVisible = computed(() => complianceLoaded.value && !complianceAcceptedInPage.value)
const canSubmit = computed(() => {
  return complianceAcceptedInPage.value && preview.value && hasRequiredAudioForVideo.value && (wantsVideo.value || wantsAudio.value || wantsCover.value) && !creatingTask.value
})

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

function coverSrc(thumbnail: string | null) {
  return thumbnail ? thumbnailProxyUrl(thumbnail) : ''
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

async function loadBase() {
  const [healthData, complianceData, cookieData, settingsData, taskData] = await Promise.all([
    api.health(),
    api.getCompliance(),
    api.cookieStatus(),
    api.getSettings(),
    api.listTasks()
  ])
  health.value = healthData
  compliance.value = complianceData
  complianceLoaded.value = true
  cookie.value = cookieData
  settings.value = settingsData
  tasks.value = taskData
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
    audioOutputFormat.value = 'm4a'
    includeVideo.value = Boolean(videoFormatId.value)
    includeAudio.value = Boolean(audioFormatId.value)
    includeCover.value = false
    ElMessage.success('解析完成')
  } catch (error) {
    preview.value = null
    coverFailed.value = false
    audioOutputFormat.value = 'm4a'
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
    const task = await api.createTask({
      url: preview.value.url,
      title: preview.value.title,
      video_format_id: videoFormat,
      audio_format_id: audioFormat,
      audio_output_format: audioOutputFormat.value,
      download_cover: downloadCover,
      thumbnail_url: downloadCover ? preview.value.thumbnail || undefined : undefined,
      merge: videoFormat && audioFormat ? merge.value : false,
      container: videoFormat ? settings.value.default_container : 'mp4',
      output_dir: settings.value.download_dir
    })
    upsertTask(task)
    attachSocket(task.id)
    ElMessage.success('任务已提交')
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '提交失败')
  } finally {
    creatingTask.value = false
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
    merge.value = true
    ensureAudioForVideo(true)
  }
})

watch(includeAudio, (enabled) => {
  if (!enabled && wantsVideo.value && preview.value?.audios.length) {
    includeAudio.value = true
    merge.value = true
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
        <p>本地核心闭环 v1</p>
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
          <el-tag v-if="preview" type="success">已解析</el-tag>
        </div>
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
            <h2>{{ preview.title }}</h2>
            <dl>
              <div><dt>UP主</dt><dd>{{ preview.uploader || '未知' }}</dd></div>
              <div><dt>时长</dt><dd>{{ formatDuration(preview.duration) }}</dd></div>
              <div><dt>视频流</dt><dd>{{ preview.videos.length }}</dd></div>
              <div><dt>音频流</dt><dd>{{ preview.audios.length }}</dd></div>
            </dl>
          </div>
        </div>
      </div>

      <div class="panel options-panel">
        <div class="panel-title">
          <span>资源选择</span>
        </div>

        <div class="resource-list">
          <section class="resource-option">
            <div class="resource-option-head">
              <span><Video :size="16" />视频</span>
              <el-checkbox v-model="includeVideo" :disabled="!preview?.videos.length">下载</el-checkbox>
            </div>
            <el-select v-model="videoFormatId" placeholder="选择视频流" filterable clearable :disabled="!includeVideo || !preview?.videos.length">
              <el-option v-for="item in preview?.videos || []" :key="item.format_id" :label="formatRow(item)" :value="item.format_id" />
            </el-select>
          </section>

          <section class="resource-option">
            <div class="resource-option-head">
              <span><Music2 :size="16" />音频</span>
              <el-checkbox v-model="includeAudio" :disabled="!preview?.audios.length || wantsVideo">
                {{ wantsVideo && merge ? '随视频' : wantsVideo ? '同步下载' : '下载' }}
              </el-checkbox>
            </div>
            <el-select v-model="audioFormatId" placeholder="选择音频流" filterable clearable :disabled="!includeAudio || !preview?.audios.length">
              <el-option v-for="item in preview?.audios || []" :key="item.format_id" :label="formatRow(item)" :value="item.format_id" />
            </el-select>
            <el-alert v-if="wantsVideo && !wantsAudio" type="warning" :closable="false" title="当前未选择音频流，无法生成有声视频。" />
            <div class="inline-setting">
              <span>输出格式</span>
              <el-segmented v-model="audioOutputFormat" :options="['m4a', 'mp3']" :disabled="!includeAudio || !preview?.audios.length || (canMerge && merge)" />
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

        <el-form v-if="showMergeSetting || showVideoContainerSetting" label-position="top">
          <el-form-item v-if="showMergeSetting" label="合并">
            <el-switch v-model="merge" active-text="合并" inactive-text="分离" />
          </el-form-item>
          <el-form-item v-if="showVideoContainerSetting" label="视频容器">
            <el-segmented v-model="settings.default_container" :options="containerOptions" />
          </el-form-item>
        </el-form>

        <el-button :icon="Download" type="success" size="large" :disabled="!canSubmit" :loading="creatingTask" @click="createTask">
          下载
        </el-button>
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
          <div class="task-main">
            <div class="task-title">
              <strong>{{ task.title || task.url }}</strong>
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
        <el-input v-model="cookieInput" type="textarea" :rows="8" placeholder="SESSDATA=...; bili_jct=..." />
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
        <el-form-item label="默认视频容器">
          <el-segmented v-model="settings.default_container" :options="containerOptions" />
        </el-form-item>
        <el-form-item label="最大并发">
          <el-input-number v-model="settings.max_concurrent_downloads" :min="1" :max="4" />
        </el-form-item>
        <el-button :icon="Play" type="primary" @click="saveSettings">保存</el-button>
      </el-form>
    </el-drawer>
  </main>
</template>
