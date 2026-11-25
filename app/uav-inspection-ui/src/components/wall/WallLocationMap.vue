<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'

interface LocationPoint {
  id: string
  latitude: number
  longitude: number
  time: number
  label: string
  color: string
  maxGap?: number
}

const props = defineProps<{
  compact?: boolean
  fill?: boolean
  points: LocationPoint[]
  current: { latitude: number; longitude: number } | null
}>()

const containerRef = ref<HTMLDivElement | null>(null)
let map: L.Map | null = null
let overlay: L.LayerGroup | null = null
let resizeObserver: ResizeObserver | null = null
let currentMarker: L.CircleMarker | null = null
let initialPoint = true
const following = ref(true)
const basemapState = ref<'loading' | 'ready' | 'unavailable'>('loading')
let basemapTimer: number | undefined
const basemapTimeout = 12000

const basemaps = [
  { url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    attribution: 'Esri World Imagery', maxNativeZoom: 19 },
  { url: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>', maxNativeZoom: 19 },
]

function loadBasemap(index = 0) {
  if (!map) return
  const source = basemaps[index]
  if (!source) {
    basemapState.value = 'unavailable'
    return
  }
  const tiles = L.tileLayer(source.url, { ...source, maxZoom: 20 })
  let loaded = false
  const fallback = () => {
    if (loaded || !map?.hasLayer(tiles)) return
    clearTimeout(basemapTimer)
    tiles.remove()
    loadBasemap(index + 1)
  }
  tiles.on('tileload', () => {
    loaded = true
    basemapState.value = 'ready'
    clearTimeout(basemapTimer)
  })
  tiles.on('loading', () => {
    loaded = false
    clearTimeout(basemapTimer)
    basemapTimer = window.setTimeout(fallback, basemapTimeout)
  })
  // Leaflet still uses the layer after dispatching load.
  tiles.on('load', () => queueMicrotask(fallback))
  tiles.addTo(map)
}

function updateCurrent() {
  if (!map) return
  if (!props.current) {
    currentMarker?.remove()
    currentMarker = null
    return
  }
  const coordinate: L.LatLngTuple = [props.current.latitude, props.current.longitude]
  if (currentMarker) currentMarker.setLatLng(coordinate)
  else currentMarker = L.circleMarker(coordinate, {
    radius: 9, color: '#0f172a', weight: 2, fillColor: '#22d3ee', fillOpacity: 1,
  }).bindTooltip('无人机拍摄位置').addTo(map)
  if (initialPoint && props.compact && !props.points.length) {
    initialPoint = false
    map.setView(coordinate, 18, { animate: false })
  } else if (props.compact && following.value && !map.getBounds().pad(-0.2).contains(coordinate)) {
    map.panTo(coordinate, { animate: false })
  }
}

function follow() {
  following.value = true
  if (props.current) map?.panTo([props.current.latitude, props.current.longitude], { animate: false })
}

function renderPoints() {
  if (!map || !overlay) return
  overlay.clearLayers()
  const bounds: L.LatLngExpression[] = []

  if (props.points.length > 1) {
    let route: L.LatLngTuple[] = []
    let previous: number | undefined
    for (const point of props.points) {
      if (props.compact && previous !== undefined && point.time - previous > (point.maxGap || 3)) {
        if (route.length > 1) L.polyline(route, { color: '#0f766e', weight: 3 }).addTo(overlay)
        route = []
      }
      route.push([point.latitude, point.longitude])
      previous = point.time
    }
    if (route.length > 1) L.polyline(route, { color: '#0f766e', weight: 3 }).addTo(overlay)
  }
  for (const point of props.points) {
    const coordinate: L.LatLngTuple = [point.latitude, point.longitude]
    bounds.push(coordinate)
    if (props.compact && (props.points.length > 1 || props.current)) continue
    L.circleMarker(coordinate, {
      radius: 7,
      color: '#ffffff',
      weight: 2,
      fillColor: point.color,
      fillOpacity: 0.95,
    }).bindTooltip(`${point.label} · ${point.time.toFixed(1)}s`).addTo(overlay)
  }
  if (props.current) {
    const coordinate: L.LatLngTuple = [props.current.latitude, props.current.longitude]
    bounds.push(coordinate)
    updateCurrent()
  }

  if (bounds.length > 1) map.fitBounds(L.latLngBounds(bounds), { padding: [36, 36], maxZoom: 19, animate: false })
  else if (bounds.length === 1) map.setView(bounds[0], 18, { animate: false })
}

onMounted(async () => {
  await nextTick()
  if (!containerRef.value) return
  map = L.map(containerRef.value, { zoomControl: true, attributionControl: true, zoomAnimation: false }).setView([24.8334, 102.8399], 16)
  loadBasemap()
  L.control.scale({ imperial: false, maxWidth: 80 }).addTo(map)
  overlay = L.layerGroup().addTo(map)
  map.on('dragstart', () => { following.value = false })
  resizeObserver = new ResizeObserver(() => map?.invalidateSize({ pan: false, debounceMoveend: true }))
  resizeObserver.observe(containerRef.value)
  renderPoints()
})

watch(() => props.points, renderPoints, { deep: true })
watch(() => props.current, updateCurrent)

onUnmounted(() => {
  clearTimeout(basemapTimer)
  resizeObserver?.disconnect()
  resizeObserver = null
  map?.stop()
  map?.remove()
  map = null
})
</script>

<template>
  <div class="relative w-full" :class="{ 'h-full': fill }">
    <div ref="containerRef" class="w-full" :class="fill ? 'h-full' : compact ? 'h-56' : 'min-h-[540px]'" aria-label="拍摄位置地图"></div>
    <p v-if="basemapState !== 'ready'" role="status" class="absolute left-2 top-2 z-[500] ml-9 rounded bg-white px-2 py-1 text-xs">
      {{ basemapState === 'loading' ? '正在加载底图' : '底图暂不可用 · 显示拍摄轨迹' }}
    </p>
    <button v-if="compact && !following && current" type="button"
      class="absolute bottom-5 right-2 z-[500] rounded border border-slate-300 bg-white px-2 py-1 text-xs"
      @click="follow">回到无人机位置</button>
  </div>
</template>

<style scoped>
:deep(.leaflet-container) {
  font-family: "Microsoft YaHei", sans-serif;
  background-color: #e8f0f0;
  background-image: linear-gradient(#cbdcde 1px, transparent 1px), linear-gradient(90deg, #cbdcde 1px, transparent 1px);
  background-size: 24px 24px;
}
</style>
