import { reactive } from 'vue'
import { webPath } from '../utils/webroot'

export interface AccountUser { id: number; username: string }
export const account = reactive<{user: AccountUser | null; ready: boolean}>({user:null,ready:false})
let pendingSession: Promise<void> | null = null
let revision = 0

async function authRequest(path: string, data?: {username:string;password:string}) {
  let response: Response
  try { response=await fetch(webPath(`/api/v1/auth/${path}`),{method:path==='me'?'GET':'POST',credentials:'same-origin',cache:'no-store',headers:data?{'Content-Type':'application/json'}:undefined,body:data?JSON.stringify(data):undefined}) }
  catch { throw new Error('暂时无法连接账号服务，请稍后重试。') }
  let payload
  try { payload=await response.json() } catch { throw new Error('账号服务暂不可用，请稍后重试。') }
  if(!response.ok){const detail=typeof payload.detail==='string'?payload.detail:'请检查用户名和密码格式';const error=new Error(detail);Object.assign(error,{status:response.status});throw error}
  return payload
}
export function loadAccount() {
  if(pendingSession)return pendingSession
  const requestRevision=revision
  pendingSession=(async()=>{try{const result=await authRequest('me');if(requestRevision===revision)account.user=result.data}catch(error){if(requestRevision!==revision)return;if((error as {status?:number}).status!==401)throw error;account.user=null}finally{account.ready=true}})().finally(()=>{pendingSession=null})
  return pendingSession
}
export async function signIn(username:string,password:string,register=false){const requestRevision=++revision;const result=await authRequest(register?'register':'login',{username,password});if(requestRevision===revision){account.user=result.data;account.ready=true}}
export async function signOut(){const requestRevision=++revision;await authRequest('logout');if(requestRevision===revision){account.user=null;account.ready=true}}
