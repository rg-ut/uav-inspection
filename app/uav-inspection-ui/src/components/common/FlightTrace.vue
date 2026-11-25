<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import type { TelemetryFrame } from '../../utils/srtParser'
import WallLocationMap from '../wall/WallLocationMap.vue'

type Mode = 'track' | 'altitude'
type Sample = { frame: TelemetryFrame; breakBefore: boolean }

const props = defineProps<{ frames: TelemetryFrame[]; current: TelemetryFrame | null; time: number }>()
const emit = defineEmits<{ seek: [seconds: number] }>()
const canvas = ref<HTMLCanvasElement>()
const preference = ref<Mode>()
const observer = new ResizeObserver(drawAltitude)
let altitudePositions: { x: number; time: number }[] = []

function hasPosition(frame: TelemetryFrame) {
  return frame.gpsAvailable === true && Number.isFinite(frame.latitude) && Math.abs(frame.latitude) <= 85 &&
    Number.isFinite(frame.longitude) && Math.abs(frame.longitude) <= 180
}
function hasAltitude(frame: TelemetryFrame) {
  return frame.altitudeAvailable === true && Number.isFinite(frame.relAlt)
}

function sampleFrames(valid: (frame: TelemetryFrame) => boolean) {
  const samples: Sample[] = []
  const stride = Math.max(1, Math.ceil(props.frames.length / 1500))
  let last: Sample | undefined
  let previous: TelemetryFrame | undefined
  let index = 0
  for (const frame of props.frames) {
    if (!valid(frame)) {
      previous = undefined
      continue
    }
    const breakBefore = !previous || frame.time - previous.endTime > .5
    if (breakBefore && last && samples.at(-1) !== last) samples.push(last)
    last = { frame, breakBefore }
    if (breakBefore || index % stride === 0) samples.push(last)
    previous = frame
    index++
  }
  if (last && samples.at(-1) !== last) samples.push(last)
  return samples
}
const locations = computed(() => sampleFrames(hasPosition).map(({ frame, breakBefore }, index) => ({
  id: String(index), latitude: frame.latitude, longitude: frame.longitude, time: frame.time,
  breakBefore, maxGap: Infinity, label: '飞行位置', color: '#0f766e',
})))
const altitude = computed(() => sampleFrames(hasAltitude))
const canTrack = computed(() => locations.value.length > 0)
const canChartAltitude = computed(() => altitude.value.length > 1)
const mode = computed<Mode>(() => {
  if (preference.value === 'altitude' && canChartAltitude.value) return 'altitude'
  return canTrack.value ? 'track' : 'altitude'
})
const mapCurrent = computed(() => props.current && hasPosition(props.current)
  ? { latitude: props.current.latitude, longitude: props.current.longitude }
  : null)

function clock(seconds: number) {
  const minutes = Math.floor(seconds / 60)
  const rest = Math.floor(seconds % 60)
  return [minutes, rest].map(value => String(value).padStart(2, '0')).join(':')
}

function drawAltitude() {
  const element = canvas.value
  if (!element) return
  const width = element.clientWidth
  const height = element.clientHeight
  const context = element.getContext('2d')
  if (!context || !width || !height || !altitude.value.length) return
  const ratio = Math.min(window.devicePixelRatio || 1, 2)
  element.width = Math.round(width * ratio)
  element.height = Math.round(height * ratio)
  context.scale(ratio, ratio)

  const points = altitude.value
  const firstTime = points[0].frame.time
  const lastTime = points[points.length - 1].frame.time
  let minHeight = points[0].frame.relAlt
  let maxHeight = minHeight
  for (const { frame } of points) {
    minHeight = Math.min(minHeight, frame.relAlt)
    maxHeight = Math.max(maxHeight, frame.relAlt)
  }
  if (maxHeight - minHeight < 2) {
    const center = (minHeight + maxHeight) / 2
    minHeight = center - 1
    maxHeight = center + 1
  }
  const duration = Math.max(.001, lastTime - firstTime)
  const left = 44
  const top = 24
  const plotWidth = Math.max(1, width - left - 27)
  const plotHeight = Math.max(1, height - top - 29)
  const x = (seconds: number) => left + (seconds - firstTime) / duration * plotWidth
  const y = (meters: number) => top + (maxHeight - meters) / (maxHeight - minHeight) * plotHeight

  const style = getComputedStyle(element)
  const accent = style.getPropertyValue('--accent').trim()
  context.fillStyle = style.getPropertyValue('--muted').trim()
  context.strokeStyle = style.getPropertyValue('--border').trim()
  context.font = '12px system-ui, sans-serif'
  context.fillText('米', 13, 16)
  for (const fraction of [0, .5, 1]) {
    const gridY = top + fraction * plotHeight
    const gridX = left + fraction * plotWidth
    context.beginPath()
    context.moveTo(left, gridY)
    context.lineTo(left + plotWidth, gridY)
    context.moveTo(gridX, top)
    context.lineTo(gridX, top + plotHeight)
    context.stroke()
    context.textAlign = 'right'
    context.fillText(String(+(maxHeight - fraction * (maxHeight - minHeight)).toFixed(1)), left - 7, gridY + 4)
    context.textAlign = 'center'
    const seconds = firstTime + fraction * duration
    context.fillText(duration < 2 ? seconds.toFixed(1) + 's' : clock(seconds), gridX, top + plotHeight + 19)
  }

  altitudePositions = []
  context.strokeStyle = accent
  context.lineWidth = 2.5
  context.lineJoin = 'round'
  context.beginPath()
  for (const { frame, breakBefore } of points) {
    const plotX = x(frame.time)
    altitudePositions.push({ x: plotX, time: frame.time })
    if (breakBefore) context.moveTo(plotX, y(frame.relAlt))
    else context.lineTo(plotX, y(frame.relAlt))
  }
  context.stroke()

  if (props.current && hasAltitude(props.current)) {
    context.beginPath()
    context.arc(x(props.current.time), y(props.current.relAlt), 5, 0, 2 * Math.PI)
    context.fillStyle = accent
    context.fill()
    context.strokeStyle = '#fff'
    context.lineWidth = 2
    context.stroke()
  }
}
function seekFromAltitude(event: MouseEvent) {
  if (!altitudePositions.length) return
  const x = event.clientX - (event.currentTarget as HTMLCanvasElement).getBoundingClientRect().left
  let nearest = altitudePositions[0]
  for (const point of altitudePositions) {
    if (Math.abs(point.x - x) < Math.abs(nearest.x - x)) nearest = point
  }
  emit('seek', nearest.time)
}

watch(canvas, element => {
  observer.disconnect()
  if (element) observer.observe(element)
  drawAltitude()
}, { flush: 'post' })
watch([altitude, () => props.current], drawAltitude, { flush: 'post' })
watch(() => props.frames, () => { preference.value = undefined })
onBeforeUnmount(() => observer.disconnect())
</script>

<template>
  <figure v-if="canTrack || canChartAltitude" class="flight-trace" aria-label="飞行图表">
    <div class="flight-trace-head">
      <h3>{{ mode === 'track' ? '飞行轨迹' : '高度变化' }}</h3>
      <div v-if="canTrack && canChartAltitude" class="flight-trace-switch" role="group" aria-label="图表类型">
        <button :aria-pressed="mode === 'track'" @click="preference = 'track'">轨迹</button>
        <button :aria-pressed="mode === 'altitude'" @click="preference = 'altitude'">高度</button>
      </div>
    </div>
    <div v-if="mode === 'track'" class="flight-map">
      <WallLocationMap :points="locations" :current="mapCurrent" :playback-time="time" compact fill seekable @seek="emit('seek', $event)" />
    </div>
    <canvas v-else ref="canvas" role="img" aria-label="相对起飞点高度随录像时间的变化" @click="seekFromAltitude" />
    <figcaption>
      <span><i class="flight-point" :class="{ 'flight-map-dot': mode === 'track' }" aria-hidden="true" /> 当前画面</span>
      <span v-if="mode === 'altitude'">点击图中位置可回看</span>
      <span v-else-if="locations.length > 1">点击轨迹可回看</span>
    </figcaption>
  </figure>
</template>

<style scoped>
.flight-trace { display: flex; flex-direction: column; flex: 1; min-height: 216px; margin: 11px 0 0; padding: 10px; border: 1px solid var(--border); border-radius: 8px; background: #f7f9f9; }
.flight-trace-head, figcaption { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 5px 9px; }
.flight-trace-head h3 { font-size: 14px; }
.flight-trace-switch { display: flex; gap: 2px; padding: 2px; background: #fff; border: 1px solid var(--border); border-radius: 6px; }
.flight-trace-switch button { padding: 3px 8px; border-radius: 4px; }
.flight-trace-switch button[aria-pressed="true"] { background: var(--accent-bg); color: var(--accent); font-weight: 700; }
canvas, .flight-map { display: block; width: 100%; height: 0; min-height: 156px; flex: 1; margin: 7px 0 6px; }
canvas { cursor: crosshair; }
.flight-map { overflow: hidden; isolation: isolate; border-radius: 6px; }
figcaption { font-size: 12px; color: var(--muted); }
.flight-point { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: var(--accent); }
.flight-map-dot { background: #22d3ee; box-shadow: 0 0 0 1px #0f172a; }
</style>
