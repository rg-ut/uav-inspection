<script setup lang="ts">
/**
 * 播放控制条：播放/暂停 + 进度 + 时间。
 *
 * 2026-11-20 新增。原先这段写在巡检过程页底部，只服务于页面级时间轴；
 * 现在放大观看是整屏覆盖，底部那条被盖住，所以抽成组件复用两处：
 * 页面底部（浅色底）与放大浮层底部（深色底）。
 */
import { computed } from 'vue'
import { Pause, Play } from 'lucide-vue-next'

const props = withDefaults(defineProps<{
  playing: boolean
  current: number
  duration: number
  dark?: boolean
}>(), {
  dark: false,
})

const emit = defineEmits<{
  toggle: []
  seek: [value: number]
}>()

function fmt(sec: number) {
  const total = Math.max(0, Math.round(Number.isFinite(sec) ? sec : 0))
  const m = Math.floor(total / 60)
  return `${m}:${String(total % 60).padStart(2, '0')}`
}

const safeMax = computed(() => Math.max(1, Math.round(props.duration || 0)))

function onInput(event: Event) {
  emit('seek', Number((event.target as HTMLInputElement).value))
}
</script>

<template>
  <div class="flex min-w-0 items-center gap-2">
    <button
      type="button"
      class="grid h-7 w-7 shrink-0 place-items-center rounded-full text-white transition"
      :class="dark ? 'bg-indigo-500 hover:bg-indigo-400' : 'bg-slate-900 hover:bg-slate-700'"
      :title="playing ? '暂停' : '播放'"
      :aria-label="playing ? '暂停' : '播放'"
      @click="emit('toggle')"
    >
      <Pause v-if="playing" :size="13" />
      <Play v-else :size="13" />
    </button>
    <input
      type="range" min="0" :max="safeMax" step="1"
      class="min-w-0 flex-1"
      :class="dark ? 'accent-indigo-400' : 'accent-indigo-600'"
      :value="Math.round(current)"
      aria-label="播放进度"
      @input="onInput"
    />
    <span class="shrink-0 font-mono text-[10px]" :class="dark ? 'text-slate-400' : 'text-slate-500'">
      {{ fmt(current) }} / {{ fmt(duration) }}
    </span>
  </div>
</template>
