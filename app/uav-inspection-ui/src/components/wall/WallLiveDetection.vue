<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { request } from '../../utils/request'
import { wallWorkflow, type FrameDetectionResult, type ModelOption } from '../../api1/wallWorkflow'

import WallDetectionOverlay from './WallDetectionOverlay.vue'

const props = defineProps<{ video: HTMLVideoElement | null; deviceId: string; connected: boolean }>()
const models = ref<ModelOption[]>([])
const model = ref('')
const sensitivity = ref(0.75)
const enabled = ref(false)
const unavailable = ref(false)
const preview = ref<FrameDetectionResult | null>(null)
const canvas = document.createElement('canvas')
const context = canvas.getContext('2d')
let scan: number | undefined
let pending = false
let generation = 0
let expires: number | undefined

watch(() => [enabled.value, props.connected, props.deviceId, model.value, sensitivity.value], () => {
  generation++
  preview.value = null
  unavailable.value = false
})

async function scanFrame() {
  const element = props.video
  if (!enabled.value || !props.connected || !element || pending || !model.value || !context ||
      !element.videoWidth || element.paused || element.readyState < 2) return
  const active = generation
  const frameTime = element.currentTime
  const ratio = Math.min(1, 960 / Math.max(element.videoWidth, element.videoHeight))
  canvas.width = Math.round(element.videoWidth * ratio)
  canvas.height = Math.round(element.videoHeight * ratio)
  context.drawImage(element, 0, 0, canvas.width, canvas.height)
  const image = canvas.toDataURL('image/jpeg', 0.74).split(',')[1]
  pending = true
  try {
    const result = await request<FrameDetectionResult>({
      url: '/wall/workflow/live/' + props.deviceId + '/detect', method: 'POST',
      data: { model: model.value, sensitivity: sensitivity.value, image }, timeout: 2500,
    })
    if (active !== generation) return
    unavailable.value = false
    preview.value = element.currentTime - frameTime <= 0.4 ? result.data : null
    clearTimeout(expires)
    expires = window.setTimeout(() => { preview.value = null }, 400)
  } catch (failure) {
    if (active !== generation) return
    preview.value = null
    unavailable.value = (failure as { response?: { status?: number } }).response?.status !== 429
  } finally {
    pending = false
  }
}

onMounted(async () => {
  scan = window.setInterval(() => { void scanFrame() }, 100)
  try {
    const data = await wallWorkflow.models()
    models.value = data.models
    model.value = data.models[0]?.id || ''
  } catch {
    unavailable.value = true
  }
})
onUnmounted(() => {
  generation++
  clearInterval(scan)
  clearTimeout(expires)
})
</script>

<template>
  <Teleport v-if="video?.parentElement" :to="video.parentElement">
    <WallDetectionOverlay v-if="preview" :frame="preview" />
  </Teleport>
  <div class="mt-3 flex flex-wrap items-end gap-3 text-sm">
    <label>检测模型<select v-model="model" class="mt-1 block rounded border border-slate-300 px-2 py-1">
      <option value="">请选择模型</option><option v-for="item in models" :key="item.id" :value="item.id">{{ item.name }}</option>
    </select></label>
    <label>检测灵敏度 {{ Math.round(sensitivity * 100) }}%
      <input v-model.number="sensitivity" type="range" min="0.05" max="0.95" step="0.01" class="mt-2 block accent-teal-700" />
    </label>
    <button class="rounded border border-slate-300 px-3 py-2" :disabled="!model || !connected"
      @click="enabled = !enabled">{{ enabled ? '暂停检测' : '开始检测' }}</button>
    <p v-if="unavailable" role="status">暂时无法检测，直播画面仍可查看。</p>
  </div>
</template>
