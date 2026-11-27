import { createApp } from 'vue'
import './style.css'
import App from './App.vue'
import router from './router' // 引入路由实例

const app = createApp(App)

// 关键步骤：使用路由插件
app.use(router)

app.mount('#app')

// 版本更新后，仍开着旧标签页的用户点击懒加载路由时，旧的内容哈希 chunk 已不存在，
// Vite 会派发 vite:preloadError；此处整页刷新以载入新入口，并用 sessionStorage 防刷新死循环。
window.addEventListener('vite:preloadError', (event) => {
  event.preventDefault()
  const key = 'uav-preload-reloaded'
  if (sessionStorage.getItem(key)) {
    sessionStorage.removeItem(key)
    return
  }
  sessionStorage.setItem(key, '1')
  window.location.reload()
})
