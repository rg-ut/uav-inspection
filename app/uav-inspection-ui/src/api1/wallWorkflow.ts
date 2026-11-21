import { sha256 } from '@noble/hashes/sha2.js'
import { request } from '../utils/request'

const root = '/wall/workflow'

export type FindingState = 'pending' | 'confirmed' | 'dismissed' | 'merged'
export type OrderState = 'draft' | 'assigned' | 'in_progress' | 'review' | 'completed' | 'cancelled'
export type OrderAction = 'save' | 'dispatch' | 'accept' | 'submit' | 'complete' | 'return' | 'cancel'
export type CountKey = 'pending' | 'confirmed' | 'assigned' | 'completed'

export interface Task {
  id: string
  name: string
  building: string
  facade: string
  created_at: string
  duration: number | null
  offset: number
  video_url: string
  subtitle_url: string | null
}

export interface Finding {
  id: string
  state: FindingState
  run_id: string | null
  kind: string
  zone: string
  bbox: number[]
  floor: string
  note: string
  evidence_time: number
  first_time: number
  last_time: number
  image_url: string | null
  score: number
}

export interface Run {
  id: string
  state: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'
  model: string
  position: number
  coverage: number
  frame_count: number
  error: string | null
}

export interface Order {
  id: string
  state: OrderState
  assignee: string
  priority: 'normal' | 'urgent'
  due_on: string | null
  instructions: string
  completion: string
  finding_ids: string[]
  photos: { kind: 'before' | 'after'; url: string }[]
}

export interface TaskDetail {
  task: Task
  findings: Finding[]
  orders: Order[]
  runs: Run[]
  counts: Record<CountKey, number> | null
  can_manage: boolean
}

export interface ModelOption { id: string; name: string; ready?: boolean; reason?: string }

async function api<T>(path: string, method = 'GET', data?: unknown, signal?: AbortSignal) {
  const config = {
    url: root + path, method, data, signal,
    ...(data instanceof FormData ? { headers: { 'Content-Type': 'multipart/form-data' }, timeout: 180000 } : {}),
  }
  return (await request<T>(config)).data
}

function fileForm(file: File) {
  const form = new FormData()
  form.append('file', file)
  return form
}

export const wallWorkflow = {
  detectFrame: (task: string, model: string, image: string, sensitivity: number, signal?: AbortSignal) =>
    api<{ boxes: number[][]; width: number; height: number; inference_ms: number }>(
      `/tasks/${task}/detect`, 'POST', { model, image, sensitivity }, signal),
  interpret: (question: string, task_id: string | null) =>
    api<{ action: 'subtitle' | 'pending' | 'seek' | 'draft'; seconds?: number } | null>(
      '/assistant', 'POST', { question, task_id }),
  tasks: () => api<Task[]>('/tasks'),
  detail: (id: string) => api<TaskDetail>('/tasks/' + id),
  models: () => api<{ models: ModelOption[]; can_upload: boolean; upload_hint?: string }>('/models'),
  uploadModel: (file: File) => api<ModelOption>('/models', 'POST', fileForm(file)),
  subtitle: (id: string, file: File) => api('/tasks/' + id + '/subtitle', 'PUT', fileForm(file)),
  offset: (id: string, seconds: number) => api('/tasks/' + id + '/subtitle-offset', 'PUT', { seconds }),
  run: (id: string, model: string, sensitivity: number, mode: 'sample' | 'all', iou: number) =>
    api('/tasks/' + id + '/runs', 'POST', { model, sensitivity, mode, iou }),
  cancel: (id: string, run: string) => api('/tasks/' + id + '/runs/' + run + '/cancel', 'POST'),
  review: (id: string, finding: Finding, state: FindingState, floor: string, note: string, kind?: string) =>
    api('/tasks/' + id + '/findings/' + finding.id, 'PATCH', { state, floor, note, kind }),
  merge: (id: string, into: string, ids: string[]) =>
    api('/tasks/' + id + '/findings/merge', 'POST', { into, ids }),
  clearFindings: (id: string) => api<{ deleted: number }>('/tasks/' + id + '/findings', 'DELETE'),
  draft: (id: string, finding_ids: string[]) => api<Order>('/tasks/' + id + '/orders', 'POST', { finding_ids }),
  order: (id: string, order: string, action: OrderAction, data: Record<string, unknown>) =>
    api<Order>('/tasks/' + id + '/orders/' + order, 'PATCH', { action, ...data }),
  photo: (id: string, order: string, kind: string, file: File) =>
    api('/tasks/' + id + '/orders/' + order + '/photos/' + kind, 'POST', fileForm(file)),
}

export async function uploadInspection(
  file: File, task_name: string, building: string, facade: string,
  progress: (percent: number) => void, signal: AbortSignal,
) {
  const hash = sha256.create()
  const reader = file.stream().getReader()
  try {
    while (true) {
      signal.throwIfAborted()
      const { value, done } = await reader.read()
      if (done) break
      hash.update(value)
    }
  } finally {
    await reader.cancel()
    reader.releaseLock()
  }
  const fingerprint = Array.from(hash.digest(),
    byte => byte.toString(16).padStart(2, '0')).join('')
  const session = await api<{ id: string; chunk_size: number; received: number[]; parts: number }>('/uploads', 'POST',
    { name: file.name, size: file.size, fingerprint, task_name, building, facade }, signal)
  const received = new Set(session.received)
  const path = '/uploads/' + session.id
  for (let index = 0; index < session.parts; index++) {
    if (!received.has(index)) {
      const start = index * session.chunk_size
      const part = new File([file.slice(start, start + session.chunk_size)], 'video.part')
      await api(path + '/parts/' + index, 'PUT', fileForm(part), signal)
    }
    progress(Math.round((index + 1) / session.parts * 100))
  }
  return (await request<{ id: string }>({ url: root + path + '/complete', method: 'POST', timeout: 600000, signal })).data.id
}

export function wallError(error: unknown) {
  const detail = (error as { response?: { data?: { detail?: unknown } } }).response?.data?.detail
  return typeof detail === 'string' ? detail : '操作未完成，请检查连接后重试。'
}
