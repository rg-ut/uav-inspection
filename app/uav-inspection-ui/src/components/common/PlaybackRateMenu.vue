<script setup lang="ts">
/**
 * 播放倍速选择器。
 *
 * 2026-11-20 新增：四宫格的每一格右上角都挂一个，放大观看时顶栏也挂一个。
 * 倍速是**全局**的——四格共用同一条时间轴，一格快一格慢就不同步了，所以四个入口
 * 改的是同一个 playbackRate，选中值在所有格子上同步回显。
 *
 * 菜单弹到 body 上：格子本身是 overflow:hidden 的裁剪容器（视频要用它裁 RViz 画面），
 * 菜单若留在格子里会被裁掉，只能 teleport + fixed 定位。
 */
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { Check, ChevronDown } from 'lucide-vue-next'

withDefaults(defineProps<{
  modelValue: number
  rates?: number[]
  compact?: boolean
}>(), {
  rates: () => [0.5, 1, 1.5, 2, 4],
  compact: false,
})

const emit = defineEmits<{
  'update:modelValue': [value: number]
}>()

const open = ref(false)
const menuStyle = ref<Record<string, string>>({})

function toggle(event: MouseEvent) {
  const rect = (event.currentTarget as HTMLElement).getBoundingClientRect()
  const right = Math.max(8, window.innerWidth - rect.right)
  // 菜单大概 100 px 高、5 项；下方放不下就翻到按钮上方
  const openUp = rect.bottom + 150 > window.innerHeight
  menuStyle.value = openUp
    ? { right: `${right}px`, bottom: `${window.innerHeight - rect.top + 6}px` }
    : { right: `${right}px`, top: `${rect.bottom + 6}px` }
  open.value = !open.value
}

function pick(value: number) {
  emit('update:modelValue', value)
  open.value = false
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') open.value = false
}

onMounted(() => window.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown))
</script>

<template>
  <div class="relative">
    <button
      type="button"
      class="inline-flex items-center gap-0.5 rounded border border-white/25 bg-slate-900/75 font-mono font-semibold text-slate-100 backdrop-blur transition hover:border-indigo-400 hover:bg-indigo-600"
      :class="compact ? 'h-5 px-1 text-[9.5px]' : 'h-7 px-1.5 text-[11px]'"
      :title="`播放倍速：${modelValue}×（四个窗格同步）`"
      :aria-label="`播放倍速 ${modelValue} 倍`"
      @click="toggle"
    >
      {{ modelValue }}×
      <ChevronDown :size="compact ? 9 : 11" />
    </button>

    <Teleport to="body">
      <div v-if="open" class="fixed inset-0 z-[95]" @click="open = false" />
      <div
        v-if="open"
        class="fixed z-[96] w-[86px] rounded-md border border-slate-700 bg-slate-900/95 p-0.5 shadow-2xl"
        :style="menuStyle"
      >
        <div class="px-1.5 pb-1 pt-0.5 text-[9px] tracking-wide text-slate-500">倍速 · 全窗格</div>
        <button
          v-for="rate in rates"
          :key="rate"
          type="button"
          class="flex w-full items-center justify-between rounded px-1.5 py-1 font-mono text-[11px] transition"
          :class="rate === modelValue ? 'bg-indigo-600 text-white' : 'text-slate-300 hover:bg-slate-800'"
          @click="pick(rate)"
        >
          {{ rate }}×
          <Check v-if="rate === modelValue" :size="10" />
        </button>
      </div>
    </Teleport>
  </div>
</template>
