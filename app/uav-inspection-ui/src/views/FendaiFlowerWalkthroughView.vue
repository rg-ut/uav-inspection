<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import * as THREE from 'three'
import { LogLevel, RenderMode, SceneFormat, Viewer } from '@mkkellogg/gaussian-splats-3d'
import { webPath } from '../utils/webroot'
import {
  ArrowDown,
  ArrowLeft,
  ArrowRight,
  ArrowUp,
  Expand,
  LocateFixed,
  MousePointer2,
  MoveDown,
  MoveUp,
  RotateCcw,
} from 'lucide-vue-next'

type MovementKey = 'KeyW' | 'KeyA' | 'KeyS' | 'KeyD' | 'Space' | 'ControlLeft' | 'ShiftLeft' | 'KeyR'

interface FendaiScene {
  id: string
  title: string
  model_url: string
  available: boolean
  point_count: number
  training_iterations: number
}

const router = useRouter()
const viewport = ref<HTMLDivElement | null>(null)
const loadProgress = ref(0)
const loadState = ref<'loading' | 'ready' | 'missing' | 'error'>('loading')
const loadMessage = ref('正在载入粉黛花场景')
const scene = ref<FendaiScene | null>(null)
const pointCountLabel = computed(() => {
  if (!scene.value) return ''
  const count = scene.value.point_count
  return `${count >= 1_000_000 ? `${(count / 1_000_000).toFixed(2)}M` : count.toLocaleString('zh-CN')} 点`
})
const loadHelp = computed(() => loadState.value === 'missing'
  ? '请在素材上传中心选择粉黛花模型并上传 fendai-flower.ksplat，或按项目 README 安装模型。'
  : '请检查后端服务和模型文件，安装步骤见项目 README。')
const isPointerLocked = ref(false)
const isFullscreen = ref(false)
const fps = ref<number | null>(null)

const playerSpawn = new THREE.Vector3(27.265092, 21.980719, -14.00421)
const eyeHeight = 1.65
const initialYaw = wrapAngle(2 * Math.atan2(0.5345788, 0.84511864) + Math.PI + 0.44)
const initialPitch = -2 * Math.atan2(0.40231982, 0.9154992)
const keys = new Set<MovementKey>()

let camera: THREE.PerspectiveCamera | null = null
let renderer: THREE.WebGLRenderer | null = null
let viewer: Viewer | null = null
let animationFrame = 0
let lastFrameTime = 0
let yaw = initialYaw
let pitch = initialPitch
let activeTouchId: number | null = null
let previousTouch = { x: 0, y: 0 }
let fpsUpdateAt = 0
let disposed = false
let settleFrame = 0
let resolveSettleFrame: (() => void) | null = null
const metadataRequest = new AbortController()

function wrapAngle(angle: number) {
  return Math.atan2(Math.sin(angle), Math.cos(angle))
}

function setCameraAngles() {
  if (!camera) return
  camera.rotation.order = 'YXZ'
  camera.rotation.set(pitch, yaw, 0, 'YXZ')
}

function resetView() {
  yaw = initialYaw
  pitch = initialPitch
  if (camera) camera.position.set(playerSpawn.x, playerSpawn.y + eyeHeight, playerSpawn.z)
  setCameraAngles()
}

function resizeScene() {
  const element = viewport.value
  if (!element || !renderer || !camera) return
  const width = Math.max(1, element.clientWidth)
  const height = Math.max(1, element.clientHeight)
  renderer.setSize(width, height, false)
  camera.aspect = width / height
  camera.updateProjectionMatrix()
}

function movePlayer(deltaSeconds: number) {
  if (!camera) return
  const forward = new THREE.Vector3(-Math.sin(yaw), 0, -Math.cos(yaw))
  const right = new THREE.Vector3(Math.cos(yaw), 0, -Math.sin(yaw))
  const movement = new THREE.Vector3()

  if (keys.has('KeyW')) movement.add(forward)
  if (keys.has('KeyS')) movement.sub(forward)
  if (keys.has('KeyD')) movement.add(right)
  if (keys.has('KeyA')) movement.sub(right)
  if (keys.has('Space')) movement.y += 1
  if (keys.has('ControlLeft')) movement.y -= 1

  if (movement.lengthSq() > 0) {
    movement.normalize()
    const speed = keys.has('ShiftLeft') ? 9 : 5
    camera.position.addScaledVector(movement, speed * deltaSeconds)
  }
}

function renderFrame(time: number) {
  if (disposed || !viewer) return
  animationFrame = requestAnimationFrame(renderFrame)

  const deltaSeconds = lastFrameTime ? Math.min((time - lastFrameTime) / 1000, 0.05) : 0
  lastFrameTime = time
  movePlayer(deltaSeconds)
  viewer.update()
  viewer.render()

  if (time - fpsUpdateAt > 700) {
    fps.value = viewer.currentFPS
    fpsUpdateAt = time
  }
}

function nextFrame() {
  if (disposed) return Promise.resolve()
  return new Promise<void>((resolve) => {
    resolveSettleFrame = resolve
    settleFrame = requestAnimationFrame(() => {
      settleFrame = 0
      resolveSettleFrame = null
      resolve()
    })
  })
}

async function settleInitialView(activeViewer: Viewer) {
  const deadline = performance.now() + 15000
  while (
    viewer === activeViewer &&
    performance.now() < deadline &&
    (activeViewer.sortRunning || activeViewer.splatSortCount < activeViewer.splatRenderCount)
  ) {
    await nextFrame()
  }

  if (disposed || viewer !== activeViewer) return
  camera?.updateMatrixWorld(true)
  await activeViewer.runSplatSort(true, true)

  const sortDeadline = performance.now() + 15000
  while (viewer === activeViewer && activeViewer.sortRunning && performance.now() < sortDeadline) {
    await nextFrame()
  }
}

function onKeyDown(event: KeyboardEvent) {
  if (loadState.value !== 'ready') return
  const code = event.code as MovementKey | 'ShiftLeft'
  if (['KeyW', 'KeyA', 'KeyS', 'KeyD', 'Space', 'ControlLeft', 'ShiftLeft'].includes(code)) {
    event.preventDefault()
    keys.add(code as MovementKey)
  }
  if (code === 'KeyR') resetView()
}

function onKeyUp(event: KeyboardEvent) {
  keys.delete(event.code as MovementKey)
}

function onMouseMove(event: MouseEvent) {
  if (document.pointerLockElement !== renderer?.domElement) return
  yaw -= event.movementX * 0.0022
  pitch = THREE.MathUtils.clamp(pitch - event.movementY * 0.0022, -1.48, 1.48)
  setCameraAngles()
}

function onPointerLockChange() {
  isPointerLocked.value = document.pointerLockElement === renderer?.domElement
  if (!isPointerLocked.value) stopMovement()
}

function stopMovement() {
  keys.clear()
  activeTouchId = null
  lastFrameTime = 0
}

function onVisibilityChange() {
  if (document.hidden) stopMovement()
}

function startPointerLock() {
  if (loadState.value !== 'ready' || !renderer?.domElement.requestPointerLock) return
  void renderer.domElement.requestPointerLock()?.catch(() => {})
}

function onTouchStart(event: PointerEvent) {
  if (loadState.value !== 'ready' || event.pointerType !== 'touch' || !viewport.value) return
  activeTouchId = event.pointerId
  previousTouch = { x: event.clientX, y: event.clientY }
  viewport.value.setPointerCapture(event.pointerId)
}

function onTouchMove(event: PointerEvent) {
  if (event.pointerId !== activeTouchId) return
  const dx = event.clientX - previousTouch.x
  const dy = event.clientY - previousTouch.y
  previousTouch = { x: event.clientX, y: event.clientY }
  yaw -= dx * 0.004
  pitch = THREE.MathUtils.clamp(pitch - dy * 0.004, -1.48, 1.48)
  setCameraAngles()
}

function onTouchEnd(event: PointerEvent) {
  if (event.pointerId === activeTouchId) activeTouchId = null
}

function pressMovement(key: MovementKey, event: PointerEvent) {
  if (loadState.value !== 'ready') return
  event.preventDefault()
  keys.add(key)
  ;(event.currentTarget as HTMLElement).setPointerCapture(event.pointerId)
}

function releaseMovement(key: MovementKey) {
  keys.delete(key)
}

async function toggleFullscreen() {
  if (!viewport.value) return
  try {
    if (document.fullscreenElement) await document.exitFullscreen()
    else await viewport.value.requestFullscreen()
  } catch {
    isFullscreen.value = Boolean(document.fullscreenElement)
  }
}

function onFullscreenChange() {
  isFullscreen.value = Boolean(document.fullscreenElement)
  resizeScene()
}

async function goHome() {
  if (document.pointerLockElement) document.exitPointerLock()
  await router.push('/')
}

async function goToAssets() {
  await router.push('/assets')
}

function releaseScene() {
  const activeViewer = viewer
  const activeRenderer = renderer
  viewer = null
  renderer = null
  camera = null
  cancelAnimationFrame(animationFrame)
  cancelAnimationFrame(settleFrame)
  resolveSettleFrame?.()
  resolveSettleFrame = null
  activeRenderer?.domElement.remove()
  const releaseRenderer = () => {
    activeRenderer?.dispose()
    activeRenderer?.forceContextLoss()
  }
  if (activeViewer) void activeViewer.dispose().catch(() => {}).finally(releaseRenderer)
  else releaseRenderer()
}

onMounted(async () => {
  const element = viewport.value
  if (!element) return

  try {
    const response = await fetch(webPath('/api/v1/fendai/scene'), { signal: metadataRequest.signal })
    if (!response.ok) throw new Error(`Scene metadata request failed: ${response.status}`)
    const result = await response.json()
    if (disposed) return
    const data = result.data as FendaiScene | undefined
    if (result.code !== 200 || !data || typeof data.available !== 'boolean' || typeof data.model_url !== 'string'
      || !Number.isFinite(data.point_count) || !Number.isFinite(data.training_iterations)) {
      throw new Error('Invalid scene metadata')
    }
    scene.value = data
    if (!data.available) {
      loadState.value = 'missing'
      loadMessage.value = '尚未安装粉黛花模型'
      return
    }

    renderer = new THREE.WebGLRenderer({ antialias: false, powerPreference: 'high-performance' })
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5))
    renderer.setClearColor(0x101719, 1)
    element.appendChild(renderer.domElement)

    camera = new THREE.PerspectiveCamera(58, 1, 0.02, 1500)
    camera.up.set(0, 1, 0)
    resetView()

    viewer = new Viewer({
      rootElement: element,
      renderer,
      camera,
      selfDrivenMode: false,
      useBuiltInControls: false,
      sharedMemoryForWorkers: false,
      gpuAcceleratedSort: false,
      integerBasedSort: false,
      halfPrecisionCovariancesOnGPU: true,
      freeIntermediateSplatData: true,
      sphericalHarmonicsDegree: 2,
      renderMode: RenderMode.Always,
      logLevel: LogLevel.None,
    })
    resizeScene()

    const activeViewer = viewer
    const modelLoad = activeViewer.addSplatScene(data.model_url, {
      // The backend URL has no file extension; avoid format inference from its path.
      format: SceneFormat.KSplat,
      showLoadingUI: false,
      splatAlphaRemovalThreshold: 5,
      position: [8.7518215, 32.66471, -2.003349],
      rotation: [0.8112788, 0, 0.3221035, -0.48793045],
      scale: [44.286705, 44.286705, -44.286705],
      onProgress: (percent: number, label?: string, state?: number) => {
        if (disposed || viewer !== activeViewer) return
        loadProgress.value = Math.max(0, Math.min(100, Math.round(percent)))
        if (state === 1) loadMessage.value = '正在构建场景'
        else if (label) loadMessage.value = `正在载入粉黛花场景 ${label}`
      },
    })

    window.addEventListener('resize', resizeScene)
    window.addEventListener('keydown', onKeyDown)
    window.addEventListener('keyup', onKeyUp)
    window.addEventListener('blur', stopMovement)
    document.addEventListener('mousemove', onMouseMove)
    document.addEventListener('pointerlockchange', onPointerLockChange)
    document.addEventListener('fullscreenchange', onFullscreenChange)
    document.addEventListener('visibilitychange', onVisibilityChange)

    // 0.4.7 returns an AbortablePromise wrapper; await its native promise so rejections propagate.
    await modelLoad.promise
    if (disposed || viewer !== activeViewer) return
    loadProgress.value = 100
    loadMessage.value = '正在优化初始视角'
    animationFrame = requestAnimationFrame(renderFrame)
    await settleInitialView(activeViewer)
    if (disposed || viewer !== activeViewer) return
    loadState.value = 'ready'
    loadMessage.value = ''
  } catch (error) {
    if (disposed) return
    console.error('Failed to load the Fendai Flower scene.', error)
    loadState.value = 'error'
    loadMessage.value = renderer ? '模型载入失败' : scene.value ? '浏览器无法初始化三维渲染器' : '暂时无法读取场景信息'
    stopMovement()
    releaseScene()
  }
})

onBeforeUnmount(() => {
  disposed = true
  metadataRequest.abort()
  cancelAnimationFrame(animationFrame)
  window.removeEventListener('resize', resizeScene)
  window.removeEventListener('keydown', onKeyDown)
  window.removeEventListener('keyup', onKeyUp)
  window.removeEventListener('blur', stopMovement)
  document.removeEventListener('mousemove', onMouseMove)
  document.removeEventListener('pointerlockchange', onPointerLockChange)
  document.removeEventListener('fullscreenchange', onFullscreenChange)
  document.removeEventListener('visibilitychange', onVisibilityChange)
  stopMovement()
  if (document.pointerLockElement === renderer?.domElement) document.exitPointerLock()
  if (document.fullscreenElement === viewport.value) void document.exitFullscreen().catch(() => {})
  releaseScene()
})
</script>

<template>
  <main ref="viewport" class="flower-walkthrough" @pointerdown="onTouchStart" @pointermove="onTouchMove" @pointerup="onTouchEnd" @pointercancel="onTouchEnd">
    <header class="scene-header">
      <button class="icon-button back-button" aria-label="返回平台首页" title="返回平台首页" @click="goHome">
        <ArrowLeft :size="19" />
      </button>

      <div class="scene-title">
        <span class="scene-kicker">数字园区治理 / 三维场景</span>
        <h1>{{ scene?.title || '粉黛花漫游' }}</h1>
      </div>

      <div class="scene-status">
        <span class="status-dot" :class="loadState"></span>
        <span>{{ loadState === 'ready' ? '场景已就绪' : loadState === 'missing' ? '待安装模型' : loadState === 'error' ? '场景异常' : '场景载入中' }}</span>
        <template v-if="pointCountLabel">
          <span class="status-divider"></span>
          <span>{{ pointCountLabel }}</span>
        </template>
        <template v-if="loadState === 'ready' && fps !== null">
          <span class="status-divider desktop-only"></span>
          <span class="desktop-only">{{ fps }} FPS</span>
        </template>
      </div>

      <div class="scene-actions">
        <button class="icon-button desktop-only" :disabled="loadState !== 'ready'" :class="{ active: isPointerLocked }" aria-label="进入第一人称漫游" title="进入第一人称漫游" @click="startPointerLock">
          <MousePointer2 :size="18" />
        </button>
        <button class="icon-button" :disabled="loadState !== 'ready'" aria-label="重置视角" title="重置视角" @click="resetView">
          <RotateCcw :size="18" />
        </button>
        <button class="icon-button" :aria-label="isFullscreen ? '退出全屏' : '进入全屏'" :title="isFullscreen ? '退出全屏' : '进入全屏'" @click="toggleFullscreen">
          <Expand :size="18" />
        </button>
      </div>
    </header>

    <div v-if="loadState !== 'ready'" class="load-state" :class="{ failed: loadState === 'error' }" role="status" aria-live="polite">
      <div v-if="loadState === 'loading'" class="loader-mark"><span></span><span></span><span></span></div>
      <LocateFixed v-else :size="22" />
      <div class="load-copy">
        <strong>{{ loadMessage }}</strong>
        <span v-if="loadState === 'loading'">{{ loadProgress }}%</span>
      </div>
      <div v-if="loadState === 'loading'" class="load-track"><span :style="{ width: `${loadProgress}%` }"></span></div>
      <template v-else>
        <p class="load-help">{{ loadHelp }}</p>
        <button class="install-button" @click="goToAssets">前往素材上传中心</button>
      </template>
    </div>

    <div v-if="loadState === 'ready'" class="crosshair" aria-hidden="true"><span></span><span></span></div>

    <footer class="scene-footer">
      <div class="model-caption">
        <span class="caption-line"></span>
        <div>
          <strong>粉黛乱子草</strong>
          <span>高斯泼溅模型<span v-if="scene"> · {{ scene.training_iterations.toLocaleString('zh-CN') }} iterations</span></span>
        </div>
      </div>
      <div class="desktop-controls" aria-label="漫游控制">
        <span><kbd>W</kbd><kbd>A</kbd><kbd>S</kbd><kbd>D</kbd><i>移动</i></span>
        <span><kbd>Space</kbd><kbd>Ctrl</kbd><i>升降</i></span>
        <span><kbd>Shift</kbd><i>加速</i></span>
        <span><kbd>R</kbd><i>重置</i></span>
      </div>
    </footer>

    <nav v-if="loadState === 'ready'" class="touch-controls" aria-label="触控漫游控制">
      <div class="move-pad">
        <button class="pad-button pad-up" aria-label="前进" title="前进" @pointerdown.stop="pressMovement('KeyW', $event)" @pointerup="releaseMovement('KeyW')" @pointercancel="releaseMovement('KeyW')" @lostpointercapture="releaseMovement('KeyW')"><ArrowUp :size="18" /></button>
        <button class="pad-button pad-left" aria-label="向左" title="向左" @pointerdown.stop="pressMovement('KeyA', $event)" @pointerup="releaseMovement('KeyA')" @pointercancel="releaseMovement('KeyA')" @lostpointercapture="releaseMovement('KeyA')"><ArrowLeft :size="18" /></button>
        <button class="pad-button pad-down" aria-label="后退" title="后退" @pointerdown.stop="pressMovement('KeyS', $event)" @pointerup="releaseMovement('KeyS')" @pointercancel="releaseMovement('KeyS')" @lostpointercapture="releaseMovement('KeyS')"><ArrowDown :size="18" /></button>
        <button class="pad-button pad-right" aria-label="向右" title="向右" @pointerdown.stop="pressMovement('KeyD', $event)" @pointerup="releaseMovement('KeyD')" @pointercancel="releaseMovement('KeyD')" @lostpointercapture="releaseMovement('KeyD')"><ArrowRight :size="18" /></button>
      </div>
      <div class="vertical-pad">
        <button class="pad-button" aria-label="上升" title="上升" @pointerdown.stop="pressMovement('Space', $event)" @pointerup="releaseMovement('Space')" @pointercancel="releaseMovement('Space')" @lostpointercapture="releaseMovement('Space')"><MoveUp :size="18" /></button>
        <button class="pad-button" aria-label="下降" title="下降" @pointerdown.stop="pressMovement('ControlLeft', $event)" @pointerup="releaseMovement('ControlLeft')" @pointercancel="releaseMovement('ControlLeft')" @lostpointercapture="releaseMovement('ControlLeft')"><MoveDown :size="18" /></button>
      </div>
    </nav>
  </main>
</template>

<style scoped>
.flower-walkthrough {
  position: fixed;
  inset: 0;
  overflow: hidden;
  color: #f5f7f7;
  background: #101719;
  font-family: Inter, "Microsoft YaHei", sans-serif;
  touch-action: none;
  user-select: none;
}

.flower-walkthrough :deep(canvas) {
  display: block;
  width: 100%;
  height: 100%;
  outline: none;
}

.scene-header,
.scene-footer {
  position: absolute;
  z-index: 3;
  display: flex;
  align-items: center;
  background: rgba(12, 17, 18, 0.78);
  border-color: rgba(255, 255, 255, 0.1);
  backdrop-filter: blur(14px);
}

.scene-header {
  top: 0;
  left: 0;
  right: 0;
  height: 68px;
  gap: 16px;
  padding: 0 22px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.11);
}

.icon-button,
.pad-button {
  display: inline-grid;
  place-items: center;
  color: #e6eceb;
  background: rgba(255, 255, 255, 0.07);
  border: 1px solid rgba(255, 255, 255, 0.14);
  border-radius: 6px;
  transition: background 120ms ease, border-color 120ms ease, color 120ms ease;
  cursor: pointer;
}

.icon-button {
  width: 38px;
  height: 38px;
}

.icon-button:hover,
.icon-button.active,
.pad-button:active {
  color: #fff;
  background: rgba(185, 98, 139, 0.42);
  border-color: rgba(227, 157, 190, 0.62);
}

.icon-button:disabled {
  opacity: 0.4;
  cursor: default;
}

.back-button {
  flex: 0 0 auto;
}

.scene-title {
  min-width: 190px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.scene-kicker {
  color: #9da9a7;
  font-size: 10px;
  line-height: 1.25;
}

.scene-title h1 {
  margin: 0;
  color: #f5f7f7;
  font-size: 15px;
  line-height: 1.3;
  font-weight: 650;
}

.scene-status {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-left: auto;
  color: #c7d0ce;
  font-size: 11px;
  white-space: nowrap;
}

.status-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #d8b36c;
}

.status-dot.ready {
  background: #72c79a;
  box-shadow: 0 0 10px rgba(114, 199, 154, 0.55);
}

.status-dot.error {
  background: #ef7d7d;
}

.status-divider {
  width: 1px;
  height: 14px;
  background: rgba(255, 255, 255, 0.19);
}

.scene-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-left: 14px;
}

.load-state {
  position: absolute;
  z-index: 2;
  left: 50%;
  top: 50%;
  width: min(360px, calc(100% - 36px));
  padding: 18px 20px;
  transform: translate(-50%, -50%);
  display: grid;
  grid-template-columns: 28px 1fr;
  align-items: center;
  column-gap: 12px;
  row-gap: 13px;
  color: #f4f6f5;
  background: rgba(17, 24, 25, 0.92);
  border: 1px solid rgba(255, 255, 255, 0.14);
  border-radius: 7px;
  box-shadow: 0 16px 50px rgba(0, 0, 0, 0.25);
}

.loader-mark {
  display: flex;
  align-items: end;
  justify-content: center;
  gap: 3px;
  height: 22px;
}

.loader-mark span {
  width: 4px;
  height: 8px;
  border-radius: 2px;
  background: #de9fbd;
  animation: load-wave 850ms ease-in-out infinite alternate;
}

.loader-mark span:nth-child(2) { height: 16px; animation-delay: 120ms; }
.loader-mark span:nth-child(3) { height: 11px; animation-delay: 240ms; }

.load-copy {
  min-width: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  font-size: 12px;
}

.load-copy strong {
  font-weight: 550;
}

.load-copy span {
  color: #aeb8b6;
  font-variant-numeric: tabular-nums;
}

.load-track {
  grid-column: 1 / -1;
  height: 3px;
  overflow: hidden;
  background: rgba(255, 255, 255, 0.1);
  border-radius: 2px;
}

.load-track span {
  display: block;
  height: 100%;
  background: #d794b1;
  transition: width 180ms ease;
}

.load-state.failed {
  grid-template-columns: 28px 1fr;
  color: #ffd6d6;
}

.load-help {
  grid-column: 1 / -1;
  margin: 0;
  color: #c7d0ce;
  font-size: 12px;
  line-height: 1.7;
}

.install-button {
  grid-column: 1 / -1;
  padding: 9px 12px;
  color: #fff;
  background: #87506b;
  border: 1px solid #b97998;
  border-radius: 6px;
  cursor: pointer;
  font: inherit;
  font-size: 12px;
}

.install-button:hover {
  background: #9d607c;
}

.crosshair {
  position: absolute;
  left: 50%;
  top: 50%;
  z-index: 1;
  width: 13px;
  height: 13px;
  transform: translate(-50%, -50%);
  opacity: 0.64;
  pointer-events: none;
}

.crosshair span:first-child {
  position: absolute;
  top: 6px;
  left: 1px;
  width: 11px;
  height: 1px;
  background: #fff;
}

.crosshair span:last-child {
  position: absolute;
  top: 1px;
  left: 6px;
  width: 1px;
  height: 11px;
  background: #fff;
}

.scene-footer {
  left: 0;
  right: 0;
  bottom: 0;
  min-height: 72px;
  justify-content: space-between;
  gap: 22px;
  padding: 12px 24px;
  border-top: 1px solid rgba(255, 255, 255, 0.11);
}

.model-caption {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}

.caption-line {
  width: 3px;
  height: 31px;
  background: #d794b1;
  border-radius: 2px;
}

.model-caption div {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.model-caption strong {
  color: #f4f6f5;
  font-size: 12px;
  font-weight: 600;
}

.model-caption span:not(.caption-line) {
  color: #a3afac;
  font-size: 10px;
}

.desktop-controls {
  display: flex;
  align-items: center;
  gap: 20px;
  color: #c7d0ce;
  font-size: 10px;
}

.desktop-controls span {
  display: flex;
  align-items: center;
  gap: 4px;
}

.desktop-controls kbd {
  min-width: 18px;
  height: 19px;
  padding: 0 4px;
  display: inline-grid;
  place-items: center;
  color: #e1e7e5;
  background: rgba(255, 255, 255, 0.09);
  border: 1px solid rgba(255, 255, 255, 0.16);
  border-radius: 3px;
  font: inherit;
  font-size: 9px;
}

.desktop-controls i {
  margin-left: 3px;
  color: #a3afac;
  font-style: normal;
}

.touch-controls {
  display: none;
}

@keyframes load-wave {
  to { transform: scaleY(1.45); opacity: 0.55; }
}

@media (max-width: 760px) {
  .scene-header {
    height: 60px;
    gap: 10px;
    padding: 0 12px;
  }

  .scene-title {
    min-width: 0;
    flex: 1 1 auto;
  }

  .scene-kicker { display: none; }
  .scene-title h1 { font-size: 14px; }
  .scene-status { gap: 7px; margin: 0; font-size: 10px; }
  .scene-actions { gap: 5px; margin-left: 2px; }
  .icon-button { width: 34px; height: 34px; }
  .desktop-only { display: none !important; }

  .scene-footer {
    display: none;
  }

  .caption-line { height: 25px; }
  .model-caption { gap: 9px; }
  .model-caption strong { font-size: 11px; }
  .model-caption span:not(.caption-line) { font-size: 9px; }

  .touch-controls {
    position: absolute;
    z-index: 3;
    left: 0;
    right: 0;
    bottom: max(12px, env(safe-area-inset-bottom));
    display: flex;
    align-items: end;
    justify-content: space-between;
    padding: 0 18px;
    pointer-events: none;
  }

  .move-pad,
  .vertical-pad {
    display: grid;
    gap: 5px;
    pointer-events: auto;
  }

  .move-pad {
    grid-template-columns: repeat(3, 42px);
    grid-template-rows: repeat(2, 42px);
  }

  .pad-up { grid-column: 2; grid-row: 1; }
  .pad-left { grid-column: 1; grid-row: 2; }
  .pad-down { grid-column: 2; grid-row: 2; }
  .pad-right { grid-column: 3; grid-row: 2; }

  .vertical-pad {
    grid-template-columns: 42px;
    grid-template-rows: repeat(2, 42px);
  }

  .pad-button {
    width: 42px;
    height: 42px;
    border-radius: 50%;
    background: rgba(16, 23, 25, 0.72);
    touch-action: none;
  }
}

@media (max-width: 390px) {
  .scene-header { gap: 8px; padding: 0 9px; }
  .scene-status { font-size: 9px; gap: 5px; }
  .scene-actions { gap: 4px; }
  .icon-button { width: 31px; height: 31px; }
  .touch-controls { padding: 0 12px; }
}
</style>
