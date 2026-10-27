<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { Info, RefreshCw, X } from 'lucide-vue-next'
import { useRoute } from 'vue-router'
import { webPath } from '../../utils/webroot'

const emit = defineEmits<{ (e: 'visibility-change', visible: boolean): void }>()

interface DeploymentStatus {
  data_available: boolean
  inference_available: boolean
  modules?: Record<string, ModuleStatus>
}

interface ModuleStatus {
  title: string
  route: string
  mode: string
  configured: boolean
  missing: { label: string; category: string; subdir: string }[]
  note: string
}

const route = useRoute()

const status = ref<DeploymentStatus | null>(null)
const serviceUnavailable = ref(false)
const dismissed = ref(false)
const checking = ref(false)
let activeRequest: AbortController | null = null

const currentModule = computed(() => Object.values(status.value?.modules ?? {}).find(item => item.route === route.path.replace(/\/$/, '')))
const incompleteModules = computed(() => Object.values(status.value?.modules ?? {}).filter(item => !item.configured))
const visible = computed(() => !dismissed.value && (serviceUnavailable.value || (status.value?.modules
  ? Boolean(currentModule.value && (!currentModule.value.configured || ['demo', 'offline-results'].includes(currentModule.value.mode))) || route.path === '/' && incompleteModules.value.length > 0
  : Boolean(status.value && (!status.value.data_available || !status.value.inference_available)))))
const title = computed(() => {
  if (serviceUnavailable.value) return '数据服务暂未连接'
  if (currentModule.value) return `${currentModule.value.title} · ${currentModule.value.mode === 'demo' ? '演示模式' : currentModule.value.configured ? '离线结果回放' : '待补齐配置'}`
  return status.value?.modules ? `${incompleteModules.value.length} 个模块待补齐数据或运行环境` : '基础部署模式'
})
const message = computed(() => {
  if (serviceUnavailable.value) return '页面仍可浏览。请启动后端服务后重新检查；无需先安装视频数据包。'
  if (currentModule.value) return currentModule.value.note
  if (status.value?.modules) return '展开查看各模块缺项。粉黛花场景可用不代表其他业务的数据与 AI 已就绪。'
  const notices: string[] = []
  if (!status.value?.data_available) notices.push('未安装可选数据包，视频和测量结果暂为空；可以先浏览页面，或上传自己的视频。')
  if (!status.value?.inference_available) notices.push('AI 检测需另行安装推理依赖与对应模型。')
  return notices.join(' ')
})

function uploadTarget(item: { category: string; subdir: string }) {
  return { path: '/assets', query: { category: item.category, subdir: item.subdir } }
}

async function checkStatus() {
  if (checking.value) return
  checking.value = true
  activeRequest = new AbortController()
  const timeout = window.setTimeout(() => activeRequest?.abort(), 5000)
  try {
    const response = await fetch(webPath('/api/deployment/status'), { signal: activeRequest.signal })
    if (!response.ok) throw new Error('Status unavailable')
    const result = await response.json()
    if (result.code !== 200 || typeof result.data?.data_available !== 'boolean' || typeof result.data?.inference_available !== 'boolean') {
      throw new Error('Invalid status response')
    }
    status.value = result.data
    serviceUnavailable.value = false
  } catch {
    serviceUnavailable.value = true
  } finally {
    window.clearTimeout(timeout)
    activeRequest = null
    checking.value = false
  }
}

onMounted(checkStatus)
onUnmounted(() => activeRequest?.abort())
watch(() => route.path, () => { dismissed.value = false; void checkStatus() })

watch(visible, (v) => emit('visibility-change', v === true), { immediate: true })
</script>

<template>
  <aside v-if="visible" class="deployment-notice" role="status" aria-live="polite">
    <Info :size="19" class="deployment-notice-icon" aria-hidden="true" />
    <div class="deployment-notice-content">
      <strong>{{ title }}</strong>
      <p>{{ message }}</p>
      <ul v-if="currentModule?.missing.length" class="deployment-notice-items">
        <li v-for="item in currentModule.missing" :key="item.label">
          {{ item.label }}
          <RouterLink v-if="item.category" :to="uploadTarget(item)">去上传</RouterLink>
        </li>
      </ul>
      <details v-else-if="!currentModule && incompleteModules.length" class="deployment-notice-details">
        <summary>查看模块缺项</summary>
        <div class="deployment-notice-module-list">
          <div v-for="item in incompleteModules" :key="item.route">
            <RouterLink :to="item.route">{{ item.title }}</RouterLink>
            <p>{{ item.missing.map(missing => missing.label).join('；') }}</p>
          </div>
        </div>
      </details>
      <button type="button" class="deployment-notice-retry" :disabled="checking" @click="checkStatus">
        <RefreshCw :size="13" :class="{ 'animate-spin': checking }" aria-hidden="true" />
        {{ checking ? '检查中…' : '重新检查' }}
      </button>
    </div>
    <button type="button" class="deployment-notice-close" aria-label="关闭部署提示" @click="dismissed = true"><X :size="17" /></button>
  </aside>
</template>

<style scoped>
.deployment-notice { position: fixed; z-index: 90; right: 16px; bottom: 16px; display: flex; align-items: flex-start; gap: 10px; width: min(460px, calc(100vw - 32px)); padding: 14px; border: 1px solid #bfdbfe; border-radius: 12px; background: #eff6ff; color: #1e3a8a; box-shadow: 0 4px 18px #0f172a12; font-size: 12px; }
.deployment-notice-icon { flex-shrink: 0; margin-top: 1px; }
.deployment-notice-content { flex: 1; min-width: 0; }
.deployment-notice-content strong { font-size: 13px; }
.deployment-notice-content p { margin: 4px 0 0; line-height: 1.7; }
.deployment-notice-items { margin: 6px 0; padding-left: 18px; list-style: disc; max-height: 28vh; overflow: auto; }
.deployment-notice-items li { margin: 5px 0; }
.deployment-notice-content a { margin-left: 5px; text-decoration: underline; font-weight: 600; }
.deployment-notice-details { margin-top: 8px; }
.deployment-notice-details summary { cursor: pointer; }
.deployment-notice-module-list { max-height: 32vh; overflow: auto; margin-top: 8px; }
.deployment-notice-module-list > div { margin-bottom: 10px; }
.deployment-notice-retry { display: inline-flex; align-items: center; gap: 5px; margin-top: 7px; font-weight: 600; cursor: pointer; }
.deployment-notice-retry:disabled { opacity: 0.6; cursor: wait; }
.deployment-notice-close { flex-shrink: 0; padding: 2px; cursor: pointer; }
</style>
