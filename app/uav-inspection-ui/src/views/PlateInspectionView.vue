<script setup lang="ts">
import { computed, markRaw, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ClipboardList, Film, Layers, Maximize2, Minimize2 } from 'lucide-vue-next'
import { useRoute } from 'vue-router'
import InspectionHeader from '../components/common/InspectionHeader.vue'
import PlaybackRateMenu from '../components/common/PlaybackRateMenu.vue'
import ReplayTransport from '../components/common/ReplayTransport.vue'
import {
  plateApi,
  type AcceptanceData,
  type CoverageData,
  type PlatePathData,
  type ReplayData,
  type ReplayPhase,
} from '../api1/plate'

// 2026-11-20 改版：删除「实时检测」模块（单张图检测工具，与巡检演示主线无关）。
// 「过程回放」与「覆盖质量」原本是两个切换页，现合并为一页四宫格，靠同一时间轴驱动。
// 页内只剩两个视图，不再有回放/覆盖之间的模块切换。
type TabKey = 'result' | 'mission'

const route = useRoute()

const LEGACY_ALIAS: Record<string, TabKey> = { replay: 'mission', coverage: 'mission', detect: 'mission', mission: 'mission', result: 'result' }
const initialView = LEGACY_ALIAS[String(route.query.view ?? '')] ?? 'mission'
const activeTab = ref<TabKey>(initialView)
const tabs: ReadonlyArray<{ key: TabKey; label: string; icon: unknown }> = [
  { key: 'mission', label: '飞行回放', icon: markRaw(Film) },
  { key: 'result', label: '检查结果', icon: markRaw(ClipboardList) },
]
const online = ref<boolean | null>(null)
const acceptance = ref<AcceptanceData | null>(null)
const loadingAcceptance = ref(false)
const replay = ref<ReplayData | null>(null)
const loadingReplay = ref(false)
const replayError = ref('')
const coverage = ref<CoverageData | null>(null)
const loadingCoverage = ref(false)
const videoTime = ref(0)
const playing = ref(false)
const previewCard = ref('')

// 四宫格里的两个视频源：总览用 Gazebo 俯视录屏，其余三格从 RViz 全屏录屏里裁。
// 三格要同时显示同一段录屏的不同区域，只能各挂一个 video 元素（一个元素占一个位置），
// 所以用一个数组收三个实例，同步时统一 follow。
const overviewEl = ref<HTMLVideoElement | null>(null)
const rvizEls = ref<Array<HTMLVideoElement | null>>([null, null])
function bindRviz(index: number) {
  return (el: unknown) => {
    rvizEls.value[index] = (el as HTMLVideoElement | null) ?? null
  }
}

// ---------------- 放大观看 / 播放倍速（2026-11-20 新增） ----------------
/**
 * 四个窗格都能单独放大到整屏。实现不是「再开一份播放器」，而是把同一个 <figure>
 * 用 position:fixed 撑满视口：video 元素还是原来那一个，时间轴不断、播放不中断，
 * 也就不需要让两份视频互相同步（那样必然出漂移，四格又对不上了）。
 */
type CellKey = 'overview' | 'onboard' | 'viewpoint' | 'path'

const zoomCell = ref<CellKey | null>(null)

// 倍速是全局的：四格共用一条时间轴，单格变速会让四格当场错开。
// 四个入口（每格一个 + 放大顶栏一个）改的都是这一个值。
const playbackRate = ref(1)

function openZoom(cell: CellKey) {
  zoomCell.value = cell
}
function closeZoom() {
  zoomCell.value = null
}

/** 把当前倍速应用到所有视频实例（RViz 裁剪格是按需挂载的，得跟着补设） */
function applyRate() {
  const els = [overviewEl.value, ...rvizEls.value]
  for (const el of els) {
    if (el && el.playbackRate !== playbackRate.value) el.playbackRate = playbackRate.value
  }
}
watch(playbackRate, () => applyRate())

// 放大时锁背景滚动。中栏是独立滚动容器，只锁 body 不够，模板里还有一处 overflow 切换。
watch(zoomCell, (cell) => {
  document.body.style.overflow = cell ? 'hidden' : ''
})

function onWindowKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape' && zoomCell.value) closeZoom()
}
onMounted(() => window.addEventListener('keydown', onWindowKeydown))
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onWindowKeydown)
  document.body.style.overflow = ''
})

/**
 * 口径代号 `C_cap` 不上屏（2026-11-21 用户要求）。
 *
 * 生成器 `scripts/gen_acceptance.py` 已改成输出「拍成率」，但当前这份 acceptance.json
 * 是它改之前产出的、且不能重跑：重跑要拿 p10 的 acceptance.json 作 `--geometry` 源，
 * 那份文件已不在（远端 p10 批次目录里只剩视频与日志），重跑会把「违停判定」整张表清空。
 * 所以数据不动，在展示层把代号换成中文——只换字，数字一个不动；新批次由生成器直接出中文，
 * 这个正则届时匹配不到，是幂等的。
 */
function plainCap(text: string) {
  return String(text ?? '').replace(/C_cap\s*=\s*/g, '拍成率 ').replace(/\bC_cap\b/g, '拍成率')
}
/**
 * 标签只删代号、不补词。
 * 「拍成率 C_cap」用 plainCap 会变成「拍成率 拍成率」——实测就是这么翻的车。
 * 句子（触发/结果）里 C_cap 是唯一的量名，才需要换成中文。
 */
function stripCap(text: string) {
  return String(text ?? '').replace(/\s*\bC_cap\b\s*/g, ' ').replace(/\s{2,}/g, ' ').trim()
}
/** 「巡检结果」四张卡：标签与注释都换成不用解释的说法（数字不动，只换词） */
const CARD_LABEL: Record<string, string> = {
  '拍成率': '拍到车牌',
  '车辆认定': '车牌读对',
  '抓拍腿数': '拍摄次数',
  '全程墙钟': '全程用时',
}
/** 数值也换成人能一眼读的单位：1.000 → 100%，1216 s → 20 分 16 秒 */
const CARD_VALUE: Record<string, (v: string) => string> = {
  '拍到车牌': (v) => {
    const n = Number(v)
    return Number.isFinite(n) ? `${Math.round(n * 1000) / 10}%` : v
  },
  '车牌读对': (v) => `${v.replace(/\s*\/\s*/g, ' / ')} 辆`,
  '拍摄次数': (v) => `${v} 次`,
  '全程用时': (v) => {
    const m = v.match(/^(\d+)\s*s$/)
    return m ? fmtDur(Number(m[1])) : v
  },
}
function plainNote(text: string) {
  return plainCap(text)
    .replace(/\s*INSPECT/g, '拍摄')
    .replace(/含重试轮/g, '含补拍')
    .replace(/按\s*spawn 真值表/g, '按标准答案表')
    .replace(/spawn 真值表/g, '标准答案表')
    .replace(/；不一致的车见\s*vehicles\[\]\.match/g, '')
    .replace(/起跑→状态机落盘/g, '从起飞到落地')
}
const summaryCards = computed(() =>
  (acceptance.value?.summary ?? []).map((card) => {
    const raw = stripCap(card.label)
    const label = CARD_LABEL[raw] ?? raw
    return {
      label,
      value: CARD_VALUE[label]?.(card.value) ?? card.value,
      note: plainNote(card.note),
    }
  }),
)
/** 页面上不出现飞行术语：near / far / retry 是机位代号，观众只需要知道「怎么拍的」 */
const LEG_CN: Record<string, string> = { near: '近拍', far: '远拍', retry: '补拍' }
function legCn(leg: string) {
  return LEG_CN[leg] ?? leg
}
function slotCn(slot: string) {
  return slot.replace(/^slot_/, '')
}
/** 补拍原因是日志原话（含 far/near/retry 代号），这里只说结论，原文留 title */
function noteCn(note: string) {
  const m = note.match(/第\s*(\d+)\s*轮/)
  if (m) return `第 ${m[1]} 轮补拍成功`
  return note.replace(/腿读空（被遮挡）/g, '被挡住没拍到')
}
const vehicles = computed(() => acceptance.value?.vehicles ?? [])
const violations = computed(() => acceptance.value?.violations ?? [])
const violatedCount = computed(() => violations.value.filter((item) => item.margin_m < 0).length)
const batchLabel = computed(() => {
  const batch = acceptance.value?.batch
  if (!batch) return '尚未载入巡检批次'
  const state = batch.fsm === 'DONE' ? '已完成' : batch.fsm
  return `${batch.start} 起 · ${state}`
})
// 「巡检结果」与「过程回放」是两份独立 JSON，可能来自不同跑次；各自保留批次号，
// 仅用于顶栏任务编号随当前视图切换，页面上不再做批次差异提示。
const acceptanceRun = computed(() => {
  const dir = (acceptance.value?.batch?.batch_dir ?? '').replace(/[\\/]+$/, '')
  return dir.split(/[\\/]/).pop() ?? ''
})
const replayRun = computed(() => replay.value?.mission?.run ?? '')
/** 顶栏的任务编号跟着当前视图走：结果页用验收批次、回放页用回放松批次，别把 p9 套在 p10 的结果上 */
const viewRun = computed(() => {
  const raw = activeTab.value === 'result'
    ? (acceptanceRun.value || replayRun.value)
    : (replayRun.value || acceptanceRun.value)
  return (raw.split(/[\\/]/).filter(Boolean).pop() ?? '').slice(0, 22)
})
const statusText = computed(() => {
  if (online.value === false) return '服务未连接'
  if (loadingReplay.value || loadingCoverage.value) return '画面载入中'
  return '画面已就绪'
})

async function refreshHealth() {
  try {
    await plateApi.getHealth()
    online.value = true
  } catch {
    online.value = false
  }
}

async function loadAcceptance() {
  loadingAcceptance.value = true
  try {
    const result = await plateApi.getAcceptance()
    acceptance.value = result.data?.available ? result.data : null
  } catch {
    acceptance.value = null
  } finally {
    loadingAcceptance.value = false
  }
}

function showCard(url: string | null) {
  previewCard.value = url || ''
}

// ---------------- 过程回放 / 覆盖质量 ----------------

async function loadReplay() {
  if (loadingReplay.value) return
  loadingReplay.value = true
  replayError.value = ''
  try {
    const result = await plateApi.getReplay()
    replay.value = result.data?.available === false ? null : (result.data ?? null)
    if (!replay.value) replayError.value = '服务端没有找到 timeline.json，回放数据不可用。'
  } catch (err) {
    replay.value = null
    replayError.value = err instanceof Error ? err.message : '回放数据加载失败'
  } finally {
    loadingReplay.value = false
  }
}

async function loadCoverage() {
  if (loadingCoverage.value) return
  loadingCoverage.value = true
  try {
    const result = await plateApi.getCoverage()
    coverage.value = result.data?.available === false ? null : (result.data ?? null)
  } catch {
    coverage.value = null
  } finally {
    loadingCoverage.value = false
  }
}

// 2026-11-20：第四格改成俯视路径图，数据来自 /plate/path（从任务日志 GOTO 序列重建）。
const pathData = ref<PlatePathData | null>(null)
const loadingPath = ref(false)

async function loadPath() {
  if (loadingPath.value) return
  loadingPath.value = true
  try {
    const result = await plateApi.getPath()
    pathData.value = result.data?.steps?.length ? result.data : null
  } catch {
    pathData.value = null
  } finally {
    loadingPath.value = false
  }
}

function fmtTime(sec: number) {
  const total = Math.max(0, Math.round(sec))
  const m = Math.floor(total / 60)
  return `${m}:${String(total % 60).padStart(2, '0')}`
}

// ---------------- 四宫格：两路录屏 + 数据化路径点 ----------------

/**
 * 分区坐标（1920×1080 帧，像素实测，非目测）。
 *
 * 两路录屏的分辨率口径不同，别混用：
 *   - watch_raw.mp4（机载/深度/相机三格）仍是 1280×720，按 1.6 取中；
 *   - rviz_raw.mp4（「视点与位姿」一格）是 2560×1440 屏幕 x11grab 后 scale 到 1920×1080。
 *
 * 2026-11-21 重录后重测的 RViz 分区：RViz 窗口铺满整屏（0,0,2560×1440），
 * 缩放 0.75 落到 1920×1080 后 —— 左 dock 右边界 x=441，三维视口 x441..1907、y48..980，
 * 底部 y980 以下是 RViz 状态栏（ROS Time / Wall Elapsed 那行），不能入画。
 * 重录前的旧口径是 x477..1920、y47..984，因为当时窗口没铺满、左边多让了 36 px。
 */
const RVIZ_W = 1920
const RVIZ_H = 1080
const CELL_RATIO = 2.2

const RV_SLOTS = {
  // 2026-11-21 晚重录：rviz_process.mp4 不再是「整屏 rviz 直录」，而是四宫格合成片
  // （左上 Displays、右上三维视口、左下彩色、右下深度，四格各 960×540，统一裁成 16:9，
  //   不含工具栏与状态栏）。所以下面两组坐标对应合成片的象限，不再依赖停靠面板的实际像素位置。
  // 左下象限（彩色）：cell 比例 2.2 → 960/2.2 = 436 高，纵向居中 y = 540 + (540-436)/2 = 592。
  onboard: { x: 0, y: 592, w: 960, h: 436 },
  // 右上象限（三维视口）：同样 960 宽、436 高，纵向居中 y = (540-436)/2 = 52。
  // 四宫格里的三维视口已按整场取景（Yaw 转 90°、Distance 38），场地横放，与「总览视角」同向。
  viewpoint: { x: 960, y: 52, w: 960, h: 436 },
} as const

/**
 * 把「原视频中的一块矩形」铺进一个固定 16:10 的容器：
 * 以容器宽为 1 个单位推缩放，再换算成 CSS 百分比（width/left 相对容器宽，height/top 相对容器高）。
 * 裁剪区比例与 16:10 不完全一致，四边最多裁掉约 4%，换取四格等高不错位。
 */
function cropStyle(box: { x: number; y: number; w: number; h: number }) {
  const s = 1 / box.w
  const vidW = RVIZ_W * s
  const vidH = RVIZ_H * s
  const cellH = 1 / CELL_RATIO
  const left = -box.x * s
  const top = cellH / 2 - (box.y + box.h / 2) * s
  return {
    width: `${vidW * 100}%`,
    height: `${(vidH / cellH) * 100}%`,
    left: `${left * 100}%`,
    top: `${(top / cellH) * 100}%`,
    // 必须写进 inline style：Tailwind preflight 给 video 定了 max-width:100%，
    // 单靠 max-w-none 这个类在这套构建里压不住，实测 computed 仍是 100%，
    // 会把 403% 的宽度压回容器宽，画面变成整幅而不是裁出的一个窗口。
    maxWidth: 'none',
    maxHeight: 'none',
    objectFit: 'fill' as const,
  }
}
// ---------------- 第四格：俯视路径图（数据来自任务日志的 GOTO 序列） ----------------
// 2026-11-20：原本这里是 RViz 点云全景，与第三格内容重叠；改成按日志重建的路径图。
// 坐标口径见后端 /plate/path 的 docstring：front/* 按车轴前推 d 米复算，transit/* 取车坐标。
const PATH_SCALE = 10 // 1 m = 10 个 SVG 单位
const pathGeom = computed(() => {
  const d = pathData.value
  if (!d || !d.steps.length) return null
  const { x0, x1, y0, y1 } = d.area
  const w = (x1 - x0) * PATH_SCALE
  const h = (y1 - y0) * PATH_SCALE
  const px = (x: number) => (x - x0) * PATH_SCALE
  const py = (y: number) => (y1 - y) * PATH_SCALE // ROS 的 y 向上，SVG 向下，这里翻转
  const cars = d.cars.map((c) => ({ ...c, cx: px(c.x), cy: py(c.y), deg: (-c.yaw * 180) / Math.PI }))
  const steps = d.steps.map((s, idx) => ({ ...s, idx, cx: px(s.x), cy: py(s.y) }))
  return { w, h, cars, steps, x0, x1, y0, y1 }
})

const pathNow = computed(() => videoTime.value + (replay.value?.video.offset_s ?? 0))
const pathCursor = computed(() => {
  const g = pathGeom.value
  if (!g) return -1
  let last = -1
  for (let i = 0; i < g.steps.length; i += 1) {
    if (g.steps[i].t <= pathNow.value) last = i
    else break
  }
  return last
})
const pathWalked = computed(() => {
  const g = pathGeom.value
  const c = pathCursor.value
  if (!g || c < 0) return ''
  return g.steps.slice(0, c + 1).map((s) => `${s.cx.toFixed(1)},${s.cy.toFixed(1)}`).join(' ')
})
const pathAll = computed(() => {
  const g = pathGeom.value
  if (!g) return ''
  return g.steps.map((s) => `${s.cx.toFixed(1)},${s.cy.toFixed(1)}`).join(' ')
})
const pathActive = computed(() => {
  const g = pathGeom.value
  const c = pathCursor.value
  return g && c >= 0 ? g.steps[c] : null
})
/** 第一个航点之前还在起飞准备，别显示成「第 0 步」 */
const pathStepLabel = computed(() =>
  pathCursor.value < 0 ? '待起飞' : `第 ${pathCursor.value + 1} 步`)

/** 每 5 m 一条网格线，给俯视图一个尺度参照 */
const pathGrid = computed(() => {
  const g = pathGeom.value
  if (!g) return { x: [] as number[], y: [] as number[] }
  const xs: number[] = []
  const ys: number[] = []
  for (let m = Math.ceil(g.x0 / 5) * 5; m <= g.x1; m += 5) xs.push((m - g.x0) * PATH_SCALE)
  for (let m = Math.ceil(g.y0 / 5) * 5; m <= g.y1; m += 5) ys.push((g.y1 - m) * PATH_SCALE)
  return { x: xs, y: ys }
})

const onboardStyle = cropStyle(RV_SLOTS.onboard)
const viewpointStyle = cropStyle(RV_SLOTS.viewpoint)

/**
 * 放大态的覆盖样式：按裁剪区的宽度定标，高度按 16:9 自己推出，再用 translate 把
 * 裁剪区中心对到容器中心。
 *
 * 不能沿用 cropStyle：那套 left/top/width/height 全是百分比，width 相对容器宽、
 * height 相对容器高，只有容器比例恰好等于裁剪区比例时画面才不变形；整屏放大后
 * 容器变成视口比例，直接套会横向压扁。
 *
 * 这里的关键是 translate 的百分比以**元素自身尺寸**为基准（不是容器），
 * 所以「视频宽度 = k 倍容器宽 → 居中偏移」可以完全脱离容器比例计算。
 */
function zoomCropStyle(box: { x: number; y: number; w: number; h: number }) {
  const k = RVIZ_W / box.w // 视频宽度是容器宽的多少倍
  const nx = (box.x + box.w / 2) / RVIZ_W // 裁剪区中心在整幅里的相对位置
  const ny = (box.y + box.h / 2) / RVIZ_H
  return {
    position: 'absolute' as const,
    left: '50%',
    top: '50%',
    width: `${k * 100}%`,
    height: 'auto',
    // 竖屏时「按宽度定标」算出的高度会小于容器高，上下露黑边；
    // minHeight 兜底 + object-fit: cover 让内容始终等比铺满，不拉伸。
    minHeight: '100%',
    objectFit: 'cover' as const,
    aspectRatio: `${RVIZ_W} / ${RVIZ_H}`,
    transform: `translate(${(-nx * 100).toFixed(2)}%, ${(-ny * 100).toFixed(2)}%)`,
    maxWidth: 'none',
    maxHeight: 'none',
  }
}

const onboardZoomStyle = zoomCropStyle(RV_SLOTS.onboard)
const viewpointZoomStyle = zoomCropStyle(RV_SLOTS.viewpoint)

const overviewOffset = computed(() => replay.value?.video.offset_s ?? 0)
const rvizOffset = computed(() => coverage.value?.rviz?.offset_s ?? overviewOffset.value)

/**
 * RViz 录屏比总览晚起（p11 那批：总览 8.3 s 起、RViz 174 s 起）时，回放前两分多钟
 * 这一格是空的。冻在第 0 帧容易被当成「坏了」，所以这段时间盖一条说明告诉用户
 * 它什么时候才开始。墙钟口径与 followOverview 同一套：wall = 视频秒 + 起点偏移。
 */
const rvizBeforeStart = computed(
  () => videoTime.value + overviewOffset.value < rvizOffset.value,
)

/**
 * 按墙钟对齐两段录屏：wall = 视频秒 + 录屏起点偏移，两路 wall 相等即同一时刻。
 *
 * 两段录屏的起点可以差很多：p12 那批总览 209.3 s、RViz 209.3 s，差 0.3 s；
 * p11 那批总览 8.3 s、RViz 174 s，差 165.7 s。差值为正时 target 会跑到负数 ——
 * 那一段总览时间里 RViz 根本还没开录。
 *
 * 2026-11-21 修（用户报「第二格第三格一直在抽搐」）：
 *   旧写法只有 `b.currentTime = Math.max(0, target)`，target<0 时等于每来一次
 *   timeupdate 就把 currentTime 拽回 0；而 rviz 两格同时在往前播，播出去不到 0.35 s
 *   又被拽回来 —— 于是反复从第 0 帧重开，看上去就是一直在抽搐。
 *   现在把「目标落在录屏区间之外」当成一种正常状态：钉在边界帧上、并且暂停播放，
 *   只在目标真正落进 [0, duration] 之后才跟着播。
 */
function followOverview() {
  const a = overviewEl.value
  if (!a) return
  const target = a.currentTime + (overviewOffset.value - rvizOffset.value)
  if (!Number.isFinite(target)) return
  // 容差随倍速放宽：4× 时 timeupdate 的间隔没变、每次前进的秒数却是 4 倍，
  // 还用 0.35 s 会因两路解码速度微差而反复纠偏，画面一顿一顿。
  const tol = 0.35 * Math.max(1, playbackRate.value)
  for (const b of rvizEls.value) {
    if (!b) continue
    const dur = Number.isFinite(b.duration) && b.duration > 0 ? b.duration : Infinity
    const clamped = Math.min(Math.max(0, target), dur)
    // 只在偏差超过容差时才 seek。相等或已在容差内就不动它，避免每拍都触发一次跳转。
    if (Math.abs(b.currentTime - clamped) > tol) b.currentTime = clamped
    // 录屏还没开始（target<0）或已经播完（target>=dur）时，这一格没有内容可播，必须暂停，
    // 否则播放位置会一直往前跑、再被拽回来，就是抽搐的根源。
    const inRange = target >= 0 && target < dur
    const shouldPlay = playing.value && inRange
    if (shouldPlay) {
      if (b.paused) void b.play().catch(() => undefined)
    } else if (!b.paused) {
      b.pause()
    }
  }
}

function onVideoTime(e: Event) {
  const el = e.target as HTMLVideoElement
  videoTime.value = el.currentTime
  // RViz 裁剪格是按需挂载的，挂载那一刻会回落到 1×；每帧顺手补设一次，代价可忽略。
  if (el.playbackRate !== playbackRate.value) applyRate()
  followOverview()
}

/**
 * 录屏开头几秒 RViz 还在初始化，三格会全是黑块。元数据到手后直接跳到第 5 秒，
 * 保证第一屏就有画面；只在用户还没动过进度时跳，避免覆盖手动定位。
 */
function onOverviewReady() {
  const el = overviewEl.value
  if (!el) return
  applyRate()
  if (el.currentTime < 1 && (el.duration || 0) > 6) {
    el.currentTime = 5
    videoTime.value = 5
    followOverview()
  }
}

function onPlayState(e: Event) {
  const a = e.target as HTMLVideoElement
  playing.value = !a.paused
  if (playing.value) applyRate()
  // 播/停交给 followOverview 统一裁决：它还要看这一格的目标时刻是否落在录屏区间内，
  // 这里直接 play() 会把「还没开录」的那一段也拉起来播，然后被纠偏拽回去。
  followOverview()
}

function togglePlay() {
  const a = overviewEl.value
  if (!a) return
  if (a.paused) void a.play().catch(() => undefined)
  else a.pause()
}

function seekToPhase(ph: ReplayPhase) {
  const el = overviewEl.value
  if (!el) return
  const target = Math.max(0, ph.t0 - overviewOffset.value)
  el.currentTime = target
  videoTime.value = target
  followOverview()
  void el.play().catch(() => undefined)
}

// 进度条拖动：以总览为主时钟，三格跟随（与 timeupdate 同一套对齐逻辑）
function seekTo(t: number) {
  const el = overviewEl.value
  if (!el || !Number.isFinite(t)) return
  el.currentTime = t
  videoTime.value = t
  followOverview()
  applyRate()
}

const phaseCursor = computed(() => {
  const phases = replay.value?.phases ?? []
  const wall = videoTime.value + overviewOffset.value
  for (let i = 0; i < phases.length; i += 1) {
    if (wall >= phases[i].t0 && wall < phases[i].t1) return i
  }
  return phases.length ? phases.length - 1 : -1
})

const currentPhase = computed(() => replay.value?.phases?.[phaseCursor.value] ?? null)

// 2026-11-21：PHASE_TEXT / phaseAction / phaseResult 三件套随「当前阶段」卡一起删除。
// 那张卡没了，阶段名与进度改由视频下方的阶段条承担（见 phaseLabel / phaseDetail），
// 留着这三个常量会被 noUnusedLocals 拦，也会让「这一段在做什么」出现两个版本。

/**
 * 阶段文案统一（2026-11-21 用户要求）。
 *
 * timeline.json 里的 label 是跑批侧的技术名（「SURVEY 巡航发现」「INSPECT 第 1 轮」），
 * 直接展示给领导看像日志。这里按 key 映射成四个中文阶段名，**不依赖重新生成 JSON**——
 * 换批次、换数据源都仍然是这四句。key 缺失时退回「阶段N + 原 label」。
 */
const PHASE_LABELS: Record<string, string> = {
  survey: '阶段一：巡航发现',
  inspect_r1: '阶段二：逐车拍摄',
  inspect_r2: '阶段三：缺口补拍',
  wrap: '阶段四：收尾降落',
}
const CN_NUM = ['一', '二', '三', '四', '五', '六']
function phaseLabel(ph: ReplayPhase | null | undefined, index: number) {
  if (!ph) return '—'
  return PHASE_LABELS[ph.key] ?? `阶段${CN_NUM[index] ?? index + 1}：${ph.label}`
}
const currentPhaseLabel = computed(() =>
  phaseLabel(currentPhase.value, Math.max(0, phaseCursor.value)))

/**
 * 阶段副标题（那一行小字）也换成口语。
 * 原文是跑批侧写的：「发现 7 条（400 s）」「停 stage1、清场」——「条」和「stage1」都要解释一遍。
 * 数字仍从原文里抠，换批次自动跟着变；匹配不上就原样返回，不丢信息。
 */
function phaseDetail(ph: ReplayPhase) {
  const d = String(ph.detail ?? '')
  const found = d.match(/^发现 (\d+) 条（(\d+) s）/)
  if (found) return `发现 ${found[1]} 辆车，用时 ${fmtDur(Number(found[2]))}`
  if (/^停 stage1、清场/.test(d)) return '停掉巡航节点、清场收工'
  return d
}

/** 当前播放位置换算成墙钟秒（视频时间 + 录屏起点偏移），阶段区间用的是墙钟 */
const wallNow = computed(() => videoTime.value + overviewOffset.value)

/** 某一段阶段已走完多少（0..1）。边界用闭区间夹紧，避免最后一帧算成 0.999 不动。 */
function phaseFrac(ph: ReplayPhase) {
  const span = Math.max(1, ph.t1 - ph.t0)
  return Math.min(1, Math.max(0, (wallNow.value - ph.t0) / span))
}

/** 整轮总进度：首段起点 → 末段终点 */
const overallProgress = computed(() => {
  const phases = replay.value?.phases ?? []
  if (!phases.length) return 0
  const t0 = phases[0].t0
  const t1 = phases[phases.length - 1].t1
  return Math.min(1, Math.max(0, (wallNow.value - t0) / Math.max(1, t1 - t0)))
})

/** 当前阶段剩余时间（秒），给「还需约 N 分」用 */
const phaseRemain = computed(() => {
  const ph = currentPhase.value
  if (!ph) return 0
  return Math.max(0, ph.t1 - wallNow.value)
})

function fmtDur(sec: number) {
  const total = Math.max(0, Math.round(sec))
  const m = Math.floor(total / 60)
  const s = total % 60
  if (m <= 0) return `${s} 秒`
  return s ? `${m} 分 ${s} 秒` : `${m} 分钟`
}

// ---------------- 关键事件：技术串 → 科普式人话（2026-11-21） ----------------
/**
 * timeline.json 的 events 是跑批日志原文（「SURVEY -> SCHEDULE 发现 7 条（新增 7，累计 7）」），
 * 直接摆给领导看等于把日志贴墙上。这里按句式匹配翻译成一句人话，**数字从原文里抠出来**，
 * 不写死 7 辆 / 2 轮，换批次自动跟着变。
 *
 * 为什么放在前端而不是重新生成 JSON：这批数据是已验收批次（C_cap 1.000），
 * 为了改措辞去重跑一次不值得；匹配不上时退回原文并去掉路径，不会丢信息。
 */
const PATH_RE = /[｜|]\s*\/[^\s｜|]+|\s*（\/[^）]+）|\s*\/home\/\S+/g

function cleanRaw(text: string) {
  return text.replace(PATH_RE, '').replace(/\s{2,}/g, ' ').trim()
}

function narrate(ev: { t: number; kind: string; text: string }) {
  const s = ev.text
  let m: RegExpMatchArray | null

  if (/^开始：/.test(s)) {
    return { title: '起飞巡检', text: '无人机从起降点起飞，开始这一轮的自主巡检。' }
  }
  if ((m = s.match(/^SURVEY 完成：发现 (\d+) 条/))) {
    return { title: '巡航发现完成', text: `沿弓字航线扫完整个场区，一共找到 ${m[1]} 辆停着的车。` }
  }
  if (/^SURVEY\s*->\s*SCHEDULE/.test(s)) {
    return { title: '整理待拍清单', text: '把刚找到的车排成一张待拍清单，准备逐车拍摄。' }
  }
  if ((m = s.match(/^SCHEDULE\s*->\s*INSPECT\s*待拍 (\d+)/))) {
    return { title: '开始逐车拍摄', text: `清单上 ${m[1]} 辆车，按顺序一辆一辆飞过去拍。` }
  }
  if ((m = s.match(/遮挡法：spawn 挡板.*（(car_\d+)）/))) {
    return {
      title: '人为设置障碍',
      text: `给 ${m[1]} 盖上一块挡板，模拟车牌被挡住的情况——这是故意加的难点，用来验证漏拍之后系统能不能自己补上。`,
    }
  }
  if ((m = s.match(/遮挡法：delete 挡板.*（(car_\d+)）/))) {
    return { title: '移除障碍', text: `把 ${m[1]} 上的挡板拿掉，再补拍一次。` }
  }
  if ((m = s.match(/^INSPECT 第 (\d+) 轮：(\d+) 辆/))) {
    return { title: `第 ${m[1]} 轮拍摄开始`, text: `这一轮要给 ${m[2]} 辆车拍照。` }
  }
  if ((m = s.match(/^INSPECT 第 (\d+) 轮结束/))) {
    return { title: `第 ${m[1]} 轮拍摄结束`, text: '这一轮飞完了，开始检查拍全没有。' }
  }
  if ((m = s.match(/^INSPECT 第 (\d+) 轮产物：.*拍成 (\d+) 辆/))) {
    return { title: '本轮结果', text: `这一轮拍成了 ${m[2]} 辆。` }
  }
  if ((m = s.match(/重拍漏掉的 (\d+) 辆/))) {
    return {
      title: '自动判定需要补拍',
      text: `发现还有 ${m[1]} 辆车没拍全，系统自己决定只补这几辆，不重飞全场。`,
    }
  }
  if ((m = s.match(/达标（(\d+)\/(\d+)）/))) {
    return { title: '全部拍全', text: `${m[1]}/${m[2]} 辆全部拍到并认清，任务达标。` }
  }
  if (/^收尾：降落/.test(s)) {
    return { title: '收尾降落', text: '拍摄完成，无人机返航、降落并上锁，任务结束。' }
  }
  return { title: ev.kind === 'fsm' ? '状态推进' : '过程记录', text: cleanRaw(s) || '—' }
}

/**
 * 车名 → 车牌号，给日志里的人话补一句「哪辆车」。
 *
 * 以验收结果为准，path.json 的 plate 只作兜底：path.json 的车辆表抄自
 * inspect_r*.yaml（起飞前的 spawn 清单），那份清单的车牌不一定与实车一致。
 * 2026-11-21 那批实测：car_0 的 yaml 写 皖AB3618，实车、取证缩略图与验收结果
 * 都是 皖BXH618（其余 6 辆两边一致）。验收结果与卡片同源，故以它覆盖。
 */
const carPlate = computed(() => {
  const m: Record<string, string> = {}
  for (const c of pathData.value?.cars ?? []) m[c.name] = c.plate
  for (const v of acceptance.value?.vehicles ?? []) {
    if (v.car && v.plate) m[v.car] = v.plate
  }
  return m
})

/** 补拍原因（VP 原文）→ 人话。匹配不上时退回中性说法，不硬编原因。 */
function whyPlain(why?: string) {
  if (why && why.includes('格式不合法')) return '前两张读出来的字符拼不成真车牌'
  if (why && why.includes('证据不足')) return '前两张读得不够一致，系统不敢下结论'
  return '前两张没能确认车牌'
}

/**
 * 逐腿航段 → 人话（2026-11-21）。
 *
 * 为什么要塞进「关键事件」：timeline.json 只有 15 条里程碑，其中 6 条挤在起跑后
 * 404 s 内、下一条要到 1094 s —— 中间 11 分钟一条都不出，看着就像「日志没跟视频走」。
 * 而 path.json 的 43 条航段本来就带任务秒，和第四格同一份数据、同一条时间轴，
 * 每条翻译成一句「现在在干什么」，日志才真的随播放推进（平均 20 s 一条）。
 *
 * 放在前端合并而不是重生成 timeline.json：两份数据都已是验收批次，为改措辞重跑不值得。
 */
const stepEvents = computed(() => {
  const steps = pathData.value?.steps ?? []
  if (!steps.length) return []
  const plate = carPlate.value
  const who = (car: string) => (plate[car] ? `${car}（${plate[car]}）` : car)
  let nOverview = 0
  return steps.map((s) => {
    const dist = `${s.d.toFixed(2)} m`
    let title: string
    let text: string
    switch (s.leg) {
      case 'overview/all':
        nOverview += 1
        title = nOverview === 1 ? '起飞巡航' : '再绕一圈'
        text = nOverview === 1
          ? '从起降点起飞，先绕场一圈，把整个停车场扫一遍。'
          : '补拍前再扫一遍，确认场区里的车没有变化。'
        break
      case 'transit/overview_in':
        title = '回场中转'
        text = '收拢队形，回到场区中央，准备下一轮。'
        break
      case 'transit/cross_rise':
        title = '转场爬升'
        text = '先升到安全高度，再横着飞到下一辆车上方——不在车位之间低空穿行，免得蹭到车。'
        break
      case 'transit/in':
        title = '进入拍摄位'
        text = `对准 ${who(s.car)} 的车头，降到拍摄高度。`
        break
      case 'transit/retry_rise':
        title = '拉回补拍起点'
        text = `${who(s.car)} 还没确认，先退到同一条视线上更远一点的位置。`
        break
      case 'transit/retry_in':
        title = '调整补拍角度'
        text = '横着偏一点再进近：原地重拍只会得到同样的错值，换个角度才有新证据。'
        break
      case 'front/far':
        title = '远距离拍一张'
        text = `在 ${dist} 外拍：整个车牌都在画面里，不会切边。`
        break
      case 'front/near':
        title = '凑近再拍一张'
        text = `降到 ${dist} 再拍：字更大，相邻字符更好分辨。`
        break
      case 'front/retry':
        title = '补拍一张'
        text = `${who(s.car)}：${whyPlain(s.why)}，所以换到 ${dist}、角度也偏开一点再拍。`
        break
      default:
        title = '航段推进'
        text = `${who(s.car)} · ${s.leg}`
    }
    return { t: s.t, title, text }
  })
})

/** 已经发生的事件（按时间倒序，最新的在最上面），第一条就是「正在发生」 */
const narratedPast = computed(() => {
  const events = replay.value?.events ?? []
  return [
    ...events.map((ev) => ({ t: ev.t, ...narrate(ev) })),
    ...stepEvents.value,
  ]
    .filter((ev) => ev.t <= wallNow.value + 0.5)
    .sort((a, b) => a.t - b.t)
    .reverse()
})

/** 日志总条数：里程碑 + 航段，进度分母跟着一起变 */
const narratedTotal = computed(() => (replay.value?.events.length ?? 0) + stepEvents.value.length)

/** 任务结果卡的通俗数字（2026-11-21：清单/补拍轮次/墙钟 全部改成人话） */
const resultPlain = computed(() => {
  const k = replay.value?.kpi
  const rounds = k?.rounds ?? []
  const first = rounds[0]
  const later = rounds.slice(1).reduce((sum, r) => sum + r.cars, 0)
  return {
    done: k?.final === 'DONE' ? '完成' : (k?.final ?? '—'),
    // 清单/拍成 → 大字块只放数字，句子放下面（右栏 266 px，塞不进一行）
    carsShort: k ? `${k.n_captured} / ${k.n_cars} 辆` : '—',
    // 清单/拍成 → 「需拍 7 辆，已拍成 7 辆」
    cars: k ? `需拍 ${k.n_cars} 辆，已拍成 ${k.n_captured} 辆` : '—',
    // 补拍轮次 → 「一共飞了 2 轮：首轮拍成 6 辆，第 2 轮补上最后 1 辆」
    // 注意 rounds 是 INSPECT 的**总**轮数，不是补拍轮数：2 轮 = 1 轮首拍 + 1 轮补拍。
    // 写成「补了 2 轮」会把总轮数当补拍数，实测就会被领导问「哪来的两轮补拍」。
    retry: !rounds.length
      ? '—'
      : rounds.length === 1
        ? `一轮就拍成 ${first?.captured ?? 0} 辆，没有补拍`
        : `一共飞了 ${rounds.length} 轮：首轮拍成 ${first?.captured ?? 0} 辆，第 ${rounds.length} 轮补上最后 ${later} 辆`,
    // 用时不再单独出一行：预算条下面已经有「已用 / 上限」，两处写同一句话是重复。
  }
})

const coverageRviz = computed(() => coverage.value?.rviz ?? null)
const rvizFallback = computed(() => Boolean(coverageRviz.value?.fallback))

/**
 * 默认演示批次没有 RViz 分屏录屏（那份 1920×1080 四宫格合成片只随实机数据交付），
 * 后端会回退到主回放视频并打上 fallback 标记。此时**不能**再套 RV_SLOTS 的四象限
 * 裁剪：坐标是按合成片量的，套在 854×480 的单幅俯视画面上只会把画面裁飞。
 *
 * 改成整幅铺满、两格各取不同局部（轻微放大 + 不同 object-position），既与主时钟
 * 同步，又不会两格长得一模一样。真实 rviz 数据到位后自动回到原来的裁剪逻辑。
 */
function rvizStyle(index: 0 | 1, zoom: boolean) {
  if (rvizFallback.value) {
    const origin = index === 0 ? '28% center' : '72% center'
    return {
      position: 'absolute' as const,
      inset: '0',
      width: '100%',
      height: '100%',
      objectFit: 'cover' as const,
      objectPosition: origin,
      transform: zoom ? 'none' : 'scale(1.35)',
      transformOrigin: origin,
      maxWidth: 'none',
      maxHeight: 'none',
    }
  }
  return index === 0
    ? (zoom ? onboardZoomStyle : onboardStyle)
    : (zoom ? viewpointZoomStyle : viewpointStyle)
}
// 2026-11-21：「分排与车位」整块已删，coverageRows / coverageSlots 不再上屏，
// 随之删掉（noUnusedLocals 会拦）。coverage 本身还要留着——四宫格第二、三格
// 的 RViz 录屏 src 来自 coverage.rviz。
/**
 * 预算条用的秒数：优先任务自身的 elapsed，退回脚本墙钟。
 *
 * 2026-11-21：同一屏两处都写「墙钟」，左上批次卡 1227 s、预算条 20:49（=1249 s），
 * 差 22 s。差的正是 launch 起 ROS 节点到 FSM 起跑的那段——脚本墙钟把准备时间也算进去了，
 * 而预算说的是「任务跑了多久」。验收报告的墙钟取的就是 elapsed，所以两处统一到这里。
 */
const budgetSpent = computed(() => {
  const b = replay.value?.budget
  if (!b) return 0
  return b.elapsed_s || b.wall_clock_s || 0
})
const budgetUsedPct = computed(() => {
  const b = replay.value?.budget
  if (!b || !b.budget_s) return 0
  return Math.min(100, Math.round((budgetSpent.value / b.budget_s) * 100))
})

watch(activeTab, (tab) => {
  if (tab !== 'mission') return
  if (!replay.value && !loadingReplay.value) void loadReplay()
  if (!coverage.value && !loadingCoverage.value) void loadCoverage()
  if (!pathData.value && !loadingPath.value) void loadPath()
}, { immediate: true })

// 从首页再次点入口而当前已经在本页时，query 变化要能切过去（含旧参数 replay / coverage）
watch(() => route.query.view, (view) => {
  const next = LEGACY_ALIAS[String(view ?? '')]
  if (next) activeTab.value = next
})

onMounted(async () => {
  await refreshHealth()
  await loadAcceptance()
})

// ---------------- 三栏工作台：批次 KPI / 左下角页内导航 ----------------
const stageTitle = computed(() => tabs.find((tab) => tab.key === activeTab.value)?.label ?? '巡检过程')
// 优先用回放的批次 KPI（整轮最权威的口径）；回放没载入时退回巡检结果的车辆清单
/**
 * 左栏「自动巡航与补拍」说明卡。
 *
 * 三步对应真实状态机（SURVEY → SCHEDULE → INSPECT → DONE，见 platedata/timeline.json
 * 的 events 字段）：巡航落一份待拍清单、按清单逐车拍、按认定率缺口补拍。
 * 这里只讲流程，辆数与时长交给下方阶段条与上方 KPI，避免同一件事在页面上写两遍数字。
 */
const FLOW_STEPS = [
  { title: '先飞一圈看全场', desc: '沿弓字形航线扫一遍，用深度相机边走边画出场地地图，找出车位和车牌在哪。' },
  { title: '再一辆辆拍照', desc: '按名单逐个靠近，每辆车从远、近、补三个角度各拍一张。' },
  { title: '漏了才补拍', desc: '只补没拍成功的那几辆，不重新飞全场。' },
]


const kpi = computed(() => {
  const nCars = replay.value?.kpi.n_cars ?? vehicles.value.length
  const nCaptured = replay.value?.kpi.n_captured ?? vehicles.value.filter((v) => Boolean(v.plate && v.plate.trim())).length
  const violated = violatedCount.value
  const run = (replay.value?.mission.run || acceptanceRun.value || '').trim()
  return {
    nCars,
    nCaptured,
    violated,
    pending: Math.max(0, nCars - nCaptured),
    wallText: acceptance.value?.summary.find((row) => row.label.includes('墙钟'))?.value
      ?? (budgetSpent.value ? `${budgetSpent.value} s` : '—'),
    runShort: run ? (run.split(/[\\/]/).filter(Boolean).pop() ?? '').slice(0, 22) : '',
    rate: nCars ? Math.round((nCaptured / nCars) * 100) : 0,
  }
})
/** 顶部那句结论要能在不看任何表格的情况下说清结果 */
const wallHuman = computed(() => {
  const raw = acceptance.value?.summary.find((row) => row.label.includes('墙钟'))?.value ?? ''
  const m = raw.match(/^(\d+)\s*s$/)
  return m ? fmtDur(Number(m[1])) : (raw || '—')
})
const verdictLine = computed(() => {
  const total = kpi.value.nCars
  if (!total) return ''
  const captured = kpi.value.nCaptured
  const bad = violatedCount.value
  const good = Math.max(0, total - bad)
  const head = captured >= total
    ? `${total} 辆车的车牌全部拍到、全部读对`
    : `${total} 辆车里拍清并读对 ${captured} 辆`
  return `这一趟飞了 ${wallHuman.value}，${head}；停车方面 ${bad} 辆压线、${good} 辆停得正。`
})

// 环形图三段必须互斥：已认定且未越界 / 已认定但越界 / 未认定。
// 早前直接把「已认定 7」与「越界 4」拼成两段，合计 11 > 总数 7，角度溢出被浏览器裁掉。
const ring = computed(() => {
  const total = Math.max(1, kpi.value.nCars)
  const flagged = new Set(violations.value.filter((row) => row.margin_m < 0).map((row) => row.car))
  const captured = new Set(vehicles.value.filter((row) => Boolean(row.plate && row.plate.trim())).map((row) => row.car))
  const both = [...captured].filter((car) => flagged.has(car)).length
  const clean = Math.max(0, captured.size - both)
  const miss = Math.max(0, kpi.value.nCars - captured.size)
  const a = (clean / total) * 360
  const b = ((clean + both) / total) * 360
  return { clean, both, miss, gradient: `conic-gradient(#2563eb 0 ${a}deg, #f43f5e ${a}deg ${b}deg, #e2e8f0 ${b}deg 360deg)` }
})
</script>

<template>
  <div class="relative min-h-screen overflow-x-hidden bg-slate-50 text-slate-800 xl:flex xl:h-screen xl:min-h-0 xl:flex-col xl:overflow-hidden">
    <div class="relative z-10 w-full shrink-0 px-4 pt-4 md:px-5">
      <InspectionHeader title="无人机车牌巡检" :task-name="batchLabel" task-label="本批任务" :online="online">
        <template #actions>
          <!-- 2026-11-21：删除「刷新数据」按钮（数据在进入页面与切换视图时自动加载），
               这个位置改放「巡检过程 / 巡检结果」切换；左栏底部原来那块「页内视图」随之删除。 -->
          <div class="flex items-center gap-1 rounded-lg border border-slate-200 bg-slate-100 p-1">
            <button
              v-for="tab in tabs"
              :key="tab.key"
              type="button"
              class="inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition"
              :class="activeTab === tab.key ? 'bg-white text-indigo-700 shadow-sm' : 'text-slate-600 hover:text-slate-900'"
              @click="activeTab = tab.key"
            >
              <component :is="tab.icon" :size="15" :class="activeTab === tab.key ? 'text-indigo-600' : 'text-slate-400'" />
              {{ tab.label }}
            </button>
          </div>
        </template>
      </InspectionHeader>
    </div>

    <!-- 2026-11-21 压缩纵向：三栏间距 gap-3 → gap-2，页面上下留白 pt-3/pb-6 → pt-2/pb-3，
         目标是 1440×900 与 1920×1080 都不需要上下滚动（四宫格按 11:5 撑高，是最占高度的一块）。 -->
    <main class="relative z-10 grid grid-cols-1 gap-2 px-4 pb-3 pt-2 md:px-5 xl:min-h-0 xl:flex-1 xl:grid-cols-[232px_minmax(0,1fr)_266px] xl:overflow-hidden">
      <!-- ===== 左栏：批次遥测 → 认定分布 → 左下角功能坞 ===== -->
      <aside class="order-2 flex min-h-0 flex-col gap-2 xl:order-none xl:overflow-y-auto xl:pr-0.5">
        <section class="rounded-lg border border-slate-200 bg-white p-3">
          <div class="mb-3 flex items-center justify-between gap-2">
            <h2 class="flex items-center gap-1.5 text-xs font-semibold text-slate-900">
              <span class="h-1.5 w-1.5 rounded-full" :class="online ? 'bg-emerald-500' : 'bg-slate-400'"></span>
              本次飞行
            </h2>
            <span class="rounded border border-slate-200 bg-slate-50 px-1.5 py-0.5 text-[9px] tracking-wide text-slate-500">无人机 · 车牌巡检</span>
          </div>
          <div class="grid grid-cols-2 gap-x-2 gap-y-2.5">
            <div>
              <span class="text-[10px] text-slate-400">要拍的车</span>
              <strong class="mt-0.5 block text-lg font-semibold leading-tight text-slate-900 tabular-nums">{{ kpi.nCars }}<small class="ml-0.5 text-[10px] font-normal text-slate-400">辆</small></strong>
            </div>
            <div>
              <span class="text-[10px] text-slate-400">拍到车牌</span>
              <strong class="mt-0.5 block text-lg font-semibold leading-tight text-slate-900 tabular-nums">{{ kpi.nCaptured }}<small class="ml-0.5 text-[10px] font-normal text-slate-400">辆</small></strong>
            </div>
            <div>
              <span class="text-[10px] text-slate-400">压线车辆</span>
              <strong class="mt-0.5 block text-lg font-semibold leading-tight tabular-nums" :class="kpi.violated ? 'text-rose-600' : 'text-slate-900'">{{ kpi.violated }}<small class="ml-0.5 text-[10px] font-normal text-slate-400">辆</small></strong>
            </div>
            <div>
              <span class="text-[10px] text-slate-400">本次用时</span>
              <strong class="mt-0.5 block whitespace-nowrap text-[13px] font-semibold leading-tight text-slate-900 tabular-nums">{{ wallHuman }}</strong>
            </div>
          </div>
          <div class="mt-3 flex items-center justify-between gap-2 border-t border-slate-100 pt-2">
            <span class="flex items-center gap-1 rounded bg-slate-100 px-1.5 py-0.5 text-[9.5px] text-slate-600">
              <span class="h-1 w-1 rounded-full" :class="online ? 'bg-emerald-500' : 'bg-rose-500'"></span>{{ statusText }}
            </span>
            <span class="text-[9px] text-slate-400">{{ kpi.runShort || '无批次' }}</span>
          </div>
        </section>

        <section class="rounded-lg border border-slate-200 bg-white p-3">
          <div class="mb-2.5 flex items-center justify-between gap-2">
            <h2 class="flex items-center gap-1.5 text-xs font-semibold text-slate-900">车牌拍摄情况</h2>
            <span class="text-[9px] tracking-widest text-slate-400">AI</span>
          </div>
          <div class="flex items-center gap-3">
            <div class="grid h-[66px] w-[66px] shrink-0 place-items-center rounded-full" :style="{ background: ring.gradient }">
              <div class="flex h-[46px] w-[46px] flex-col items-center justify-center rounded-full bg-white shadow-[inset_0_0_0_1px_#f1f5f9]">
                <strong class="text-[15px] font-semibold leading-none text-slate-900">{{ kpi.nCaptured }}</strong>
                <span class="mt-0.5 text-[8px] text-slate-400">拍清</span>
              </div>
            </div>
            <ul class="min-w-0 flex-1 space-y-1">
              <li class="flex items-center gap-1.5 text-[9.5px] text-slate-500"><i class="h-1.5 w-1.5 shrink-0 rounded-sm bg-blue-600"></i>拍清且停正<b class="ml-auto font-semibold text-slate-700 tabular-nums">{{ ring.clean }}</b></li>
              <li class="flex items-center gap-1.5 text-[9.5px] text-slate-500"><i class="h-1.5 w-1.5 shrink-0 rounded-sm bg-rose-500"></i>拍清但压线<b class="ml-auto font-semibold text-slate-700 tabular-nums">{{ ring.both }}</b></li>
              <li class="flex items-center gap-1.5 text-[9.5px] text-slate-500"><i class="h-1.5 w-1.5 shrink-0 rounded-sm bg-slate-300"></i>没拍到<b class="ml-auto font-semibold text-slate-700 tabular-nums">{{ ring.miss }}</b></li>
            </ul>
          </div>
          <!-- 2026-11-21：右下角的「看明细 →」已删（用户要求）。切换入口在顶栏，
               这里只留认定率这一句口径说明。 -->
          <div class="mt-2.5 border-t border-slate-100 pt-2 text-[9px] text-slate-400">
            拍到并读对 {{ kpi.rate }}%
          </div>
        </section>

        <!-- 自动巡航与补拍：填在左栏中段原本的弹性空白里，页内导航仍压在最底 -->
        <section class="rounded-lg border border-slate-200 bg-white p-3">
          <div class="mb-1 flex items-center justify-between gap-2">
            <h2 class="text-[10.5px] font-semibold tracking-wide text-slate-500">飞机是怎么飞的</h2>
            <span class="text-[8.5px] tracking-widest text-slate-400">AUTO</span>
          </div>
          <p class="mb-2.5 text-[9px] leading-snug text-slate-400">先飞一圈看全场 → 再一辆辆拍 → 漏了才补</p>
          <ol class="space-y-2">
            <li v-for="(step, i) in FLOW_STEPS" :key="step.title" class="flex gap-2">
              <span class="mt-[1.5px] grid h-3.5 w-3.5 shrink-0 place-items-center rounded-full bg-slate-900 text-[8px] font-semibold leading-none text-white">{{ i + 1 }}</span>
              <div class="min-w-0">
                <p class="text-[10.5px] font-semibold leading-tight text-slate-800">{{ step.title }}</p>
                <p class="mt-0.5 text-[9.5px] leading-[1.45] text-slate-500">{{ step.desc }}</p>
              </div>
            </li>
          </ol>
        </section>

        <!-- 2026-11-21：原「页内视图」整块（含底部弹性占位）已删除，
             「巡检过程 / 巡检结果」移到顶栏原「刷新数据」位置。左栏现在只剩三张信息卡。 -->
      </aside>

      <!-- ===== 中栏：主视图（过程回放为显式主入口） ===== -->
      <section class="order-1 flex min-h-0 flex-col gap-2 xl:order-none">
        <div class="flex shrink-0 flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-800 bg-slate-900 px-3 py-1.5">
          <div class="flex min-w-0 items-center gap-2">
            <span class="h-1.5 w-1.5 shrink-0 rounded-full" :class="online ? 'bg-emerald-400' : 'bg-rose-400'"></span>
            <strong class="truncate text-[11.5px] font-semibold text-slate-100">{{ stageTitle }}</strong>
            <span class="hidden truncate text-[9.5px] text-slate-500 sm:inline">任务编号 · {{ viewRun || '未载入批次' }}</span>
          </div>
          <div class="flex shrink-0 items-center gap-1.5">
            <span class="hidden rounded border border-slate-700 bg-slate-800 px-2 py-1 text-[10px] text-slate-400 sm:inline">{{ activeTab === 'result' ? '离线验收结果 · 车牌与违停判定' : '三路画面 + 飞行路线，进度同步' }}</span>
          </div>
        </div>

        <div class="flex min-h-0 flex-1 flex-col gap-1.5 pr-0.5" :class="zoomCell ? 'overflow-hidden' : 'overflow-y-auto'">
          <!-- 巡检结果 -->
          <template v-if="activeTab === 'result'">
            <section v-if="!acceptance" class="rounded-lg border border-slate-200 bg-white px-6 py-14 text-center">
              <Layers :size="34" class="mx-auto text-slate-300" />
              <p class="mt-3 text-sm text-slate-500">{{ loadingAcceptance ? '正在载入巡检结果…' : '还没有可回放的巡检批次。' }}</p>
            </section>
            <template v-else>
              <!-- 2026-11-21 晚（用户要求：只给结果、整页不滚、各尺寸都可读）：
                   ① 结论条只有一句结论 + 四个数，不重复放色带（下面的违停带已经表达同一件事）；
                   ② 违停判定压成一条横带，只有「车号 + 压线/停正」，七个胶囊一行排开；
                      左右偏、前后偏、车位吻合、离车位线这些判定过程一律不上页面；
                   ③ 车辆认定不再用 table：table 的行高会随字体与内边距浮出一截，
                      七行加起来把底部顶出视口。改成 flex 栅格 + 每行 flex-1 均分剩余高度，
                      视口变矮时行自动变矮，不会出现纵向滚动；
                   ④ 说明段全部撤掉，车位来源只留在违停条的悬停提示里。 -->
              <div class="flex h-full min-h-0 flex-col gap-1">
                <!-- ① 结论 -->
                <section class="shrink-0 rounded-lg border border-slate-200 bg-white px-3.5 py-2">
                  <p class="text-[14px] font-semibold leading-relaxed text-slate-800">{{ verdictLine }}</p>
                  <div class="mt-1.5 grid grid-cols-2 gap-1.5 sm:grid-cols-4">
                    <div v-for="card in summaryCards" :key="card.label" class="flex items-baseline justify-between gap-2 rounded-lg bg-slate-50 px-2.5 py-1" :title="card.note">
                      <span class="shrink-0 text-[10.5px] text-slate-500">{{ card.label }}</span>
                      <strong class="whitespace-nowrap text-[14px] font-semibold tabular-nums text-slate-900">{{ card.value }}</strong>
                    </div>
                  </div>
                </section>

                <!-- ② 违停判定：一条横带，只给结论 -->
                <section
                  class="shrink-0 rounded-lg border border-slate-200 bg-white px-3 py-1.5"
                  :title="acceptance.geometry ? '车位尺寸取自 ' + acceptance.geometry.start + ' 那一轮单独测量，本次沿用同一套；本次只验了拍得到、读得对' : ''"
                >
                  <div class="flex items-center gap-3">
                    <div class="shrink-0 leading-tight">
                      <h2 class="text-[13px] font-semibold text-slate-900">违停判定</h2>
                      <p class="mt-0.5 whitespace-nowrap text-[10.5px] text-slate-500"><b class="text-[13px] font-semibold text-rose-600">{{ violatedCount }}</b> 辆压线 / {{ violations.length }} 辆</p>
                    </div>
                    <ul class="flex min-w-0 flex-1 items-center gap-1.5">
                      <li
                        v-for="row in violations" :key="row.car"
                        class="flex min-w-0 flex-1 items-center justify-center gap-1 rounded-md px-1.5 py-1"
                        :class="row.margin_m < 0 ? 'bg-rose-50 ring-1 ring-rose-100' : 'bg-emerald-50 ring-1 ring-emerald-100'"
                      >
                        <i class="h-1.5 w-1.5 shrink-0 rounded-full" :class="row.margin_m < 0 ? 'bg-rose-500' : 'bg-emerald-500'"></i>
                        <span class="truncate text-[10.5px] font-medium text-slate-700">{{ row.car }}</span>
                        <span class="shrink-0 text-[11px] font-semibold" :class="row.margin_m < 0 ? 'text-rose-700' : 'text-emerald-700'">{{ row.margin_m < 0 ? '压线' : '停正' }}</span>
                      </li>
                    </ul>
                  </div>
                </section>

                <!-- ③ 车辆认定：flex 栅格，七行均分剩余高度 -->
                <section class="flex min-h-0 flex-1 flex-col rounded-lg border border-slate-200 bg-white px-3 py-1.5">
                  <div class="flex shrink-0 items-baseline justify-between gap-2">
                    <h2 class="text-[13px] font-semibold text-slate-900">车辆认定</h2>
                    <span class="text-[10.5px] text-slate-500">点照片看大图</span>
                  </div>
                  <div class="mt-1.5 flex min-h-0 flex-1 flex-col">
                    <div class="grid shrink-0 grid-cols-[52px_60px_minmax(96px,1fr)_68px_76px_68px_minmax(110px,1.1fr)] items-center gap-2 border-b border-slate-200 pb-1 text-[10.5px] text-slate-500">
                      <span>照片</span>
                      <span>车</span>
                      <span>车牌</span>
                      <span title="程序对整块车牌有多大把握，100% 最确定">识别把握</span>
                      <span>拍摄距离</span>
                      <span>怎么拍的</span>
                      <span>备注</span>
                    </div>
                    <div
                      v-for="vehicle in vehicles" :key="vehicle.car"
                      class="grid min-h-0 flex-1 grid-cols-[52px_60px_minmax(96px,1fr)_68px_76px_68px_minmax(110px,1.1fr)] items-center gap-2 border-b border-slate-100 text-[11.5px] last:border-0"
                    >
                      <span>
                        <button v-if="vehicle.card_url" class="block overflow-hidden rounded border border-slate-200 transition hover:border-indigo-400" :title="vehicle.car + ' · 车位 ' + slotCn(vehicle.real_car) + ' · ' + legCn(vehicle.leg) + (vehicle.note ? ' · ' + vehicle.note : '')" @click="showCard(vehicle.card_url)">
                          <img :src="vehicle.card_url" :alt="vehicle.car" class="block h-7 w-11 object-cover" />
                        </button>
                        <span v-else class="text-slate-400">—</span>
                      </span>
                      <span class="truncate text-slate-500">{{ vehicle.car }}</span>
                      <span class="truncate font-semibold text-slate-900">{{ vehicle.plate }}</span>
                      <span class="tabular-nums text-slate-600">{{ (vehicle.confidence * 100).toFixed(1) }}%</span>
                      <span class="tabular-nums text-slate-600">{{ vehicle.distance_m }} 米</span>
                      <span class="text-slate-600">{{ legCn(vehicle.leg) }}</span>
                      <span class="truncate text-[10.5px] text-slate-500" :title="vehicle.note">{{ vehicle.note ? noteCn(vehicle.note) : '—' }}</span>
                    </div>
                  </div>
                </section>
              </div>
            </template>
          </template>

          <!-- 巡检过程：四宫格同步时间轴（2026-11-20 由「过程回放」+「覆盖质量」合并） -->
          <template v-else>
            <section class="rounded-lg border border-slate-200 bg-white p-3">
              <div v-if="loadingReplay" class="py-20 text-center text-sm text-slate-500">正在载入巡检过程…</div>
              <div v-else-if="!replay" class="py-20 text-center text-sm text-slate-500">{{ replayError || '没有可用的回放数据。' }}</div>
              <template v-else>
                <!-- 2026-11-21（用户第二次要求）：原来这里有一块「MISSION REPLAY + 场景名 +
                     跑次/起跑时刻」的标题，下面还挂一张「当前阶段 / 正在执行 / 产生结果」的
                     靛蓝卡。用户指着这块说不需要——因为阶段名与进度现在都由视频下方的阶段条
                     承担，同一件事在上面又说一遍。整块删除，中栏直接从四宫格开始。
                     跑次号仍在左栏「批次遥测」与顶栏「作业区域」里，信息没丢。 -->

                <div class="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
                  <figure
                    class="relative overflow-hidden rounded-lg border border-slate-800 bg-slate-950"
                    :class="zoomCell === 'overview' ? 'cell-zoom' : ''"
                  >
                    <!-- 放大态顶栏：标题 + 倍速 + 退出（原来那条 figcaption 在放大时被替换掉） -->
                    <div v-if="zoomCell === 'overview'" class="flex shrink-0 items-center justify-between gap-2 border-b border-slate-800 bg-slate-900 px-3 py-2">
                      <div class="flex min-w-0 items-center gap-2">
                        <span class="shrink-0 rounded bg-indigo-600 px-1.5 py-0.5 text-[9px] font-semibold text-white">放大观看</span>
                        <strong class="truncate text-[13px] font-semibold text-slate-100">全场俯视</strong>
                        <span class="hidden truncate text-[10px] text-slate-500 sm:inline">固定机位俯视 · 全场</span>
                      </div>
                      <div class="flex shrink-0 items-center gap-1.5">
                        <PlaybackRateMenu v-model="playbackRate" />
                        <button type="button" class="grid h-7 w-7 place-items-center rounded border border-white/25 bg-slate-800 text-slate-200 transition hover:border-rose-400 hover:bg-rose-600 hover:text-white" title="退出放大（Esc）" @click="closeZoom">
                          <Minimize2 :size="14" />
                        </button>
                      </div>
                    </div>
                    <div class="relative overflow-hidden" :class="zoomCell === 'overview' ? 'min-h-0 flex-1' : 'aspect-[11/5]'">
                      <video
                        ref="overviewEl" :src="replay.video.src" :poster="replay.video.poster"
                        class="absolute inset-0 h-full w-full"
                        :class="zoomCell === 'overview' ? 'object-contain' : 'object-cover'"
                        playsinline preload="metadata"
                        @timeupdate="onVideoTime" @play="onPlayState" @pause="onPlayState" @loadedmetadata="onOverviewReady"
                      />
                      <!-- 常显的小工具条：图标别收进 hover，否则演示时用户找不到 -->
                      <div v-if="!zoomCell" class="absolute right-1.5 top-1.5 z-10 flex items-center gap-1">
                        <PlaybackRateMenu v-model="playbackRate" compact />
                        <button type="button" class="grid h-5 w-5 place-items-center rounded border border-white/25 bg-slate-900/75 text-slate-100 backdrop-blur transition hover:border-indigo-400 hover:bg-indigo-600" title="放大观看" aria-label="放大观看全场俯视" @click="openZoom('overview')">
                          <Maximize2 :size="12" />
                        </button>
                      </div>
                    </div>
                    <figcaption v-if="zoomCell !== 'overview'" class="pointer-events-none absolute inset-x-0 bottom-0 z-10 flex items-baseline justify-between gap-2 bg-gradient-to-t from-slate-950/95 via-slate-950/60 to-transparent px-2.5 pb-1 pt-5">
                      <span class="text-[11px] font-semibold text-slate-100">全场俯视</span>
                      <span class="text-[9px] text-slate-400">固定机位俯视 · 全场</span>
                    </figcaption>
                    <!-- 放大时页面底部那条被盖住，浮层自己带一条 -->
                    <div v-if="zoomCell === 'overview'" class="shrink-0 border-t border-slate-800 bg-slate-900 px-3 py-2">
                      <ReplayTransport dark :playing="playing" :current="videoTime" :duration="replay.video.seconds" @toggle="togglePlay" @seek="seekTo" />
                    </div>
                  </figure>

                  <figure
                    class="relative overflow-hidden rounded-lg border border-slate-800 bg-slate-950"
                    :class="zoomCell === 'onboard' ? 'cell-zoom' : ''"
                  >
                    <div v-if="zoomCell === 'onboard'" class="flex shrink-0 items-center justify-between gap-2 border-b border-slate-800 bg-slate-900 px-3 py-2">
                      <div class="flex min-w-0 items-center gap-2">
                        <span class="shrink-0 rounded bg-indigo-600 px-1.5 py-0.5 text-[9px] font-semibold text-white">放大观看</span>
                        <strong class="truncate text-[13px] font-semibold text-slate-100">{{ rvizFallback ? '俯视画面 · 左' : '机上视角' }}</strong>
                        <span class="hidden truncate text-[10px] text-slate-500 sm:inline">{{ rvizFallback ? '全场俯视 · 同步回放' : '机载相机 · 进近与拍摄' }}</span>
                      </div>
                      <div class="flex shrink-0 items-center gap-1.5">
                        <PlaybackRateMenu v-model="playbackRate" />
                        <button type="button" class="grid h-7 w-7 place-items-center rounded border border-white/25 bg-slate-800 text-slate-200 transition hover:border-rose-400 hover:bg-rose-600 hover:text-white" title="退出放大（Esc）" @click="closeZoom">
                          <Minimize2 :size="14" />
                        </button>
                      </div>
                    </div>
                    <div class="relative overflow-hidden" :class="zoomCell === 'onboard' ? 'min-h-0 flex-1' : 'aspect-[11/5]'">
                      <template v-if="coverageRviz">
                        <video :ref="bindRviz(0)" :src="coverageRviz.src" class="absolute max-w-none" :style="rvizStyle(0, zoomCell === 'onboard')" muted playsinline preload="metadata" />
                      </template>
                      <div v-else class="grid h-full place-items-center text-[11px] text-slate-500">本批次未提供分屏录屏</div>
                      <span v-if="rvizFallback && !zoomCell" class="pointer-events-none absolute left-1.5 top-1.5 z-10 rounded bg-slate-900/80 px-1.5 py-0.5 text-[9px] text-slate-400">同步回放 · 未提供分屏录屏</span>
                      <!-- 录屏比总览晚起的这一段，这一格冻在第 0 帧。盖一条说明，免得被当成卡死。 -->
                      <div v-if="rvizBeforeStart && coverageRviz && !zoomCell" class="pointer-events-none absolute inset-0 z-10 grid place-items-center bg-slate-950/75">
                        <span class="rounded bg-slate-900/85 px-2 py-1 text-[10px] text-slate-400">RViz 录屏自第 {{ fmtTime(rvizOffset) }} 起</span>
                      </div>
                      <div v-if="!zoomCell && coverageRviz" class="absolute right-1.5 top-1.5 z-10 flex items-center gap-1">
                        <PlaybackRateMenu v-model="playbackRate" compact />
                        <button type="button" class="grid h-5 w-5 place-items-center rounded border border-white/25 bg-slate-900/75 text-slate-100 backdrop-blur transition hover:border-indigo-400 hover:bg-indigo-600" title="放大观看" aria-label="放大观看机上视角" @click="openZoom('onboard')">
                          <Maximize2 :size="12" />
                        </button>
                      </div>
                    </div>
                    <figcaption v-if="zoomCell !== 'onboard'" class="pointer-events-none absolute inset-x-0 bottom-0 z-10 flex items-baseline justify-between gap-2 bg-gradient-to-t from-slate-950/95 via-slate-950/60 to-transparent px-2.5 pb-1 pt-5">
                      <span class="text-[11px] font-semibold text-slate-100">{{ rvizFallback ? '俯视画面 · 左' : '机上视角' }}</span>
                      <span class="text-[9px] text-slate-400">{{ rvizFallback ? '全场俯视 · 同步回放' : '机载相机 · 进近与拍摄' }}</span>
                    </figcaption>
                    <div v-if="zoomCell === 'onboard'" class="shrink-0 border-t border-slate-800 bg-slate-900 px-3 py-2">
                      <ReplayTransport dark :playing="playing" :current="videoTime" :duration="replay.video.seconds" @toggle="togglePlay" @seek="seekTo" />
                    </div>
                  </figure>

                  <figure
                    class="relative overflow-hidden rounded-lg border border-slate-800 bg-slate-950"
                    :class="zoomCell === 'viewpoint' ? 'cell-zoom' : ''"
                  >
                    <div v-if="zoomCell === 'viewpoint'" class="flex shrink-0 items-center justify-between gap-2 border-b border-slate-800 bg-slate-900 px-3 py-2">
                      <div class="flex min-w-0 items-center gap-2">
                        <span class="shrink-0 rounded bg-indigo-600 px-1.5 py-0.5 text-[9px] font-semibold text-white">放大观看</span>
                        <strong class="truncate text-[13px] font-semibold text-slate-100">{{ rvizFallback ? '俯视画面 · 右' : '拍照位置' }}</strong>
                        <span class="hidden truncate text-[10px] text-slate-500 sm:inline">{{ rvizFallback ? '全场俯视 · 同步回放' : '点云俯视 · 全场' }}</span>
                      </div>
                      <div class="flex shrink-0 items-center gap-1.5">
                        <PlaybackRateMenu v-model="playbackRate" />
                        <button type="button" class="grid h-7 w-7 place-items-center rounded border border-white/25 bg-slate-800 text-slate-200 transition hover:border-rose-400 hover:bg-rose-600 hover:text-white" title="退出放大（Esc）" @click="closeZoom">
                          <Minimize2 :size="14" />
                        </button>
                      </div>
                    </div>
                    <div class="relative overflow-hidden" :class="zoomCell === 'viewpoint' ? 'min-h-0 flex-1' : 'aspect-[11/5]'">
                      <template v-if="coverageRviz">
                        <video :ref="bindRviz(1)" :src="coverageRviz.src" class="absolute max-w-none" :style="rvizStyle(1, zoomCell === 'viewpoint')" muted playsinline preload="metadata" />
                      </template>
                      <div v-else class="grid h-full place-items-center text-[11px] text-slate-500">本批次未提供分屏录屏</div>
                      <span v-if="rvizFallback && !zoomCell" class="pointer-events-none absolute left-1.5 top-1.5 z-10 rounded bg-slate-900/80 px-1.5 py-0.5 text-[9px] text-slate-400">同步回放 · 未提供分屏录屏</span>
                      <!-- 同上：录屏起点晚于总览时，这一格先冻住并说明 -->
                      <div v-if="rvizBeforeStart && coverageRviz && !zoomCell" class="pointer-events-none absolute inset-0 z-10 grid place-items-center bg-slate-950/75">
                        <span class="rounded bg-slate-900/85 px-2 py-1 text-[10px] text-slate-400">RViz 录屏自第 {{ fmtTime(rvizOffset) }} 起</span>
                      </div>
                      <div v-if="!zoomCell && coverageRviz" class="absolute right-1.5 top-1.5 z-10 flex items-center gap-1">
                        <PlaybackRateMenu v-model="playbackRate" compact />
                        <button type="button" class="grid h-5 w-5 place-items-center rounded border border-white/25 bg-slate-900/75 text-slate-100 backdrop-blur transition hover:border-indigo-400 hover:bg-indigo-600" title="放大观看" aria-label="放大观看拍照位置" @click="openZoom('viewpoint')">
                          <Maximize2 :size="12" />
                        </button>
                      </div>
                    </div>
                    <figcaption v-if="zoomCell !== 'viewpoint'" class="pointer-events-none absolute inset-x-0 bottom-0 z-10 flex items-baseline justify-between gap-2 bg-gradient-to-t from-slate-950/95 via-slate-950/60 to-transparent px-2.5 pb-1 pt-5">
                      <span class="text-[11px] font-semibold text-slate-100">{{ rvizFallback ? '俯视画面 · 右' : '拍照位置' }}</span>
                      <span class="text-[9px] text-slate-400">{{ rvizFallback ? '全场俯视 · 同步回放' : '点云俯视 · 全场' }}</span>
                    </figcaption>
                    <div v-if="zoomCell === 'viewpoint'" class="shrink-0 border-t border-slate-800 bg-slate-900 px-3 py-2">
                      <ReplayTransport dark :playing="playing" :current="videoTime" :duration="replay.video.seconds" @toggle="togglePlay" @seek="seekTo" />
                    </div>
                  </figure>

                  <figure
                    class="relative overflow-hidden rounded-lg border border-slate-800 bg-slate-950"
                    :class="zoomCell === 'path' ? 'cell-zoom' : ''"
                  >
                    <div v-if="zoomCell === 'path'" class="flex shrink-0 items-center justify-between gap-2 border-b border-slate-800 bg-slate-900 px-3 py-2">
                      <div class="flex min-w-0 items-center gap-2">
                        <span class="shrink-0 rounded bg-indigo-600 px-1.5 py-0.5 text-[9px] font-semibold text-white">放大观看</span>
                        <strong class="truncate text-[13px] font-semibold text-slate-100">飞行路线</strong>
                        <span v-if="pathGeom" class="hidden truncate text-[10px] text-slate-500 sm:inline">{{ pathGeom.steps.length }} 步 · {{ pathStepLabel }} · 青色为已飞航段</span>
                      </div>
                      <div class="flex shrink-0 items-center gap-1.5">
                        <PlaybackRateMenu v-model="playbackRate" />
                        <button type="button" class="grid h-7 w-7 place-items-center rounded border border-white/25 bg-slate-800 text-slate-200 transition hover:border-rose-400 hover:bg-rose-600 hover:text-white" title="退出放大（Esc）" @click="closeZoom">
                          <Minimize2 :size="14" />
                        </button>
                      </div>
                    </div>
                    <div class="relative overflow-hidden" :class="zoomCell === 'path' ? 'min-h-0 flex-1' : 'aspect-[11/5]'">
                      <svg
                        v-if="pathGeom"
                        :viewBox="`0 0 ${pathGeom.w} ${pathGeom.h}`"
                        class="h-full w-full"
                        preserveAspectRatio="xMidYMid meet"
                      >
                        <rect x="0" y="0" :width="pathGeom.w" :height="pathGeom.h" fill="#020617" />
                        <!-- 5 m 网格 -->
                        <g stroke="#1e293b" stroke-width="0.4">
                          <line v-for="gx in pathGrid.x" :key="`gx${gx}`" :x1="gx" y1="0" :x2="gx" :y2="pathGeom.h" />
                          <line v-for="gy in pathGrid.y" :key="`gy${gy}`" x1="0" :y1="gy" :x2="pathGeom.w" :y2="gy" />
                        </g>
                        <!-- 场地边界（贴墙矩形 x±13.7 / y±9.2） -->
                        <rect x="0.8" y="0.8" :width="pathGeom.w - 1.6" :height="pathGeom.h - 1.6"
                              fill="none" stroke="#334155" stroke-width="0.8" stroke-dasharray="4 3" />
                        <!-- 车辆：4.4 × 1.8 m 的车体，车头短线指示朝向 -->
                        <g v-for="c in pathGeom.cars" :key="c.name" :transform="`rotate(${c.deg} ${c.cx} ${c.cy})`">
                          <rect :x="c.cx - 22" :y="c.cy - 9" width="44" height="18" rx="3"
                                fill="#1e293b" stroke="#64748b" stroke-width="0.7" />
                          <line :x1="c.cx + 14" :y1="c.cy" :x2="c.cx + 22" :y2="c.cy" stroke="#f59e0b" stroke-width="2" />
                        </g>
                        <!-- 车号单独画在旋转组外，否则文字会跟着车身一起转 -->
                        <text v-for="c in pathGeom.cars" :key="`n${c.name}`" :x="c.cx" :y="c.cy - 12"
                              font-size="7.5" fill="#64748b" text-anchor="middle">
                          {{ c.name.replace('car_', 'C') }}
                        </text>
                        <!-- 全量路径（未走：虚线灰）+ 已走（实线青） -->
                        <polyline :points="pathAll" fill="none" stroke="#334155" stroke-width="1" stroke-dasharray="2 2" />
                        <polyline :points="pathWalked" fill="none" stroke="#22d3ee" stroke-width="1.6" stroke-linejoin="round" />
                        <!-- 视点：拍摄腿为实心圆，转场腿为小点 -->
                        <circle
                          v-for="s in pathGeom.steps" :key="s.idx" :cx="s.cx" :cy="s.cy"
                          :r="s.kind === 'capture' ? 2.2 : 1.3"
                          :fill="s.idx <= pathCursor ? (s.kind === 'capture' ? '#22d3ee' : '#94a3b8') : (s.kind === 'capture' ? '#0e7490' : '#334155')"
                        />
                        <!-- 当前腿：外圈脉冲 + 实心点 -->
                        <g v-if="pathActive">
                          <circle :cx="pathActive.cx" :cy="pathActive.cy" r="6.5" fill="none" stroke="#22d3ee" stroke-width="1" opacity="0.55" />
                          <circle :cx="pathActive.cx" :cy="pathActive.cy" r="3" fill="#e0f2fe" stroke="#22d3ee" stroke-width="1" />
                        </g>
                      </svg>
                      <div v-else class="grid h-full place-items-center text-[11px] text-slate-500">
                        {{ loadingPath ? '正在载入路径…' : '本批次未采集飞行日志，暂无路线可回放' }}
                      </div>
                      <div v-if="!zoomCell" class="absolute right-1.5 top-1.5 z-10 flex items-center gap-1">
                        <PlaybackRateMenu v-model="playbackRate" compact />
                        <button type="button" class="grid h-5 w-5 place-items-center rounded border border-white/25 bg-slate-900/75 text-slate-100 backdrop-blur transition hover:border-indigo-400 hover:bg-indigo-600" title="放大观看" aria-label="放大观看飞行路线" @click="openZoom('path')">
                          <Maximize2 :size="12" />
                        </button>
                      </div>
                    </div>
                    <figcaption v-if="zoomCell !== 'path'" class="pointer-events-none absolute inset-x-0 bottom-0 z-10 flex items-baseline justify-between gap-2 bg-gradient-to-t from-slate-950/95 via-slate-950/60 to-transparent px-2.5 pb-1 pt-5">
                      <span class="text-[11px] font-semibold text-slate-100">飞行路线</span>
                      <span class="text-[9px] text-slate-400">
                        <template v-if="pathGeom">{{ pathGeom.steps.length }} 步 · {{ pathStepLabel }}</template>
                        <template v-else>按日志 GOTO 序列复算</template>
                      </span>
                    </figcaption>
                    <div v-if="zoomCell === 'path'" class="shrink-0 border-t border-slate-800 bg-slate-900 px-3 py-2">
                      <ReplayTransport dark :playing="playing" :current="videoTime" :duration="replay.video.seconds" @toggle="togglePlay" @seek="seekTo" />
                    </div>
                  </figure>
                </div>

                <div class="flex flex-wrap items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-1.5">
                  <ReplayTransport class="min-w-0 flex-1" :playing="playing" :current="videoTime" :duration="replay.video.seconds" @toggle="togglePlay" @seek="seekTo" />
                  <div class="flex shrink-0 items-center gap-1.5">
                    <span class="hidden text-[9px] text-slate-400 sm:inline">倍速 · 全窗格</span>
                    <PlaybackRateMenu v-model="playbackRate" compact />
                  </div>
                </div>

                <!-- 阶段区（2026-11-21）：四段等宽 + 实时进度填充。
                     文案统一为「阶段一：巡航发现」…，不再显示跑批侧的技术名（SURVEY / INSPECT 第 N 轮）；
                     进度按墙钟算（视频时间 + 录屏起点偏移），跟着播放走，暂停也停在同一格。 -->
                <div class="rounded-lg border border-slate-200 bg-white px-2.5 py-1.5">
                  <div class="mb-1.5 flex flex-wrap items-baseline justify-between gap-x-2 gap-y-0.5">
                    <div class="flex min-w-0 items-baseline gap-1.5">
                      <span class="shrink-0 text-[9px] text-slate-400">当前阶段</span>
                      <strong class="truncate text-[11.5px] font-semibold text-slate-900">{{ currentPhaseLabel }}</strong>
                      <span class="shrink-0 font-mono text-[9px] text-slate-400">{{ fmtTime(currentPhase?.t0 ?? 0) }}–{{ fmtTime(currentPhase?.t1 ?? 0) }}</span>
                    </div>
                    <span class="shrink-0 text-[9.5px] text-slate-500">
                      全程 <b class="font-semibold tabular-nums text-slate-800">{{ Math.round(overallProgress * 100) }}%</b>
                      <span class="ml-1.5 text-slate-400">本阶段还剩 {{ fmtDur(phaseRemain) }}</span>
                    </span>
                  </div>
                  <!-- 整轮进度：从第一段起点到末段终点 -->
                  <div class="h-1 overflow-hidden rounded-full bg-slate-100">
                    <div class="h-full rounded-full bg-gradient-to-r from-indigo-500 to-sky-400 transition-[width] duration-300 ease-linear" :style="{ width: `${overallProgress * 100}%` }" />
                  </div>
                  <!-- 四段等宽：原来按 ph.seconds 做 flexGrow，收尾降落只有几十秒就被挤成一条缝，
                       「阶段四」两个字都换行了。等宽更整齐，时长信息改由每格底部的进度条表达。 -->
                  <div class="mt-1.5 grid grid-cols-2 gap-1 sm:grid-cols-4">
                    <button
                      v-for="(ph, i) in replay.phases"
                      :key="ph.key"
                      type="button"
                      class="group relative h-[46px] overflow-hidden rounded-lg border text-left transition"
                      :class="i === phaseCursor ? 'border-indigo-400 ring-2 ring-indigo-300' : 'border-slate-200 hover:border-indigo-300'"
                      :title="`${phaseLabel(ph, i)}（${fmtTime(ph.t0)}–${fmtTime(ph.t1)}，${ph.seconds} s）：${phaseDetail(ph)}`"
                      @click="seekToPhase(ph)"
                    >
                      <img :src="ph.cover" :alt="phaseLabel(ph, i)" class="absolute inset-0 h-full w-full object-cover opacity-70" />
                      <!-- 压暗要压到底：收尾降落那张封面接近纯白，原来 via/35 的渐变到文字位置
                           只剩一点灰，白字直接糊掉。改成 95/70/40 三段，任何封面都能压住。 -->
                      <span class="absolute inset-0 bg-gradient-to-t from-slate-900/95 via-slate-900/70 to-slate-900/40" />
                      <span class="relative flex h-full flex-col justify-end gap-0.5 p-1.5">
                        <span class="truncate text-[10px] font-semibold leading-tight text-white">{{ phaseLabel(ph, i) }}</span>
                        <span class="truncate text-[9px] leading-tight text-indigo-200">{{ phaseDetail(ph) }}</span>
                        <!-- 本段进度：已走完的段满格，未到的段为空 -->
                        <span class="mt-0.5 block h-[3px] overflow-hidden rounded-full bg-white/25">
                          <span class="block h-full rounded-full bg-cyan-300 transition-[width] duration-300 ease-linear" :style="{ width: `${phaseFrac(ph) * 100}%` }" />
                        </span>
                      </span>
                    </button>
                  </div>
                </div>

              </template>
            </section>
          </template>

        </div>
      </section>

      <!-- ===== 右栏：当前视图的上下文（任务结果 / 覆盖分级 / 事件） ===== -->
      <aside class="order-3 flex min-h-0 flex-col gap-2 xl:order-none xl:overflow-y-auto xl:pl-0.5">
        <template v-if="activeTab === 'mission'">
          <section v-if="replay" class="rounded-lg border border-slate-200 bg-white p-3">
            <h3 class="text-xs font-semibold text-slate-800">任务结果</h3>
            <!-- 2026-11-21：原来这卡是「终态 / 清单 / 拍成 / 补拍轮次 / 墙钟预算」五个技术词，
                 领导看不懂。改成两句人话 + 两个数字块，并把「用掉预算 69%」并进下面的进度条，
                 去掉 kpi.note（原文只有「C_cap=1.000 达标」，口径内部看，不必上屏）。 -->
            <div class="mt-2.5 grid grid-cols-2 gap-1.5">
              <div class="rounded-lg border border-emerald-100 bg-emerald-50/70 px-2 py-1.5">
                <div class="text-[9.5px] text-emerald-700/70">任务状态</div>
                <div class="mt-0.5 text-[13px] font-semibold leading-tight text-emerald-700">{{ resultPlain.done }}</div>
              </div>
              <div class="rounded-lg border border-slate-200 bg-slate-50 px-2 py-1.5">
                <div class="text-[9.5px] text-slate-400">拍成车辆</div>
                <div class="mt-0.5 text-[13px] font-semibold leading-tight tabular-nums text-slate-900">{{ resultPlain.carsShort }}</div>
              </div>
            </div>
            <dl class="mt-2 space-y-1.5 text-[11px] leading-snug">
              <div>
                <dt class="text-slate-400">拍成情况</dt>
                <dd class="mt-0.5 font-medium text-slate-800">{{ resultPlain.cars }}</dd>
              </div>
              <div>
                <dt class="text-slate-400">补拍情况</dt>
                <dd class="mt-0.5 text-slate-700">{{ resultPlain.retry }}</dd>
              </div>
            </dl>
            <!-- 用时只在进度条这一处说：原来上面还有一行「用时 20 分 35 秒，上限 30 分钟」，
                 和条下的「已用 / 上限」完全重复，同一句话说了两遍。 -->
            <div class="mt-2.5 border-t border-slate-100 pt-2">
              <div class="flex items-baseline justify-between gap-2">
                <span class="text-[9.5px] text-slate-400">预算占用</span>
                <b class="text-[11px] font-semibold tabular-nums text-slate-800">{{ budgetUsedPct }}%</b>
              </div>
              <div class="mt-1 h-1.5 overflow-hidden rounded-full bg-slate-200">
                <div
                  class="h-full rounded-full transition-[width] duration-300"
                  :class="budgetUsedPct >= 90 ? 'bg-rose-500' : budgetUsedPct >= 70 ? 'bg-amber-500' : 'bg-indigo-500'"
                  :style="{ width: `${budgetUsedPct}%` }"
                />
              </div>
              <div class="mt-0.5 flex justify-between text-[9px] text-slate-400">
                <span>已用 {{ fmtDur(budgetSpent) }}</span>
                <span>上限 {{ fmtDur(replay.budget.budget_s) }}</span>
              </div>
            </div>
          </section>

          <!-- 2026-11-20 删除「覆盖分级」卡片（含覆盖热力图）：用户判断该图对演示无作用，
               分级比例与热力图与实际巡检决策无关。 -->
          <!-- 2026-11-21 再删「分排与车位」：用户要求去掉，把「关键事件」提到这个位置。
               覆盖率数据（coverageRows / coverageSlots）仍在 store 里，只是不再上屏。 -->

          <!-- 关键事件（2026-11-21 改造）：原来逐条贴跑批日志原文，像把 log 贴墙上。
               现在按播放进度实时生成，只显示已经发生的，最新一条在最上面并高亮；
               文案走 narrate()，数字从原文抠出来，换批次自动跟着变。 -->
          <section v-if="replay" class="flex min-h-0 flex-1 flex-col rounded-lg border border-slate-200 bg-white p-3">
            <div class="flex items-baseline justify-between gap-2">
              <h3 class="text-xs font-semibold text-slate-800">关键事件</h3>
              <span class="shrink-0 text-[9px] text-slate-400">随播放推进 · 已发生 {{ narratedPast.length }} / {{ narratedTotal }}</span>
            </div>
            <ul class="mt-2 min-h-0 flex-1 space-y-1.5 overflow-y-auto pr-1">
              <li
                v-for="(ev, i) in narratedPast"
                :key="`${ev.t}-${i}`"
                class="rounded-lg border px-2 py-1.5 transition"
                :class="i === 0 ? 'border-indigo-200 bg-indigo-50/70' : 'border-slate-100 bg-white'"
              >
                <div class="flex items-baseline gap-1.5">
                  <span class="shrink-0 font-mono text-[9px] tabular-nums" :class="i === 0 ? 'text-indigo-500' : 'text-slate-400'">{{ fmtTime(ev.t) }}</span>
                  <strong class="min-w-0 truncate text-[10.5px] font-semibold" :class="i === 0 ? 'text-indigo-900' : 'text-slate-700'">{{ ev.title }}</strong>
                  <span v-if="i === 0" class="ml-auto shrink-0 rounded bg-indigo-600 px-1 py-0.5 text-[8px] font-semibold leading-none text-white">刚刚</span>
                </div>
                <p class="mt-0.5 text-[10px] leading-[1.55]" :class="i === 0 ? 'text-indigo-900/80' : 'text-slate-500'">{{ ev.text }}</p>
              </li>
              <li v-if="!narratedPast.length" class="rounded-lg border border-dashed border-slate-200 px-2 py-3 text-center text-[10px] text-slate-400">
                播放开始后，这里按时间点逐条出现。
              </li>
            </ul>
          </section>
        </template>
      </aside>
    </main>

    <div v-if="previewCard" class="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/70 p-6" @click="previewCard = ''">
      <img :src="previewCard" alt="识别结果卡" class="max-h-full max-w-4xl rounded-xl shadow-2xl" />
    </div>
  </div>
</template>

<style scoped>
/**
 * 放大观看：把四宫格里任意一张 figure 直接撑满视口。
 * 用 fixed 而不是另开一份播放器——video 元素没被搬动/重建，时间轴连续，
 * 也就不存在「旁边那格还在播、这格停在原地」的同步问题。
 * 选择器带上 figure 提高特异性，压过 Tailwind 的 rounded-lg / border 工具类。
 */
figure.cell-zoom {
  position: fixed;
  inset: 0;
  z-index: 70;
  display: flex;
  flex-direction: column;
  border-width: 0;
  border-radius: 0;
  background: #020617;
}

/**
 * 「拍照位置」这一格原来挂过 .rviz-dark { filter: invert(1) hue-rotate(180deg) }，
 * 用来把白底的 RViz 录屏压成深色。2026-11-21 重录时改的是源头：
 * demo_multi_car.rviz 的 Background Color 由 255;255;255 改成 18;22;32（与 bg-slate-950 同族），
 * 录出来的画面本身就是深底，滤镜必须一起去掉——
 * 再 invert 一次会把深底翻回浅色，橙色点云也会被 hue-rotate 拧成青色。
 * 教训：滤镜只能当桥，源头能改就改源头，桥要记得拆。
 */
</style>
