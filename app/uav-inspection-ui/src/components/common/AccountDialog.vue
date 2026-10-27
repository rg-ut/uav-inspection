<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref, watch } from 'vue'
import { X, LogIn, UserPlus, Eye, EyeOff, UserRound, LockKeyhole, ArrowRight } from 'lucide-vue-next'
import { signIn } from '../../stores/auth'
const props=defineProps<{mode:'login'|'register'}>()
const emit=defineEmits<{close:[];success:[];mode:[value:'login'|'register']}>()
const username=ref(''),password=ref(''),confirmation=ref(''),showPassword=ref(false),busy=ref(false),error=ref(''),input=ref<HTMLInputElement|null>(null)
const isRegister=computed(()=>props.mode==='register')
watch(()=>props.mode,()=>{password.value='';confirmation.value='';error.value='';showPassword.value=false;input.value?.focus()})
onMounted(()=>input.value?.focus())
function close(){if(!busy.value)emit('close')}
function onKey(event:KeyboardEvent){if(event.key==='Escape')close()}
onMounted(()=>document.addEventListener('keydown',onKey))
onBeforeUnmount(()=>document.removeEventListener('keydown',onKey))
async function submit(){
  error.value=''
  if(isRegister.value && !/^[A-Za-z0-9_]{3,24}$/.test(username.value.trim())){error.value='用户名需为 3–24 位字母、数字或下划线';return}
  if(isRegister.value && password.value.length<8){error.value='密码至少需要 8 位';return}
  if(isRegister.value && password.value!==confirmation.value){error.value='两次输入的密码不一致';return}
  busy.value=true
  try{await signIn(username.value.trim(),password.value,isRegister.value);emit('success')}
  catch(reason){error.value=reason instanceof Error?reason.message:'操作失败，请稍后重试'}finally{busy.value=false}
}
</script>
<template>
  <div class="account-backdrop" @click.self="close"><section class="account-dialog" role="dialog" aria-modal="true" :aria-label="isRegister?'注册账号':'账号登录'">
    <button class="dialog-close" aria-label="关闭账号窗口" :disabled="busy" @click="close"><X :size="19" /></button>
    <div class="account-emblem"><UserPlus v-if="isRegister" :size="27"/><LogIn v-else :size="27"/></div>
    <h2>{{ isRegister?'创建平台账号':'欢迎登录' }}</h2><p class="account-subtitle">低空智能巡检与决策平台</p>
    <div class="account-tabs"><button :aria-pressed="!isRegister" :disabled="busy" @click="emit('mode','login')">账号登录</button><button :aria-pressed="isRegister" :disabled="busy" @click="emit('mode','register')">注册账号</button></div>
    <form @submit.prevent="submit">
      <label class="account-label" for="account-username">用户名</label><div class="account-field"><UserRound :size="17"/><input id="account-username" ref="input" v-model="username" name="username" autocomplete="username" placeholder="请输入用户名" required maxlength="24" :disabled="busy" /></div>
      <label class="account-label" for="account-password">密码</label><div class="account-field"><LockKeyhole :size="17"/><input id="account-password" v-model="password" name="password" :type="showPassword?'text':'password'" :autocomplete="isRegister?'new-password':'current-password'" :placeholder="isRegister?'设置密码，至少 8 位':'请输入密码'" required maxlength="128" :disabled="busy" /><button type="button" :aria-label="showPassword?'隐藏密码':'显示密码'" @click="showPassword=!showPassword"><EyeOff v-if="showPassword" :size="17"/><Eye v-else :size="17"/></button></div>
      <template v-if="isRegister"><label class="account-label" for="account-confirmation">确认密码</label><div class="account-field"><LockKeyhole :size="17"/><input id="account-confirmation" v-model="confirmation" name="confirmation" :type="showPassword?'text':'password'" autocomplete="new-password" placeholder="请再次输入密码" required maxlength="128" :disabled="busy"/></div><p class="registration-hint">用户名支持 3–24 位字母、数字和下划线，注册成功后自动登录。</p></template>
      <p v-if="error" class="account-error" role="alert">{{ error }}</p><button class="account-submit" :disabled="busy">{{ busy?'正在处理…':isRegister?'注册并登录':'登录平台' }}<ArrowRight v-if="!busy" :size="17"/></button>
    </form>
    <div class="default-account"><span>默认演示账号</span><p>账号 <strong>admin</strong><i></i>密码 <strong>admin123</strong></p></div>
  </section></div>
</template>
<style scoped>
.account-backdrop{position:fixed;z-index:200;inset:0;background:#0f172a60;backdrop-filter:blur(5px);display:flex;align-items:center;justify-content:center;padding:20px}.account-dialog{position:relative;background:white;width:420px;max-width:100%;max-height:94vh;overflow:auto;border:1px solid #e2e8f0;border-radius:18px;padding:30px;box-shadow:0 24px 100px #0f172a40;color:#334155}.dialog-close{position:absolute;right:17px;top:17px;padding:5px;color:#94a3b8;border-radius:5px}.account-emblem{height:54px;width:54px;background:linear-gradient(140deg,#eff6ff,#e0e7ff);color:#3b5ddb;border:1px solid #dbeafe;border-radius:15px;display:grid;place-items:center;margin:0 auto 16px}.account-dialog h2{text-align:center;font-size:23px;font-weight:700;color:#1e293b}.account-subtitle{font-size:12px;color:#94a3b8;text-align:center;margin-top:7px}.account-tabs{display:flex;background:#f1f5f9;border-radius:8px;padding:4px;margin:25px 0 20px}.account-tabs button{flex:1;padding:8px;border-radius:5px;color:#94a3b8;font-size:12px}.account-tabs button[aria-pressed=true]{background:white;color:#3157c8;font-weight:600;box-shadow:0 2px 6px #0f172a0c}.account-label{display:block;font-size:12px;font-weight:600;color:#64748b;margin:16px 0 8px}.account-field{display:flex;align-items:center;gap:10px;padding:0 12px;border:1px solid #dbe3ed;border-radius:7px;color:#94a3b8;background:white}.account-field:focus-within{border-color:#7c9eea;box-shadow:0 0 0 3px #eff6ff}.account-field input{flex:1;min-width:0;outline:none;background:transparent;padding:12px 0;font-size:13px;color:#334155}.account-field input::placeholder{color:#a6b1c1}.account-field button{display:flex;padding:4px}.account-submit{width:100%;display:flex;align-items:center;justify-content:center;gap:10px;background:#3159d2;border-radius:7px;color:white;font-size:13px;font-weight:600;padding:12px;margin-top:24px}.account-submit:hover{background:#2649b4}button:disabled{opacity:.5;cursor:not-allowed}.account-error{font-size:11px;color:#be123c;background:#fff1f2;border:1px solid #ffe4e6;border-radius:6px;padding:10px;margin-top:15px;line-height:1.7}.registration-hint{font-size:10px;color:#94a3b8;line-height:1.8;margin-top:12px}.default-account{margin-top:23px;padding-top:18px;border-top:1px solid #edf2f7;font-size:11px;text-align:center;color:#94a3b8}.default-account>span{display:block;margin-bottom:9px}.default-account p{display:flex;align-items:center;justify-content:center;gap:6px}.default-account strong{font-family:ui-monospace,monospace;color:#64748b;font-size:12px;font-weight:500}.default-account i{height:10px;width:1px;background:#e2e8f0;margin:0 5px}@media(max-width:500px){.account-dialog{padding:26px 22px;border-radius:14px}.account-backdrop{padding:14px}}
</style>
