<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import DeploymentNotice from './components/common/DeploymentNotice.vue'

const noticeVisible = ref(false)
const route = useRoute()
// This scene uses its own model availability notice and does not require inference packages.
const showDeploymentNotice = computed(() => route.name !== 'fendai-flower')

watch([noticeVisible, showDeploymentNotice], ([visible, show]) => {
  document.body.classList.toggle('notice-visible', visible && show)
})
</script>

<template>
  <div class="router-host">
    <router-view></router-view>
  </div>
  <DeploymentNotice v-if="showDeploymentNotice" @visibility-change="noticeVisible = $event" />
</template>

<style>
/* 部署提示悬浮时为页面底部预留空间，防止遮挡正文（移动端尤其明显） */
body.notice-visible .router-host {
  padding-bottom: 140px;
}
</style>
