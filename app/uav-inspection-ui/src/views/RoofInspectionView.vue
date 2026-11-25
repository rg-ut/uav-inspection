<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { FilePlus2, ScanLine, Upload } from 'lucide-vue-next'
import InfrastructureWorkspace from '../components/common/InfrastructureWorkspace.vue'
import { findTelemetry, parseSrt, type TelemetryFrame } from '../utils/srtParser'
import { webPath } from '../utils/webroot'

type Review = 'pending' | 'confirmed' | 'rejected'
interface Task {
  id: string; name: string; status: string; progress: number; defect_count: number
  video_url: string; srt_url: string; report_url: string; created_at: string; error: string
}
interface Defect {
  id: string; class_name: string; confidence?: number; video_time_ms?: number
  review_status?: Review; screenshot_path?: string; bbox?: [number, number, number, number]
}
interface LegacyPoint { video_time_ms: number; latitude?: number; longitude?: number; relative_altitude?: number; rel_alt?: number; altitude?: number }

const api = '/api/v1/roof'
const statuses: Record<string, string> = { uploaded: '待检测', running: '检测中', completed: '已检测', failed: '检测中断' }
const reviewLabels: Record<Review, string> = { pending: '待复核', confirmed: '已确认', rejected: '误报' }
const tasks = ref<Task[]>([])
const task = ref<Task | null>(null)
const defects = ref<Defect[]>([])
const frames = ref<TelemetryFrame[]>([])
const legacy = ref<LegacyPoint[]>([])
const reviewFilter = ref<Review | 'all'>('all')
const activeDefectId = ref('')
const brokenImages = ref<string[]>([])
const model = ref({ ready: false, name: '', message: '请添加屋顶巡检模型。' })
const assetDialog = ref<HTMLDialogElement>()
const taskDialog = ref<HTMLDialogElement>()
const video = ref<HTMLVideoElement>()
const videoStage = ref<HTMLElement>()
const videoTime = ref(0)
const box = ref({ left: 0, top: 0, width: 0, height: 0 })
const confidence = ref(0.3)
const name = ref('')
const videoFile = ref<File>()
const srtFile = ref<File>()
const modelFile = ref<File>()
const busy = ref('')
const errorMessage = ref('')
const dialogError = ref('')
const note = ref('')
let selection = 0
let poll: ReturnType<typeof setTimeout> | undefined
let resizeObserver: ResizeObserver | undefined

const visibleDefects = computed(() => reviewFilter.value === 'all' ? defects.value : defects.value.filter(item => (item.review_status || 'pending') === reviewFilter.value))
const counts = computed(() => ({
  pending: defects.value.filter(item => !item.review_status || item.review_status === 'pending').length,
  confirmed: defects.value.filter(item => item.review_status === 'confirmed').length,
  rejected: defects.value.filter(item => item.review_status === 'rejected').length,
}))
const telemetry = computed(() => {
  if (frames.value.length) {
    const point = findTelemetry(frames.value, videoTime.value)
    if (!point || videoTime.value < point.time || videoTime.value > point.endTime) return null
    return { lat: point.gpsAvailable ? point.latitude : undefined, lng: point.gpsAvailable ? point.longitude : undefined, height: point.altitudeAvailable ? point.relAlt : undefined }
  }
  const point = [...legacy.value].reverse().find(item => item.video_time_ms <= videoTime.value * 1000)
  return point ? { lat: point.latitude, lng: point.longitude, height: point.relative_altitude ?? point.rel_alt ?? point.altitude } : null
})
const activeDefect = computed(() => defects.value.find(item =>
  item.id === activeDefectId.value && item.bbox?.length === 4 && item.bbox.every(value => value >= 0 && value <= 1) && Math.abs((item.video_time_ms || 0) / 1000 - videoTime.value) < 0.75
))

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(webPath(api + path), { credentials: 'same-origin', ...options })
  if (!response.ok) {
    const data = await response.json().catch(() => ({}))
    throw new Error(typeof data.detail === 'string' ? data.detail : '操作未完成，请稍后重试。')
  }
  return response.json()
}
function errorText(error: unknown) {
  return error instanceof Error ? error.message : '连接失败，请稍后重试。'
}
function formatVideoTime(milliseconds = 0) {
  const seconds = Math.max(0, Math.floor(milliseconds / 1000))
  return [Math.floor(seconds / 60), seconds % 60].map(value => String(value).padStart(2, '0')).join(':')
}
function displayNumber(value: number | undefined, places: number, units = '') {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(places) + units : '—'
}
function fileValue(event: Event) {
  return (event.target as HTMLInputElement).files?.[0]
}
function evidenceUrl(item: Defect) {
  const path = item.screenshot_path
  if (!path || brokenImages.value.includes(item.id)) return ''
  return webPath(path.startsWith('/') || /^https?:\/\//i.test(path) ? path : api + '/reports/' + task.value?.id + '/' + path)
}

async function loadTasks() {
  tasks.value = (await request<{ tasks: Task[] }>('/tasks')).tasks
}
async function refreshTask(id: string, token: number) {
  if (poll) clearTimeout(poll)
  try {
    const path = '/tasks/' + encodeURIComponent(id)
    const [details, results] = await Promise.all([request<Task>(path), request<{ defects: Defect[] }>(path + '/defects')])
    if (token !== selection) return
    const wasRunning = task.value?.status === 'running'
    task.value = details
    defects.value = results.defects
    const index = tasks.value.findIndex(item => item.id === id)
    if (index >= 0) tasks.value[index] = details
    if (details.status === 'running') poll = setTimeout(() => void refreshTask(id, token), 1100)
    if (wasRunning && details.status === 'completed') note.value = '检测完成，点击右侧缺陷可回看。'
  } catch (error) {
    if (token !== selection) return
    errorMessage.value = errorText(error)
    if (task.value?.status === 'running') poll = setTimeout(() => void refreshTask(id, token), 3000)
  }
}
async function chooseTask(id: string) {
  const token = ++selection
  task.value = tasks.value.find(item => item.id === id) || null
  videoTime.value = 0
  frames.value = []
  legacy.value = []
  defects.value = []
  brokenImages.value = []
  activeDefectId.value = ''
  note.value = ''
  errorMessage.value = ''
  reviewFilter.value = 'all'
  const taskData = refreshTask(id, token)
  try {
    const url = task.value?.srt_url
    if (url) {
      const response = await fetch(webPath(url))
      if (!response.ok) throw new Error('无法读取定位字幕。')
      const parsed = parseSrt(await response.text())
      if (token === selection) {
        frames.value = parsed
        if (!parsed.some(point => point.gpsAvailable || point.altitudeAvailable)) note.value = '字幕中没有可用的无人机定位或高度。'
      }
    } else {
      const data = await request<{ telemetry: LegacyPoint[] }>('/tasks/' + encodeURIComponent(id) + '/telemetry')
      if (token === selection) legacy.value = data.telemetry
    }
  } catch (error) {
    if (token === selection) errorMessage.value = errorText(error)
  }
  await taskData
}
function updateVideoBox() {
  const element = video.value
  if (!element?.videoWidth) return
  const width = element.clientWidth
  const height = element.clientHeight
  const scale = Math.min(width / element.videoWidth, height / element.videoHeight)
  const fittedWidth = element.videoWidth * scale
  const fittedHeight = element.videoHeight * scale
  box.value = { left: (width - fittedWidth) / 2, top: (height - fittedHeight) / 2, width: fittedWidth, height: fittedHeight }
}
function updateTime() {
  videoTime.value = video.value?.currentTime || 0
}
function jump(item: Defect) {
  if (!video.value || !Number.isFinite(video.value.duration) || typeof item.video_time_ms !== 'number') return
  video.value.pause()
  video.value.currentTime = Math.min(video.value.duration, item.video_time_ms / 1000)
  activeDefectId.value = item.id
  updateTime()
}
function openDialog(dialog: HTMLDialogElement | undefined) {
  dialogError.value = ''
  dialog?.showModal()
}
function resetTaskForm() {
  taskDialog.value?.querySelector('form')?.reset()
  videoFile.value = undefined
  srtFile.value = undefined
  name.value = ''
}
function resetModelForm() {
  assetDialog.value?.querySelector('form')?.reset()
  modelFile.value = undefined
}
async function uploadTask() {
  if (!videoFile.value || busy.value) return
  busy.value = 'task'
  dialogError.value = ''
  try {
    const data = new FormData()
    data.set('task_name', name.value)
    data.set('video', videoFile.value)
    if (srtFile.value) data.set('srt', srtFile.value)
    const { task_id } = await request<{ task_id: string }>('/tasks/upload', { method: 'POST', body: data })
    taskDialog.value?.close()
    name.value = ''
    videoFile.value = undefined
    srtFile.value = undefined
    await loadTasks()
    await chooseTask(task_id)
  } catch (error) {
    if (taskDialog.value?.open) dialogError.value = errorText(error)
    else errorMessage.value = errorText(error)
  } finally { busy.value = '' }
}
async function uploadModel() {
  if (!modelFile.value || busy.value) return
  busy.value = 'model'
  dialogError.value = ''
  try {
    const body = new FormData()
    body.set('file', modelFile.value)
    model.value = await request<typeof model.value>('/model', { method: 'POST', body })
    assetDialog.value?.close()
    modelFile.value = undefined
    note.value = model.value.message
  } catch (error) { dialogError.value = errorText(error) }
  finally { busy.value = '' }
}
async function startAnalysis() {
  if (!task.value || busy.value) return
  const { id, defect_count } = task.value
  if (defect_count && !window.confirm('重新检测会更新未复核的自动识别结果。继续吗？')) return
  const token = selection
  busy.value = 'analysis'
  errorMessage.value = ''
  note.value = ''
  try {
    await request('/tasks/' + encodeURIComponent(id) + '/analysis', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ confidence: confidence.value }),
    })
    if (token === selection) await refreshTask(id, token)
  } catch (error) { if (token === selection) errorMessage.value = errorText(error) }
  finally { busy.value = '' }
}
async function review(item: Defect, value: Review) {
  if (!task.value || busy.value) return
  const token = selection
  busy.value = 'review'
  errorMessage.value = ''
  try {
    await request('/tasks/' + encodeURIComponent(task.value.id) + '/defects/' + encodeURIComponent(item.id) + '/review', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ review_status: value }),
    })
    if (token === selection) item.review_status = value
  } catch (error) { if (token === selection) errorMessage.value = errorText(error) }
  finally { busy.value = '' }
}
onMounted(async () => {
  await nextTick()
  resizeObserver = new ResizeObserver(updateVideoBox)
  if (videoStage.value) resizeObserver.observe(videoStage.value)
  const results = await Promise.allSettled([loadTasks(), request<typeof model.value>('/model')])
  if (results[1].status === 'fulfilled') model.value = results[1].value
  else errorMessage.value = errorText(results[1].reason)
  if (results[0].status === 'rejected') errorMessage.value = errorText(results[0].reason)
  else if (tasks.value[0]) await chooseTask(tasks.value[0].id)
})
onUnmounted(() => { ++selection; if (poll) clearTimeout(poll); resizeObserver?.disconnect() })
</script>

<template>
  <InfrastructureWorkspace kind="roof" title="屋顶巡检" :task="task?.name" :status="task ? (statuses[task.status] || '待检测') : '待上传'">
    <template #actions>
      <button class="infra-button" @click="openDialog(assetDialog)"><Upload :size="16" /> 上传模型</button>
      <button class="infra-button infra-button--primary" @click="openDialog(taskDialog)"><FilePlus2 :size="16" /> 上传巡检任务</button>
    </template>

    <div ref="videoStage" class="infra-video">
      <video v-if="task?.video_url" :key="task.id" ref="video" :src="webPath(task.video_url)" controls playsinline preload="metadata"
        aria-label="屋顶巡检录像" @loadedmetadata="updateVideoBox" @timeupdate="updateTime" @seeked="updateTime"
        @error="errorMessage = '无法播放此录像，请重新上传视频。'" />
      <p v-else>{{ task ? '这个任务没有可播放的录像。' : '上传巡检录像，或从右上角打开历史任务。' }}</p>
      <div v-if="activeDefect?.bbox && box.width" class="roof-video-box" :style="{
        left: (box.left + activeDefect.bbox[0] * box.width) + 'px', top: (box.top + activeDefect.bbox[1] * box.height) + 'px',
        width: ((activeDefect.bbox[2] - activeDefect.bbox[0]) * box.width) + 'px',
        height: ((activeDefect.bbox[3] - activeDefect.bbox[1]) * box.height) + 'px'
      }"><span>{{ activeDefect.class_name }}</span></div>
    </div>
    <section class="infra-panel infra-telemetry" aria-label="拍摄位置">
      <span>纬度<strong>{{ displayNumber(telemetry?.lat, 6) }}</strong></span>
      <span>经度<strong>{{ displayNumber(telemetry?.lng, 6) }}</strong></span>
      <span>相对起飞点高度<strong>{{ displayNumber(telemetry?.height, 1, ' m') }}</strong></span>
      <span v-if="!task?.srt_url && !legacy.length" class="infra-muted">未提供飞行字幕</span>
    </section>
    <section class="infra-panel roof-tools" aria-label="视频检测">
      <div><strong>{{ model.ready ? '模型已就绪' : model.message }}</strong><small v-if="model.ready">{{ model.name }}</small></div>
      <label>识别阈值
        <select v-model.number="confidence" class="infra-input" :disabled="task?.status === 'running'">
          <option :value="0.2">20%</option><option :value="0.3">30%</option><option :value="0.45">45%</option><option :value="0.6">60%</option>
        </select>
      </label>
      <button class="infra-button infra-button--primary" :disabled="!task?.video_url || !model.ready || !!busy || task.status === 'running'" @click="startAnalysis"><ScanLine :size="16" />{{ task?.status === 'completed' ? '重新检测' : '开始检测' }}</button>
      <p v-if="task?.status === 'running'" class="roof-feedback" role="status"><progress :value="task.progress" max="100" /> 检测进度 {{ task.progress }}%</p>
      <p v-if="errorMessage || task?.error" class="roof-feedback infra-warning" role="alert">{{ errorMessage || task?.error }}</p>
      <p v-else-if="note" class="roof-feedback infra-message" role="status">{{ note }}</p>
    </section>

    <template #results>
      <h2>屋面缺陷</h2>
      <div class="infra-metrics">
        <div><strong>{{ counts.pending }}</strong>待复核</div>
        <div><strong>{{ counts.confirmed }}</strong>已确认</div>
        <div><strong>{{ counts.rejected }}</strong>误报</div>
      </div>
      <label class="roof-filter">按状态筛选
        <select v-model="reviewFilter" class="infra-input"><option value="all">全部缺陷</option><option v-for="(label, key) in reviewLabels" :key="key" :value="key">{{ label }}</option></select>
      </label>
      <div v-if="visibleDefects.length" class="infra-list" aria-label="缺陷记录">
        <article v-for="item in visibleDefects" :key="item.id" class="infra-row roof-defect">
          <img v-if="evidenceUrl(item)" :src="evidenceUrl(item)" alt="缺陷所在画面" loading="lazy" @error="brokenImages.push(item.id)" />
          <div>
            <h3>{{ item.class_name }}</h3>
            <p class="infra-muted">{{ reviewLabels[item.review_status || 'pending'] }}<span v-if="typeof item.confidence === 'number'"> · 识别把握 {{ Math.round(item.confidence * 100) }}%</span></p>
            <div class="roof-defect-actions">
              <button class="infra-button" :disabled="!task?.video_url || typeof item.video_time_ms !== 'number'" @click="jump(item)">回看 {{ formatVideoTime(item.video_time_ms) }}</button>
              <button v-if="item.review_status !== 'confirmed'" class="infra-button" :disabled="!!busy || task?.status === 'running'" @click="review(item, 'confirmed')">确认</button>
              <button v-if="item.review_status !== 'rejected'" class="infra-button" :disabled="!!busy || task?.status === 'running'" @click="review(item, 'rejected')">标为误报</button>
            </div>
          </div>
        </article>
      </div>
      <p v-else class="infra-empty">{{ task?.status === 'running' ? '正在读取画面，发现疑似缺陷后将在这里显示。' : task?.status === 'completed' ? '当前筛选下没有缺陷记录。' : '选择任务并开始检测后，疑似缺陷会显示在这里。' }}</p>
    </template>

    <template #history="{ close }">
      <div v-if="tasks.length" class="infra-list">
        <article v-for="item in tasks" :key="item.id" class="infra-row">
          <div><h3>{{ item.name }}</h3><p class="infra-muted">{{ statuses[item.status] || '待检测' }} · {{ item.defect_count }} 条记录<span v-if="item.created_at"> · {{ item.created_at }}</span></p></div>
          <div class="infra-actions roof-defect-actions">
            <a v-if="item.report_url" class="infra-button" :href="webPath(item.report_url)" target="_blank" rel="noopener">查看原报告</a>
            <button class="infra-button" :disabled="task?.id === item.id" @click="close(); chooseTask(item.id)">{{ task?.id === item.id ? '当前任务' : '打开任务' }}</button>
          </div>
        </article>
      </div>
      <p v-else class="infra-empty">还没有屋顶巡检任务。</p>
    </template>

    <template #dialogs>
      <dialog ref="taskDialog" class="infra-dialog" @close="resetTaskForm" @cancel="busy && $event.preventDefault()">
        <h2>上传屋顶巡检任务</h2>
        <form class="infra-form" @submit.prevent="uploadTask">
          <label>任务名称 <input v-model.trim="name" class="infra-input" placeholder="例如：3 号楼屋顶复查" maxlength="100" /></label>
          <label>巡检视频 <input class="infra-input" type="file" accept=".mp4,.mov,.avi,.mkv,.webm" required @change="videoFile = fileValue($event)" /></label>
          <label>飞行字幕 SRT（可选） <input class="infra-input" type="file" accept=".srt" @change="srtFile = fileValue($event)" /></label>
          <p class="infra-muted">请选用这段录像对应的 SRT，播放时会同步显示坐标和高度。</p>
          <p v-if="dialogError" class="infra-warning" role="alert">{{ dialogError }}</p>
          <footer><button type="button" class="infra-button" :disabled="!!busy" @click="taskDialog?.close()">取消</button><button type="submit" class="infra-button infra-button--primary" :disabled="!videoFile || !!busy">{{ busy === 'task' ? '正在上传' : '上传任务' }}</button></footer>
        </form>
      </dialog>
      <dialog ref="assetDialog" class="infra-dialog" @close="resetModelForm" @cancel="busy && $event.preventDefault()">
        <h2>上传屋顶巡检模型</h2>
        <form class="infra-form" @submit.prevent="uploadModel">
          <p class="infra-muted">请选择训练好的屋顶 YOLO 模型，支持 PT 和 ONNX。</p>
          <details class="infra-muted"><summary>ONNX 导出要求</summary>图像大小 640，关闭内置 NMS。支持单类别检测，或按杂草堆积、屋面脱皮、积水区域排序的三类别检测。</details>
          <label>模型文件 <input class="infra-input" type="file" accept=".onnx,.pt" required @change="modelFile = fileValue($event)" /></label>
          <p v-if="dialogError" class="infra-warning" role="alert">{{ dialogError }}</p>
          <footer><button type="button" class="infra-button" :disabled="!!busy" @click="assetDialog?.close()">取消</button><button type="submit" class="infra-button infra-button--primary" :disabled="!modelFile || !!busy">{{ busy === 'model' ? '正在校验' : '上传模型' }}</button></footer>
        </form>
      </dialog>
    </template>
  </InfrastructureWorkspace>
</template>

<style scoped>
.roof-tools { display: flex; align-items: center; gap: 8px 14px; flex-wrap: wrap; }
.roof-tools > div { margin-right: auto; display: grid; gap: 2px; }
.roof-tools small { color: var(--muted); }
.roof-tools label, .roof-filter { display: flex; align-items: center; gap: 7px; white-space: nowrap; }
.roof-tools select { width: auto; }
.roof-feedback { flex-basis: 100%; }
.roof-feedback progress { vertical-align: middle; width: min(230px, 50%); accent-color: var(--accent); }
.roof-defect { align-items: start; justify-content: start; }
.roof-defect img { flex-shrink: 0; width: 66px; height: 53px; object-fit: cover; border-radius: 4px; }
.roof-defect > div { min-width: 0; }
.roof-defect-actions { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 3px; }
.roof-defect-actions .infra-button { min-height: 30px; padding: 3px 6px; font-size: 13px; }
.roof-video-box { position: absolute; pointer-events: none; border: 2px solid #39d5ab; }
.roof-video-box span { position: absolute; top: 0; left: 0; color: #10251d; background: #8ff6d2; white-space: nowrap; font-weight: 700; padding: 1px 5px; }
</style>
