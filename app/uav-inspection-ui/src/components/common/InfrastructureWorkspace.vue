<script setup lang="ts">
import { ref } from 'vue'
import { ArrowLeft, Building2, History, RadioTower, UtilityPole } from 'lucide-vue-next'

const props = defineProps<{ kind: 'roof' | 'power' | 'telecom'; title: string; task?: string; status: string }>()
const historyDialog = ref<HTMLDialogElement>()
function closeHistory() { historyDialog.value?.close() }
const icons = { roof: Building2, power: UtilityPole, telecom: RadioTower }
</script>

<template>
  <div class="infra-workspace" :data-kind="props.kind">
    <header class="infra-header">
      <div>
        <RouterLink to="/" class="infra-home"><ArrowLeft :size="15" /> 平台首页</RouterLink>
        <h1><component :is="icons[kind]" :size="25" aria-hidden="true" />{{ title }}</h1>
      </div>
      <div class="infra-actions">
        <button v-if="$slots.history" class="infra-button" aria-haspopup="dialog" @click="historyDialog?.showModal()"><History :size="16" /> 历史任务</button>
        <slot name="actions" />
      </div>
    </header>
    <main class="infra-main">
      <section class="infra-primary" aria-label="巡检录像">
        <div class="infra-task">
          <div><span>当前任务</span><strong>{{ task || '尚未选择任务' }}</strong></div>
          <span class="infra-status">{{ status }}</span>
        </div>
        <slot />
      </section>
      <aside class="infra-panel infra-rail"><slot name="results" /></aside>
    </main>
    <dialog v-if="$slots.history" ref="historyDialog" class="infra-dialog infra-history-dialog" aria-labelledby="infra-history-title">
      <div class="infra-dialog-head">
        <h2 id="infra-history-title">历史任务</h2>
        <button class="infra-button" @click="closeHistory">关闭</button>
      </div>
      <slot name="history" :close="closeHistory" />
    </dialog>
    <slot name="dialogs" />
  </div>
</template>

<style>
.infra-workspace {
  --accent: #146454; --accent-bg: #e5f4ee; --primary: #146454;
  --surface: #fff; --border: #d9e2df; --muted: #52645e;
  min-height: 100vh; display: flex; flex-direction: column; background: #f3f6f5; color: #172a27;
  font-size: 14px;
}
.infra-workspace[data-kind=power] { --accent: #885313; --accent-bg: #fbf0dd; --primary: #80501c; --border: #e3dfd6; --muted: #5e5b51; background: #f6f5f1; }
.infra-workspace[data-kind=telecom] { --accent: #23598e; --accent-bg: #e7f0fc; --primary: #23598e; --border: #dce4ed; --muted: #4e6176; background: #f3f6fa; }
.infra-header, .infra-main { width: 100%; padding-inline: clamp(12px, 1.6vw, 28px); }
.infra-header { display: flex; align-items: end; justify-content: space-between; flex-wrap: wrap; gap: 10px 20px; padding-block: 13px; flex: 0 0 auto; }
.infra-header h1 { display: flex; align-items: center; gap: 10px; font-size: clamp(23px, 1.9vw, 31px); font-weight: 750; line-height: 1.2; margin: 7px 0 0; }
.infra-header h1 svg, .infra-home { color: var(--accent); }
.infra-home { display: inline-flex; gap: 5px; align-items: center; }
.infra-actions { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.infra-main { display: grid; grid-template-columns: minmax(0, 1fr) clamp(330px, 30%, 530px); align-items: stretch; gap: 14px; padding-bottom: 16px; flex: 1; }
.infra-primary { display: grid; min-width: 0; gap: 10px; }
.infra-task, .infra-panel { background: var(--surface); border: 1px solid var(--border); border-radius: 11px; }
.infra-task { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 11px 14px; border-left: 4px solid var(--accent); }
.infra-task > div { display: flex; flex-wrap: wrap; gap: 3px 10px; align-items: baseline; min-width: 0; }
.infra-task > div span, .infra-muted { color: var(--muted); }
.infra-task strong { overflow-wrap: anywhere; }
.infra-status { flex-shrink: 0; border-radius: 5px; background: var(--accent-bg); color: var(--accent); font-weight: 650; padding: 4px 8px; }
.infra-panel { padding: 14px; min-width: 0; }
.infra-panel h2 { font-size: 17px; font-weight: 750; margin: 0 0 8px; }
.infra-panel h3, .infra-history-dialog h3 { font-weight: 700; margin: 0; }
.infra-rail { min-height: 350px; display: grid; align-content: start; gap: 12px; }
.infra-rail:has(.replay-flight) { align-self: start; min-height: 0; }
.infra-rail:has(.flight-trace) { align-self: stretch; grid-template-rows: auto minmax(min-content, 1fr); align-content: stretch; }
.infra-button { display: inline-flex; align-items: center; justify-content: center; gap: 5px; min-height: 36px; border: 1px solid var(--border); border-radius: 6px; padding: 5px 11px; background: white; font: inherit; font-weight: 650; color: inherit; cursor: pointer; text-decoration: none; }
.infra-button:hover:not(:disabled) { border-color: var(--accent); color: var(--accent); }
.infra-button--primary, .infra-button--primary:hover:not(:disabled) { border-color: var(--primary); background: var(--primary); color: white; }
.infra-button:disabled { cursor: default; opacity: .48; }
.infra-workspace :is(button,a,input,select,textarea):focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.infra-workspace .inspection-video-frame { height: clamp(345px, 57vh, 670px); min-height: 0; }
.infra-workspace .infra-video { position: relative; width: 100%; height: clamp(345px, 57vh, 670px); border-radius: 11px; overflow: hidden; display: grid; place-items: center; background: #182321; color: #eef5f2; }
.infra-video video { width: 100%; height: 100%; object-fit: contain; }
.infra-telemetry { display: flex; flex-wrap: wrap; gap: 6px 22px; padding-block: 9px; }
.infra-telemetry strong { margin-left: 5px; font-variant-numeric: tabular-nums; }
.infra-metrics { display: grid; grid-auto-flow: column; grid-auto-columns: 1fr; gap: 7px; }
.infra-metrics > div { display: grid; gap: 3px; border-radius: 7px; background: var(--accent-bg); color: var(--accent); padding: 9px; }
.infra-metrics strong { font-size: 23px; font-variant-numeric: tabular-nums; line-height: 1; }
.infra-empty { border: 1px dashed var(--border); border-radius: 8px; color: var(--muted); padding: 16px 12px; }
.infra-list { display: grid; gap: 7px; max-height: 50vh; overflow-y: auto; }
.infra-row { border: 1px solid var(--border); border-radius: 7px; padding: 8px 10px; display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.infra-form { display: grid; gap: 12px; }
.infra-form label { display: grid; gap: 5px; font-weight: 600; }
.infra-input { background: #fff; border: 1px solid var(--border); border-radius: 6px; padding: 7px 9px; min-width: 0; width: 100%; color: inherit; font: inherit; }
.infra-dialog { width: min(475px, calc(100% - 28px)); max-height: 90vh; margin: auto; padding: 20px; border: 1px solid var(--border); border-radius: 12px; background: white; color: #172a27; }
.infra-dialog::backdrop { background: #15262275; }
.infra-dialog h2 { margin: 0 0 16px; font-size: 21px; font-weight: 750; }
.infra-history-dialog { width: min(770px, calc(100% - 28px)); }
.infra-dialog-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; gap: 10px; }
.infra-dialog-head h2 { margin: 0; }
.infra-history-dialog .infra-list { max-height: min(65dvh, 580px); }
.infra-history-dialog .infra-row > div { min-width: 0; overflow-wrap: anywhere; }
.infra-history-dialog .infra-row .infra-actions { flex-shrink: 0; }
.infra-dialog footer { display: flex; justify-content: end; gap: 8px; margin-top: 10px; }
.infra-message { color: var(--accent); background: var(--accent-bg); border-radius: 6px; padding: 8px 10px; }
.infra-warning { color: #7a351e; background: #fff1e7; border-radius: 6px; padding: 8px 10px; }
@media (min-width: 881px) and (min-height: 550px) {
  .infra-workspace { height: 100vh; height: 100dvh; min-height: 0; overflow: hidden; }
  .infra-main { min-height: 0; }
  .infra-primary { min-height: 0; grid-template-rows: auto minmax(0, 1fr); grid-auto-rows: auto; }
  .infra-rail { min-height: 0; overflow-y: auto; }
  .infra-rail .infra-list { max-height: none; overflow: visible; }
  .infra-workspace .inspection-video-frame, .infra-workspace .infra-video { height: 100%; min-height: 0; }
}
@media (max-width: 880px) {
  .infra-main { grid-template-columns: minmax(0, 1fr); }
  .infra-rail { min-height: 0; }
  .infra-history-dialog .infra-row { align-items: start; flex-direction: column; }
  .infra-workspace .inspection-video-frame, .infra-workspace .infra-video { height: clamp(250px, 51vw, 500px); }
}
</style>
