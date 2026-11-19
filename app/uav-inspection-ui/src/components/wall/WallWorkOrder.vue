<script setup lang="ts">
import { ref, watch } from 'vue'
import type { Finding, Order, OrderAction } from '../../api1/wallWorkflow'

const props = defineProps<{ order: Order; findings: Finding[]; manager: boolean; busy: boolean }>()
const emit = defineEmits<{
  update: [action: OrderAction, data: Record<string, unknown>]
  photo: [kind: 'before' | 'after', file: File]
  seek: [time: number]
}>()

const labels = { draft: '草稿', assigned: '待接单', in_progress: '维修中', review: '待验收', completed: '已完成', cancelled: '已取消' }
const kindLabels: Record<string, string> = { Crack: '裂缝', Seepage: '渗水', TileSpalling: '面砖脱落' }
const assignee = ref('')
const due = ref('')
const priority = ref<'normal' | 'urgent'>('normal')
const instructions = ref('')
const completion = ref('')
const reason = ref('')
const pending = ref<OrderAction | null>(null)

watch(() => props.order.id, () => {
  assignee.value = props.order.assignee
  due.value = props.order.due_on || ''
  priority.value = props.order.priority
  instructions.value = props.order.instructions
  completion.value = props.order.completion
  reason.value = ''
  pending.value = null
}, { immediate: true })
watch(() => props.order.state, () => { pending.value = null; reason.value = '' })

function update(action: OrderAction) {
  const data = { assignee: assignee.value, due_on: due.value || null, priority: priority.value,
    instructions: instructions.value, completion: completion.value, reason: reason.value }
  emit('update', action, data)
}
function selectPhoto(kind: 'before' | 'after', event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (file) emit('photo', kind, file)
  input.value = ''
}
</script>

<template>
  <section class="rounded-lg border border-slate-200 bg-white p-4">
    <header class="flex items-center justify-between gap-4">
      <h3 class="font-semibold">维修工单</h3>
      <span class="text-sm text-teal-800">{{ labels[order.state] }}</span>
    </header>
    <p class="mt-1 text-sm text-slate-500">编号 {{ order.id.slice(0, 8) }} · {{ order.finding_ids.length }} 处缺陷</p>
    <p class="mt-2 text-xs text-slate-500">缺陷位置按画面方位标注（上部/中部/下部 × 左侧/中央/右侧），请结合楼层与现场构件定位复核。</p>
    <ul class="mt-3 space-y-1 text-sm">
      <li v-for="item in findings.filter(finding => order.finding_ids.includes(finding.id))" :key="item.id">
        <button class="text-teal-800 underline" @click="emit('seek', item.evidence_time)">
          {{ kindLabels[item.kind] || '缺陷' }} · {{ item.zone || '方位待核实' }} · {{ item.floor || '楼层待核实' }} · 查看 {{ item.evidence_time.toFixed(1) }} 秒处画面
        </button>
        <span v-if="item.note"> · {{ item.note }}</span>
      </li>
    </ul>
    <div v-if="manager && order.state === 'draft'" class="mt-4 grid gap-3 text-sm sm:grid-cols-2">
      <label>维修人员账号<input v-model.trim="assignee" class="field" /></label>
      <label>截止日期<input v-model="due" type="date" class="field" /></label>
      <label>优先级<select v-model="priority" class="field"><option value="normal">常规</option><option value="urgent">优先处理</option></select></label>
      <label class="sm:col-span-2">处置要求<textarea v-model="instructions" class="field" rows="2" /></label>
    </div>
    <dl v-else class="mt-4 grid gap-2 text-sm sm:grid-cols-2">
      <div><dt class="text-slate-500">维修人员</dt><dd>{{ order.assignee || '—' }}</dd></div>
      <div><dt class="text-slate-500">截止日期</dt><dd>{{ order.due_on || '—' }}</dd></div>
      <div><dt class="text-slate-500">优先级</dt><dd>{{ order.priority === 'urgent' ? '优先处理' : '常规' }}</dd></div>
      <div v-if="order.instructions"><dt class="text-slate-500">处置要求</dt><dd>{{ order.instructions }}</dd></div>
    </dl>
    <div v-if="order.state === 'in_progress'" class="mt-4 grid gap-3 text-sm">
      <label>完成说明<textarea v-model="completion" class="field" rows="2" /></label>
      <label>维修前照片<input type="file" accept="image/*" class="field" :disabled="busy" @change="selectPhoto('before', $event)" /></label>
      <label>维修后照片<input type="file" accept="image/*" class="field" :disabled="busy" @change="selectPhoto('after', $event)" /></label>
    </div>
    <p v-else-if="order.completion" class="mt-4 text-sm">{{ order.completion }}</p>
    <div v-if="order.photos.length" class="mt-3 flex flex-wrap gap-3">
      <a v-for="photo in order.photos" :key="photo.url" :href="photo.url" target="_blank" class="text-sm text-teal-800 underline">
        {{ photo.kind === 'before' ? '查看维修前照片' : '查看维修后照片' }}
      </a>
    </div>
    <div v-if="pending" class="mt-4 border-t border-slate-200 pt-3">
      <p v-if="pending === 'dispatch'" class="mb-2 text-sm">确认将这张工单派给 {{ assignee || '指定人员' }}。</p>
      <label v-else class="text-sm">原因<input v-model.trim="reason" class="field" /></label>
      <div class="mt-2 flex gap-2">
        <button class="primary" :disabled="busy" @click="update(pending)">确认{{ pending === 'dispatch' ? '派发' : pending === 'return' ? '退回' : '取消' }}</button>
        <button class="button" @click="pending = null">返回</button>
      </div>
    </div>
    <div v-else class="mt-4 flex flex-wrap gap-2">
      <template v-if="manager && order.state === 'draft'">
        <button class="primary" :disabled="busy" @click="pending = 'dispatch'">派发工单</button>
        <button class="button" :disabled="busy" @click="update('save')">保存草稿</button>
      </template>
      <button v-if="order.state === 'assigned'" class="primary" :disabled="busy" @click="update('accept')">接单</button>
      <button v-if="order.state === 'in_progress'" class="primary" :disabled="busy" @click="update('submit')">提交验收</button>
      <template v-if="manager && order.state === 'review'">
        <button class="primary" :disabled="busy" @click="update('complete')">确认完成</button>
        <button class="button" :disabled="busy" @click="pending = 'return'">退回维修</button>
      </template>
      <button v-if="manager && !['completed', 'cancelled'].includes(order.state)" class="button" :disabled="busy" @click="pending = 'cancel'">取消工单</button>
    </div>
  </section>
</template>

<style scoped>
.field { display: block; width: 100%; margin-top: .25rem; padding: .5rem; border: 1px solid #cbd5e1; border-radius: .375rem; }
.button,.primary { min-height: 2.5rem; border: 1px solid #cbd5e1; border-radius: .375rem; padding: .4rem .8rem; font-size: .875rem; }
.primary { color: white; background: #0f766e; border-color: #0f766e; }
button:disabled { opacity: .5; }
</style>
