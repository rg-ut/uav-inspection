<script setup lang="ts">
import { computed } from 'vue'
import type { FrameDetectionResult } from '../../api1/wallWorkflow'

const props = defineProps<{ frame: FrameDetectionResult }>()
const fontSize = computed(() => Math.max(12, Math.round(props.frame.width * 0.025)))
const labels = computed(() => {
  const { boxes, detections, width, height } = props.frame
  const size = fontSize.value
  return (detections ?? boxes.map(bbox => ({ bbox, label: '疑似缺陷' }))).flatMap(({ bbox, label }) => {
    const [left, top, right, bottom] = bbox
    if (left === undefined || top === undefined || right === undefined || bottom === undefined ||
        !bbox.every(Number.isFinite) || right <= left || bottom <= top) return []
    const labelHeight = size * 1.45
    const labelWidth = Math.min(width, (label.length + 0.65) * size)
    return [{
      left, top, right, bottom, label, labelWidth, labelHeight,
      x: Math.max(0, Math.min(left, width - labelWidth)),
      y: Math.max(0, Math.min(top >= labelHeight ? top - labelHeight : top, height - labelHeight)),
    }]
  })
})
</script>

<template>
  <svg v-if="labels.length" class="wall-detection-overlay"
    :viewBox="'0 0 ' + frame.width + ' ' + frame.height" preserveAspectRatio="xMidYMid meet"
    role="img" aria-label="画面中的缺陷检测框和类别">
    <g v-for="(box, index) in labels" :key="index">
      <rect :x="box.left" :y="box.top" :width="box.right - box.left" :height="box.bottom - box.top"
        fill="none" stroke="#facc15" stroke-width="2" vector-effect="non-scaling-stroke" />
      <rect :x="box.x" :y="box.y" :width="box.labelWidth" :height="box.labelHeight" :rx="fontSize * .16" fill="#facc15" />
      <text :x="box.x + fontSize * .32" :y="box.y + box.labelHeight / 2" dominant-baseline="central"
        :font-size="fontSize" font-weight="700" fill="#171a1d">{{ box.label }}</text>
    </g>
  </svg>
</template>

<style scoped>
.wall-detection-overlay { position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: none; }
</style>
