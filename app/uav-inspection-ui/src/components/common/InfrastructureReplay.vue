<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { powerApi, type PowerDetectionItem } from '../../api1/power'
import { telecomApi } from '../../api1/telecom'
import { apiErrorMessage } from '../../utils/request'
import { findTelemetry, parseSrt, type TelemetryFrame } from '../../utils/srtParser'
import { webPath } from '../../utils/webroot'
import DetectionVideoFrame from './DetectionVideoFrame.vue'
import FlightTrace from './FlightTrace.vue'
import InfrastructureWorkspace from './InfrastructureWorkspace.vue'

interface Task {
  id: string; taskName: string; videoName: string; videoUrl: string
  srtUrl: string; createdAt: string; legacy?: boolean
}
const props = defineProps<{ kind: 'power' | 'telecom' }>()
const isPower = props.kind === 'power'
const title = isPower ? '杆路线路巡检' : '通信基站巡检'
const api = isPower ? powerApi : telecomApi
const videoRef = ref<HTMLVideoElement>()
const canvasRef = ref<HTMLCanvasElement>()
const uploadDialog = ref<HTMLDialogElement>()
const tasks = ref<Task[]>([])
const currentTask = ref<Task>()
const currentVideoUrl = computed(() => currentTask.value?.videoUrl || '')
const canPlay = computed(() => !!currentVideoUrl.value)
const backendOnline = ref(false)
const inferenceReady = ref(false)
const loading = ref(true)
const detecting = ref(false)
const isPlaying = ref(false)
const message = ref('')
const videoCurrentTime = ref(0)
const videoDuration = ref(0)
const uploadTaskName = ref('')
const uploadVideoFile = ref<File>()
const uploadSrtFile = ref<File>()
const uploadError = ref('')
const uploadingTask = ref(false)
const uploadReady = computed(() => !!uploadVideoFile.value)
const managing = ref(false)
const srtFrames = ref<TelemetryFrame[]>([])
const subtitleStatus = ref<'missing' | 'loading' | 'ready' | 'failed'>('missing')
const currentTelemetry = computed(() => {
  const time = videoCurrentTime.value
  const frame = findTelemetry(srtFrames.value, time)
  return frame && time >= frame.time && time <= frame.endTime ? frame : null
})
const confidenceDefault = isPower ? 0.30 : 0.35
const confThreshold = ref(confidenceDefault)
const iouThreshold = ref(0.45)
const autoDetectEnabled = ref(true)
const detectIntervalSec = ref(1)
const detections = ref<PowerDetectionItem[]>([])
const currentStats = computed(() => ({
  primary: detections.value.filter(item => item.name === (isPower ? 'WirePole' : 'Station')).length,
  secondary: detections.value.filter(item => item.name === (isPower ? 'Wire' : 'Antenna')).length,
}))
const detectedTime = ref<number | null>(null)
const statusText = computed(() => {
  if (loading.value) return '加载中'
  if (!backendOnline.value) return '服务未连接'
  if (detecting.value) return '正在识别'
  if (isPlaying.value) return '播放中'
  if (!canPlay.value) return '待上传'
  return inferenceReady.value ? '待识别' : '录像复查'
})
let taskRevision = 0
let frameRevision = 0
let lastBucket = -1
let captureSize = { width: 0, height: 0 }
let healthTimer: number | undefined
let observer: ResizeObserver | undefined
let active = true

function errorText(failure: unknown, fallback: string) {
  return apiErrorMessage(failure, fallback)
}
function videoStem(name: string) {
  return name.replace(/\.[^/.]+$/, '')
}

async function refreshHealth() {
  try {
    const result = await api.getHealth()
    backendOnline.value = true
    inferenceReady.value = result.data.inference_available ?? result.data.status === 'ready'
  } catch {
    backendOnline.value = false
    inferenceReady.value = false
  }
}

async function loadTasks(preferredId?: string) {
  try {
    let entries: Task[]
    if (isPower) {
      const result = await powerApi.getVideos()
      entries = result.data.videos.map(item => ({
        id: item.id || item.name, taskName: item.task_name || videoStem(item.name), videoName: item.name,
        videoUrl: webPath(item.url), srtUrl: webPath(item.srt_url || ''), createdAt: item.created_at || '',
      }))
    } else {
      const [taskResult, legacyResult] = await Promise.allSettled([telecomApi.getTasks(), telecomApi.getVideos()])
      if (taskResult.status === 'rejected' && legacyResult.status === 'rejected') throw taskResult.reason
      entries = taskResult.status === 'fulfilled' ? taskResult.value.data.tasks.map(item => ({
        id: item.task_id, taskName: item.task_name, videoName: item.video_name, videoUrl: webPath(item.video_url),
        srtUrl: webPath(item.srt_url), createdAt: item.created_at,
      })) : []
      if (legacyResult.status === 'fulfilled') {
        entries.push(...legacyResult.value.data.videos.map(item => ({
          id: 'media:' + item.name, taskName: videoStem(item.name), videoName: item.name,
          videoUrl: webPath(item.url), srtUrl: '', createdAt: '', legacy: true,
        })))
      }
      if (taskResult.status === 'rejected') message.value = '已上传的任务暂时无法读取。'
    }
    if (!active) return
    tasks.value = entries.sort((a, b) => b.createdAt.localeCompare(a.createdAt))
    const next = entries.find(item => item.id === (preferredId || currentTask.value?.id)) || entries[0]
    if (next?.id === currentTask.value?.id) currentTask.value = next
    else await selectTask(next)
  } catch (failure) {
    message.value = errorText(failure, '无法读取历史任务。')
  }
}
async function selectTask(task?: Task) {
  const version = ++taskRevision
  videoRef.value?.pause()
  currentTask.value = task
  isPlaying.value = false
  videoCurrentTime.value = 0
  videoDuration.value = 0
  srtFrames.value = []
  resetDetectionState()
  message.value = ''
  subtitleStatus.value = task?.srtUrl ? 'loading' : 'missing'
  if (!task?.srtUrl) return
  try {
    const response = await fetch(task.srtUrl)
    if (!response.ok) throw new Error()
    const subtitle = await response.text()
    const text = /^\uFEFF?WEBVTT/.test(subtitle)
      ? subtitle.replace(/^\uFEFF?WEBVTT[^\n]*\n/, '').replace(/(?:^|(?<=--> ))(\d{2}:\d{2}\.\d{3})/gm, '00:$1')
      : subtitle
    const frames = parseSrt(text)
    if (version !== taskRevision) return
    srtFrames.value = frames
    subtitleStatus.value = 'ready'
  } catch {
    if (version === taskRevision) subtitleStatus.value = 'failed'
  }
}
function onVideoFileSelect(event: Event) {
  uploadVideoFile.value = (event.target as HTMLInputElement).files?.[0]
  uploadError.value = ''
}
function onSrtFileSelect(event: Event) {
  uploadSrtFile.value = (event.target as HTMLInputElement).files?.[0]
  uploadError.value = ''
}
function clearUploadDraft() {
  uploadVideoFile.value = undefined
  uploadSrtFile.value = undefined
  uploadTaskName.value = ''
  uploadError.value = ''
  uploadDialog.value?.querySelector('form')?.reset()
}
async function confirmUploadTask() {
  if (!uploadVideoFile.value || uploadingTask.value) return
  const body = new FormData()
  body.set('task_name', uploadTaskName.value || videoStem(uploadVideoFile.value.name))
  body.set('video', uploadVideoFile.value)
  if (uploadSrtFile.value) body.set('srt', uploadSrtFile.value)
  uploadingTask.value = true
  uploadError.value = ''
  try {
    const id = isPower ? (await powerApi.uploadTask(body)).data.id : (await telecomApi.uploadTask(body)).data.task_id
    uploadDialog.value?.close()
    await loadTasks(id)
  } catch (failure) {
    uploadError.value = errorText(failure, '无法上传任务，请检查文件格式后重试。')
  } finally { uploadingTask.value = false }
}
async function renameTask(task: Task) {
  if (managing.value) return
  const name = window.prompt('请输入任务名称', task.taskName)?.trim()
  if (!name || name === task.taskName) return
  managing.value = true
  try {
    await telecomApi.renameTask(task.id, name)
    await loadTasks()
  } catch (failure) { message.value = errorText(failure, '重命名失败。') }
  finally { managing.value = false }
}
async function deleteTask(task: Task) {
  if (managing.value || !window.confirm('删除任务「' + task.taskName + '」？删除后无法恢复。')) return
  managing.value = true
  try {
    await telecomApi.deleteTask(task.id)
    await loadTasks()
  } catch (failure) { message.value = errorText(failure, '删除失败。') }
  finally { managing.value = false }
}
function formatClock(seconds: number) {
  return [Math.floor(seconds / 60), Math.floor(seconds % 60)].map(value => String(value).padStart(2, '0')).join(':')
}
async function togglePlay() {
  const video = videoRef.value
  if (!video) return
  if (!video.paused) { video.pause(); return }
  try { await video.play() }
  catch { if (video === videoRef.value) message.value = '无法播放录像，请重试。' }
}
function replayVideo() {
  seekVideo(0)
  if (videoRef.value?.paused) void togglePlay()
}
function seekVideo(seconds: number) {
  if (!videoRef.value || !videoDuration.value) return
  resetDetectionState()
  videoRef.value.currentTime = Math.min(videoDuration.value, Math.max(0, seconds))
  syncTime()
}
function syncTime() { videoCurrentTime.value = videoRef.value?.currentTime || 0 }
function onPlay() { isPlaying.value = true }
function onPause() { isPlaying.value = false }
function onEnded() { isPlaying.value = false }
function onVideoError() {
  isPlaying.value = false
  videoDuration.value = 0
  resetDetectionState()
  message.value = '这个视频无法播放，请重新上传录像。'
}
function onVideoLoadedMetadata() {
  const duration = videoRef.value?.duration
  videoDuration.value = duration && Number.isFinite(duration) ? duration : 0
  drawDetections()
}
function resetDetectionState() {
  frameRevision++
  lastBucket = -1
  detectedTime.value = null
  detections.value = []
  drawDetections()
}
function onTimeUpdate() {
  syncTime()
  const video = videoRef.value
  if (!autoDetectEnabled.value || !video || video.paused || video.seeking || video.readyState < 2 || !inferenceReady.value || detecting.value) return
  const bucket = Math.floor(video.currentTime / (isPower ? detectIntervalSec.value : 1 / 3))
  if (bucket === lastBucket) return
  lastBucket = bucket
  void detectCurrentFrame()
}
async function detectCurrentFrame() {
  const video = videoRef.value
  if (!video || !video.videoWidth || video.readyState < 2 || video.seeking || detecting.value || !inferenceReady.value) return
  const version = frameRevision
  const time = video.currentTime
  const scale = Math.min(1, 640 / Math.max(video.videoWidth, video.videoHeight))
  const canvas = document.createElement('canvas')
  canvas.width = Math.max(1, Math.round(video.videoWidth * scale))
  canvas.height = Math.max(1, Math.round(video.videoHeight * scale))
  const context = canvas.getContext('2d')
  if (!context) return
  detecting.value = true
  try {
    context.drawImage(video, 0, 0, canvas.width, canvas.height)
    const image = canvas.toDataURL('image/jpeg', 0.75).split(',')[1]
    if (!image) throw new Error('无法读取当前画面。')
    const result = await api.detectFrame(image, confThreshold.value, iouThreshold.value)
    if (version !== frameRevision || video !== videoRef.value) return
    captureSize = { width: canvas.width, height: canvas.height }
    detections.value = result.data.detections
    detectedTime.value = time
    message.value = ''
    drawDetections()
  } catch (failure) {
    if (version === frameRevision) message.value = errorText(failure, '暂时无法识别，请检查模型或稍后重试。')
  } finally { detecting.value = false }
}
function drawDetections() {
  const canvas = canvasRef.value
  const video = videoRef.value
  if (!canvas) return
  canvas.width = canvas.clientWidth
  canvas.height = canvas.clientHeight
  const context = canvas.getContext('2d')
  if (!context || !video?.videoWidth || !captureSize.width || !detections.value.length) return
  const scale = Math.min(canvas.width / video.videoWidth, canvas.height / video.videoHeight)
  const width = video.videoWidth * scale
  const height = video.videoHeight * scale
  const left = (canvas.width - width) / 2
  const top = (canvas.height - height) / 2
  context.lineWidth = 2
  context.font = '600 14px sans-serif'
  context.textBaseline = 'top'
  for (const item of detections.value) {
    const [x1, y1, x2, y2] = item.bbox
    const x = left + x1 / captureSize.width * width
    const y = top + y1 / captureSize.height * height
    const color = ['Wire', 'Antenna'].includes(item.name) ? '#fbce82' : '#76e5bc'
    const label = (item.cn || item.name) + ' ' + Math.round(item.conf * 100) + '%'
    const labelWidth = context.measureText(label).width + 10
    const labelX = Math.max(left, Math.min(left + width - labelWidth, x))
    const labelY = Math.max(top, y - 20)
    context.strokeStyle = color
    context.strokeRect(x, y, (x2 - x1) / captureSize.width * width, (y2 - y1) / captureSize.height * height)
    context.fillStyle = color
    context.fillRect(labelX, labelY, labelWidth, 20)
    context.fillStyle = '#14231d'
    context.fillText(label, labelX + 5, labelY + 3)
  }
}
function exportReport() {
  const report = document.implementation.createHTMLDocument(title + '画面报告')
  report.documentElement.lang = 'zh-CN'
  const meta = report.createElement('meta')
  meta.setAttribute('charset', 'utf-8')
  report.head.prepend(meta)
  const style = report.createElement('style')
  style.textContent = 'body{max-width:800px;margin:40px auto;padding:0 20px;font:16px/1.6 system-ui;color:#172a27} h1{border-bottom:3px solid #23598e} p{margin:.45em 0}'
  report.head.append(style)
  function add(tag: string, text: string) {
    const element = report.createElement(tag)
    element.textContent = text
    report.body.append(element)
  }
  add('h1', title + '画面报告')
  add('p', '任务 ' + (currentTask.value?.taskName || '—'))
  add('p', '录像 ' + (currentTask.value?.videoName || '—'))
  add('p', '录像位置 ' + formatClock(videoCurrentTime.value))
  const point = currentTelemetry.value
  if (point?.gpsAvailable) add('p', '经纬度 ' + point.longitude.toFixed(6) + ', ' + point.latitude.toFixed(6))
  if (point?.altitudeAvailable) add('p', '相对起飞点高度 ' + point.relAlt.toFixed(1) + ' m')
  add('h2', '最近一次识别')
  if (detectedTime.value === null) add('p', '尚无识别结果。')
  else {
    add('p', '录像位置 ' + formatClock(detectedTime.value) + ' · 基站 ' + currentStats.value.primary + ' · 天线 ' + currentStats.value.secondary)
    detections.value.forEach(item => add('p', (item.cn || item.name) + ' · 识别把握 ' + Math.round(item.conf * 100) + '%'))
  }
  const url = URL.createObjectURL(new Blob(['<!doctype html>', report.documentElement.outerHTML], { type: 'text/html;charset=utf-8' }))
  const link = document.createElement('a')
  link.href = url
  link.download = (currentTask.value?.taskName || title).replace(/[\\/:*?"<>|]/g, '_') + '-画面报告.html'
  link.click()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}
onMounted(async () => {
  observer = new ResizeObserver(drawDetections)
  if (canvasRef.value?.parentElement) observer.observe(canvasRef.value.parentElement)
  await Promise.all([refreshHealth(), loadTasks()])
  if (!active) return
  loading.value = false
  healthTimer = window.setInterval(refreshHealth, 10000)
})
onUnmounted(() => {
  active = false
  taskRevision++
  frameRevision++
  window.clearInterval(healthTimer)
  observer?.disconnect()
})
</script>

<template>
  <InfrastructureWorkspace :kind="kind" :title="title" :task="currentTask?.taskName" :status="statusText">
    <template #actions>
      <RouterLink class="infra-button" :to="{ path: '/assets', query: { category: kind + '-model' } }">模型与素材</RouterLink>
      <button v-if="!isPower" class="infra-button" :disabled="!videoDuration" @click="exportReport">导出画面报告</button>

      <button class="infra-button infra-button--primary" @click="uploadDialog?.showModal()">上传巡检任务</button>
    </template>
    <DetectionVideoFrame :data-testid="kind + '-video-frame'" label="巡检录像" :status="detecting ? '正在识别' : isPlaying ? '播放中' : '已暂停'"
      :playing="isPlaying" :disabled="!canPlay" :current-time="videoCurrentTime" :duration="videoDuration" :empty="!canPlay"
      :empty-text="loading ? '正在加载任务…' : '上传巡检录像，或从右上角打开历史任务。'"
      @toggle="togglePlay" @replay="replayVideo" @seek="seekVideo">
      <video v-if="canPlay" ref="videoRef" :key="currentVideoUrl" class="h-full w-full object-contain" :src="currentVideoUrl"
        muted playsinline preload="auto" @play="onPlay" @pause="onPause" @ended="onEnded" @timeupdate="onTimeUpdate"
        @loadedmetadata="onVideoLoadedMetadata" @seeking="resetDetectionState" @seeked="syncTime" @error="onVideoError" />
      <canvas ref="canvasRef" class="pointer-events-none absolute inset-0 h-full w-full"></canvas>
    </DetectionVideoFrame>
    <section class="infra-panel infra-toolbar" aria-label="识别操作">
      <div>
        <button class="infra-button infra-button--primary" :disabled="!canPlay || !inferenceReady || detecting || !videoDuration" @click="detectCurrentFrame">识别当前画面</button>
        <label><input v-model="autoDetectEnabled" type="checkbox" /> 播放时自动识别</label>
        <details>
          <summary>识别设置</summary>
          <label>识别阈值 {{ Math.round(confThreshold * 100) }}% <input v-model.number="confThreshold" type="range" min="0.1" max="0.9" step="0.01" /></label>
          <label>重叠判定 {{ Math.round(iouThreshold * 100) }}% <input v-model.number="iouThreshold" type="range" min="0.1" max="0.9" step="0.01" /></label>
          <label v-if="isPower">自动识别间隔 {{ detectIntervalSec.toFixed(1) }} 秒 <input v-model.number="detectIntervalSec" type="range" min="0.5" max="3" step="0.1" /></label>
        </details>
      </div>
      <p v-if="message" class="infra-message" role="status">{{ message }}</p>
    </section>
    <template #results>
      <section class="replay-results" aria-labelledby="replay-detections-heading">
        <div class="replay-section-head">
          <h2 id="replay-detections-heading">画面目标</h2>
          <span v-if="detectedTime !== null" class="infra-muted">识别时间 {{ formatClock(detectedTime) }}</span>
        </div>
        <div class="infra-metrics">
          <div><strong>{{ detectedTime === null ? '—' : currentStats.primary }}</strong>{{ isPower ? '杆体' : '基站' }}</div>
          <div><strong>{{ detectedTime === null ? '—' : currentStats.secondary }}</strong>{{ isPower ? '线缆' : '天线' }}</div>
        </div>
        <p v-if="detectedTime === null" class="replay-hint infra-muted">
          {{ loading ? '正在连接巡检服务…' : !backendOnline ? '巡检服务暂不可用。' : !inferenceReady ? '识别模型尚未就绪。' : '播放录像或点击「识别当前画面」后显示数量。' }}
        </p>
      </section>

      <section class="replay-flight" aria-labelledby="replay-flight-heading">
        <div class="replay-section-head">
          <h2 id="replay-flight-heading">飞行数据</h2>
          <span v-if="subtitleStatus === 'ready' && currentTelemetry" class="infra-muted">SRT · {{ formatClock(videoCurrentTime) }}</span>
        </div>
        <p v-if="!currentTask" class="infra-empty">打开带 SRT 的巡检任务后可查看。</p>
        <p v-else-if="subtitleStatus === 'missing'" class="infra-empty">这个任务没有附带飞行字幕。</p>
        <p v-else-if="subtitleStatus === 'loading'" class="infra-empty">正在读取飞行字幕…</p>
        <p v-else-if="subtitleStatus === 'failed'" class="infra-warning">无法读取飞行字幕，录像仍可播放。</p>
        <p v-else-if="!srtFrames.length" class="infra-empty">字幕中没有可同步的时间数据。</p>
        <p v-else-if="!currentTelemetry" class="infra-empty">当前录像时间没有对应的飞行字幕。</p>
        <dl v-else class="replay-flight-data">
          <div><dt>经度</dt><dd>{{ currentTelemetry.gpsAvailable ? currentTelemetry.longitude.toFixed(6) : '—' }}</dd></div>
          <div><dt>纬度</dt><dd>{{ currentTelemetry.gpsAvailable ? currentTelemetry.latitude.toFixed(6) : '—' }}</dd></div>
          <div><dt>相对起飞点高度</dt><dd>{{ currentTelemetry.altitudeAvailable ? currentTelemetry.relAlt.toFixed(1) + ' m' : '—' }}</dd></div>
          <div v-if="currentTelemetry.speed !== undefined && Number.isFinite(currentTelemetry.speed)"><dt>飞行速度</dt><dd>{{ currentTelemetry.speed.toFixed(1) }} m/s</dd></div>
          <div v-if="currentTelemetry.datetime" class="replay-flight-wide"><dt>拍摄时间</dt><dd>{{ currentTelemetry.datetime }}</dd></div>
        </dl>
        <FlightTrace v-if="subtitleStatus === 'ready'" :frames="srtFrames" :current="currentTelemetry" :time="videoCurrentTime"
          @seek="videoRef?.pause(); seekVideo($event)" />
      </section>
    </template>
    <template #history="{ close }">
      <div v-if="tasks.length" class="infra-list">
        <article v-for="item in tasks" :key="item.id" class="infra-row">
          <div><h3>{{ item.taskName }}</h3><p class="infra-muted">{{ item.videoName }}<span v-if="item.srtUrl"> · 已附定位字幕</span><span v-if="item.createdAt"> · {{ item.createdAt }}</span></p></div>
          <div class="infra-actions">

            <button v-if="!isPower && !item.legacy" class="infra-button" :disabled="managing" @click="renameTask(item)">重命名</button>
            <button v-if="!isPower && !item.legacy" class="infra-button" :disabled="managing" @click="deleteTask(item)">删除</button>
            <button class="infra-button" :disabled="currentTask?.id === item.id" @click="close(); selectTask(item)">{{ currentTask?.id === item.id ? '当前任务' : '打开任务' }}</button>
          </div>
        </article>
      </div>
      <p v-else class="infra-empty">还没有{{ title }}任务。</p>
    </template>
    <template #dialogs>
      <dialog ref="uploadDialog" class="infra-dialog" @close="clearUploadDraft" @cancel="uploadingTask && $event.preventDefault()">
        <h2>上传{{ title }}任务</h2>
        <form class="infra-form" @submit.prevent="confirmUploadTask">
          <label>任务名称 <input v-model.trim="uploadTaskName" class="infra-input" maxlength="100" :placeholder="isPower ? '例如：北区沿线复查' : '例如：东区 3 号基站复查'" /></label>
          <label>巡检视频 <input class="infra-input" type="file" accept=".mp4,.avi,.mov,.mkv" required @change="onVideoFileSelect" /></label>
          <label>飞行字幕（可选） <input class="infra-input" type="file" :accept="isPower ? '.srt' : '.srt,.txt,.vtt'" @change="onSrtFileSelect" /></label>
          <p v-if="uploadError" class="infra-warning" role="alert">{{ uploadError }}</p>
          <footer><button type="button" class="infra-button" :disabled="uploadingTask" @click="uploadDialog?.close()">取消</button><button type="submit" class="infra-button infra-button--primary" :disabled="!uploadReady || uploadingTask">{{ uploadingTask ? '正在上传' : '上传任务' }}</button></footer>
        </form>
      </dialog>
    </template>
  </InfrastructureWorkspace>
</template>

<style scoped>
.replay-section-head { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; margin-bottom: 9px; }
.replay-section-head h2 { margin: 0; }
.replay-section-head span { font-variant-numeric: tabular-nums; font-size: 13px; }
.replay-flight { border-top: 1px solid var(--border); padding-top: 13px; }
.replay-flight:has(.flight-trace) { display: flex; flex-direction: column; }
.replay-hint { margin: 8px 0 0; font-size: 13px; }
.replay-flight-data { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; margin: 0; }
.replay-flight-data > div { min-width: 0; background: #f7f9f9; border: 1px solid var(--border); border-radius: 7px; padding: 9px; }
.replay-flight-data dt { color: var(--muted); font-size: 13px; }
.replay-flight-data dd { margin: 3px 0 0; font-weight: 650; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.replay-flight-wide { grid-column: 1 / -1; }
.infra-toolbar { display: grid; gap: 8px; }
.infra-toolbar > div { display: flex; flex-wrap: wrap; align-items: start; gap: 8px 16px; }
.infra-toolbar label, .infra-toolbar summary { cursor: pointer; }
.infra-toolbar > div > label, .infra-toolbar summary { line-height: 36px; }
.infra-toolbar details label { line-height: 1.8; display: grid; }
.infra-toolbar input { accent-color: var(--accent); }
</style>
