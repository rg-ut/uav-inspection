import { observationTargets, surveyPoints, routeLength, returnRoute, sampleRoute, segmentLength, viewSector, observationCoverage, simulationBattery, type MissionStage, type PlantObservation, type Position3, type SimulationSample, type ViewpointResult, type ViewStrategy } from './pestSimulation.ts'

export const CAPTURE_SECONDS = 2
export const REQUIRED_VIEWS = 3
export const MAX_ATTEMPTS = 6
export const HOME: Position3 = [0, .7, 33]
export const SURVEY_POINTS = surveyPoints
export type PlantState = 'pending' | 'surveyed' | 'queued' | 'inspecting' | 'reviewed' | 'needs-review'
export interface ViewPlan extends ViewpointResult { id: string; target: number; sector: number; state: 'planned' | 'arrived' | 'captured' | 'cancelled'; trigger: 'auto' | 'manual'; plannedAt: number; arrivedAt?: number; capturedAt?: number }
export interface MissionState {
  version: 3; id: string; routeId: string; status: MissionStage; phase: 'survey' | 'transit' | 'observe' | 'return'
  leg: Position3[]; legDistance: number; distance: number; elapsed: number; surveyed: boolean; queue: number[]; reviewTargets: number[]
  activeTarget: number | null; pending: ViewPlan | null; plans: ViewPlan[]; observations: PlantObservation[]; samples: SimulationSample[]
  plants: PlantState[]; captureRemaining: number; aborted: boolean; progressAtAbort: number; sequence: number
  events: { second: number; message: string }[]
}
export interface MissionServices {
  plan(target: number, origin: Position3, strategy: ViewStrategy, observations: PlantObservation[], avoidedSectors: number[]): ViewpointResult | null
  observe(target: number, position: Position3): number
}
export function createMission(routeId = 'maize-adaptive'): MissionState {
  return { version: 3, id: `SIM-${Date.now()}`, routeId, status: 'idle', phase: 'survey', leg: routeId === 'maize-orbit' ? [HOME, [0, 10, 33]] : SURVEY_POINTS.map(p => [...p]), legDistance: 0, distance: 0, elapsed: 0, surveyed: false, queue: [], reviewTargets: [], activeTarget: null, pending: null, plans: [], observations: [], samples: [], plants: observationTargets.map(() => 'pending'), captureRemaining: 0, aborted: false, progressAtAbort: 0, sequence: 0, events: [] }
}
export function missionPose(m: MissionState) {
  const pose = sampleRoute(m.leg, m.legDistance)
  if (m.phase === 'observe' && m.pending?.yaw !== undefined) pose.heading = m.pending.yaw
  return pose
}
export function isMissionRunning(m: MissionState) { return m.status === 'running' || m.status === 'returning' }
function event(m: MissionState, message: string) { m.events.unshift({ second: m.elapsed, message }); m.events = m.events.slice(0, 60) }
function setLeg(m: MissionState, points: Position3[]) { m.leg = points.filter((point, i) => i === 0 || segmentLength(points[i - 1]!, point) > 1e-7).map(p => [...p]); if (m.leg.length === 1) m.leg.push([...m.leg[0]!]); m.legDistance = 0 }
export function safeViewLeg(origin: Position3, destination: Position3): Position3[] {
  const height = Math.max(10, origin[1], destination[1])
  return [[...origin], [origin[0], height, origin[2]], [destination[0], height, destination[2]], [...destination]]
}
export function missionProgress(m: MissionState) {
  if (m.aborted) return m.progressAtAbort
  if (m.status === 'completed') return 100
  const leg = Math.min(1, m.legDistance / Math.max(1e-7, routeLength(m.leg)))
  const surveyWeight = m.routeId === 'maize-orbit' ? 10 : m.routeId === 'maize-boundary' && !m.reviewTargets.length ? 85 : 40
  if (m.phase === 'survey') return surveyWeight * leg
  if (m.phase === 'return') return 90 + 10 * leg
  const total = Math.max(1, m.reviewTargets.length)
  const done = m.reviewTargets.filter(i => ['reviewed', 'needs-review'].includes(m.plants[i]!)).length
  const views = m.activeTarget === null ? 0 : Math.min(REQUIRED_VIEWS - .01, observationCoverage(m.observations, m.activeTarget))
  const current = m.phase === 'observe' ? .8 + .2 * (1 - m.captureRemaining / CAPTURE_SECONDS) : .8 * leg
  return Math.min(89.9, surveyWeight + (90 - surveyWeight) * (done + (views + current) / REQUIRED_VIEWS) / total)
}
export function startMission(m: MissionState) {
  if (m.status === 'completed' || isMissionRunning(m)) return
  m.status = m.phase === 'return' ? 'returning' : 'running'
  event(m, m.elapsed ? '巡检已继续。' : m.routeId === 'maize-orbit' ? '重点巡检开始，起飞后前往目标植株。' : '区域巡查开始，完成覆盖后安排复核。')
}
export function pauseMission(m: MissionState) { if (isMissionRunning(m)) { m.status = 'paused'; event(m, '任务已暂停，继续后从当前位置执行。') } }
function cancelPending(m: MissionState) {
  if (m.pending && m.pending.state !== 'captured') m.pending.state = 'cancelled'
  m.pending = null; m.captureRemaining = 0
}
export function returnMission(m: MissionState, automatic = false) {
  if (m.status === 'idle' || m.status === 'completed' || m.phase === 'return') return
  if (!automatic) { m.progressAtAbort = missionProgress(m); m.aborted = true }
  const origin = missionPose(m).position
  cancelPending(m)
  for (const i of [m.activeTarget, ...m.queue]) if (i !== null && !['reviewed', 'needs-review'].includes(m.plants[i]!)) m.plants[i] = 'needs-review'
  m.queue = []; m.activeTarget = null; m.phase = 'return'; m.status = 'returning'; setLeg(m, returnRoute(origin))
  event(m, automatic ? '复核任务已处理，正在返回起降点。' : '已结束后续巡检，沿安全航段返航。未完成项目将保留为待复核。')
}
function addTarget(m: MissionState, target: number, priority = false) {
  if (!m.reviewTargets.includes(target)) m.reviewTargets.push(target)
  if (m.activeTarget !== target) { m.queue = m.queue.filter(i => i !== target); priority ? m.queue.unshift(target) : m.queue.push(target); m.plants[target] = 'queued' }
}
function planNext(m: MissionState, services: MissionServices, strategy: ViewStrategy, trigger: 'auto' | 'manual' = 'auto', avoided: number[] = []) {
  while (true) {
    if (m.activeTarget === null) {
      const next = m.queue.shift()
      if (next === undefined) { returnMission(m, true); return }
      m.activeTarget = next; m.plants[next] = 'inspecting'
      event(m, `开始复核 ${observationTargets[next]!.name}，目标为 ${REQUIRED_VIEWS} 个有效视角。`)
    }
    const target = m.activeTarget, origin = missionPose(m).position
    const result = services.plan(target, origin, strategy, m.observations, avoided)
    if (!result || !result.position.every(Number.isFinite) || result.position[1] < 5.5) {
      m.plants[target] = 'needs-review'; event(m, `${observationTargets[target]!.name} 暂无可用观察航点，已标记为待复核。`); m.activeTarget = null; continue
    }
    const plan: ViewPlan = { ...result, target, sector: viewSector(result.position, observationTargets[target]!.point), id: `${m.id}-V${++m.sequence}`, state: 'planned', trigger, plannedAt: m.elapsed }
    m.pending = plan; m.plans.push(plan); m.phase = 'transit'; setLeg(m, safeViewLeg(origin, plan.position))
    event(m, `已规划 ${observationTargets[target]!.name} 的观察视点 V${m.sequence}，正在前往。`)
    return
  }
}
export function optimizeMission(m: MissionState, target: number, strategy: ViewStrategy, services: MissionServices) {
  if (!Number.isInteger(target) || !observationTargets[target]) return '请选择有效植株。'
  if (m.phase === 'return' || m.status === 'completed') return '当前任务已进入返航或结束，请在新任务中安排复核。'
  if (m.plants[target] === 'reviewed') return '该植株已完成本次复核；如需重新巡检，请开始新任务。'
  if (m.status === 'idle' || m.phase === 'survey' || m.activeTarget !== target) {
    addTarget(m, target, true)
    const message = m.status === 'idle' ? `${observationTargets[target]!.name} 已加入优先复核，开始巡检后执行。` : m.phase === 'survey' ? `${observationTargets[target]!.name} 已加入优先复核，将在区域巡查完成后执行。` : `${observationTargets[target]!.name} 已加入队列，当前植株复核完成后优先执行。`
    event(m, message); return message
  }
  const avoided = m.pending ? [m.pending.sector] : []
  cancelPending(m); planNext(m, services, strategy, 'manual', avoided)
  const message = m.status === 'paused' ? '观察航段已重新规划，继续巡检后飞往新视点。' : '观察航段已重新规划，无人机将飞往新视点后采样。'
  event(m, message); return message
}
function finishSurvey(m: MissionState, services: MissionServices, strategy: ViewStrategy) {
  m.surveyed = true
  if (m.routeId !== 'maize-orbit') m.plants = m.plants.map(value => value === 'pending' ? 'surveyed' : value)
  const requested = [...m.queue]
  const targets = m.routeId === 'maize-boundary' ? [] : m.routeId === 'maize-orbit' ? [0] : observationTargets.map((target, index) => ({ ...target, index })).filter(target => target.cue >= .2).map(target => target.index)
  const origin = missionPose(m).position
  targets.sort((a, b) => segmentLength(origin, observationTargets[a]!.point) - segmentLength(origin, observationTargets[b]!.point))
  for (const target of targets) if (!requested.includes(target)) addTarget(m, target)
  event(m, m.routeId === 'maize-orbit' ? '起飞完成，开始重点样株复核。' : m.queue.length ? `区域巡查完成，${m.queue.length} 株进入重点复核。` : '区域巡查完成，正在返航。')
  planNext(m, services, strategy)
}
function sample(m: MissionState) {
  const pose = missionPose(m)
  m.samples.push({ second: +m.elapsed.toFixed(2), x: +pose.position[0].toFixed(4), altitude: +pose.position[1].toFixed(4), z: +pose.position[2].toFixed(4), heading: +pose.heading.toFixed(2), battery: +simulationBattery(m.elapsed).toFixed(2) })
  m.samples = m.samples.slice(-1200)
}
function capture(m: MissionState, services: MissionServices, strategy: ViewStrategy) {
  const plan = m.pending, target = m.activeTarget
  if (!plan || target === null) return
  const position = missionPose(m).position
  const visible = Math.max(0, Math.min(1, services.observe(target, position)))
  m.observations.push({ target, position: [...position], sector: viewSector(position, observationTargets[target]!.point), visible, source: 'flight', viewId: plan.id, capturedAt: m.elapsed })
  plan.state = 'captured'; plan.capturedAt = m.elapsed; plan.after = 1 - visible
  sample(m)
  const coverage = observationCoverage(m.observations, target), attempts = m.observations.filter(item => item.target === target).length
  event(m, `${observationTargets[target]!.name} 完成悬停采样，视野通畅率 ${Math.round(visible * 100)}%，有效视角 ${Math.min(coverage, REQUIRED_VIEWS)}/${REQUIRED_VIEWS}。`)
  m.pending = null
  if (coverage >= REQUIRED_VIEWS || attempts >= MAX_ATTEMPTS) {
    m.plants[target] = coverage >= REQUIRED_VIEWS ? 'reviewed' : 'needs-review'
    event(m, `${observationTargets[target]!.name}${coverage >= REQUIRED_VIEWS ? ' 复核完成。' : ' 观察不足，已转为待复核。'}`); m.activeTarget = null
  }
  planNext(m, services, strategy)
}
export function advanceMission(m: MissionState, seconds: number, speed: number, strategy: ViewStrategy, services: MissionServices) {
  if (!isMissionRunning(m) || !Number.isFinite(seconds) || seconds <= 0 || !Number.isFinite(speed) || speed <= 0) return
  let remaining = Math.min(seconds, 1)
  const previousSecond = Math.floor(m.elapsed)
  for (let transitions = 0; remaining > 1e-8 && isMissionRunning(m) && transitions < 12; transitions++) {
    if (m.phase === 'observe') {
      const used = Math.min(remaining, m.captureRemaining)
      m.elapsed += used; m.captureRemaining -= used; remaining -= used
      if (m.captureRemaining <= 1e-8) capture(m, services, strategy)
    } else {
      const left = Math.max(0, routeLength(m.leg) - m.legDistance), used = Math.min(remaining, left / speed)
      m.legDistance = Math.min(routeLength(m.leg), m.legDistance + used * speed); m.distance += used * speed; m.elapsed += used; remaining -= used
      if (left - used * speed > 1e-7) break
      if (m.phase === 'survey') finishSurvey(m, services, strategy)
      else if (m.phase === 'transit' && m.pending) {
        m.phase = 'observe'; m.pending.state = 'arrived'; m.pending.arrivedAt = m.elapsed; m.captureRemaining = CAPTURE_SECONDS; sample(m)
        event(m, `已抵达视点 V${m.sequence}，悬停采样 ${CAPTURE_SECONDS} 秒。`)
      } else if (m.phase === 'return') { m.status = 'completed'; sample(m); event(m, m.aborted ? '无人机已降落，本次任务中途结束。' : '巡检完成，无人机已降落，任务记录可生成报告。') }
    }
  }
  if (Math.floor(m.elapsed) !== previousSecond && m.status !== 'completed') sample(m)
  if (simulationBattery(m.elapsed) <= 20 && m.phase !== 'return') returnMission(m)
}
export function restoreMission(raw: unknown): MissionState | null {
  if (!raw || typeof raw !== 'object') return null
  const m = raw as MissionState
  const point = (p: unknown) => Array.isArray(p) && p.length === 3 && p.every(v => typeof v === 'number' && Number.isFinite(v))
  if (m.version !== 3 || !['maize-adaptive', 'maize-orbit', 'maize-boundary'].includes(m.routeId) || !['idle', 'running', 'paused', 'returning', 'completed'].includes(m.status) || !['survey', 'transit', 'observe', 'return'].includes(m.phase)) return null
  if (!Array.isArray(m.leg) || m.leg.length < 2 || m.leg.length > 30 || !m.leg.every(point) || ![m.legDistance, m.distance, m.elapsed, m.captureRemaining, m.sequence, m.progressAtAbort].every(v => Number.isFinite(v) && v >= 0)) return null
  if (!Array.isArray(m.queue) || !Array.isArray(m.reviewTargets) || ![...m.queue, ...m.reviewTargets, ...(m.activeTarget === null ? [] : [m.activeTarget])].every(i => Number.isInteger(i) && i >= 0 && i < observationTargets.length)) return null
  if (!Array.isArray(m.plants) || m.plants.length !== observationTargets.length || !m.plants.every(p => ['pending', 'surveyed', 'queued', 'inspecting', 'reviewed', 'needs-review'].includes(p))) return null
  if (!Array.isArray(m.observations) || !m.observations.every(o => o && point(o.position) && Number.isInteger(o.target) && o.target >= 0 && o.target < observationTargets.length && Number.isInteger(o.sector) && o.sector >= 0 && o.sector < 8 && Number.isFinite(o.visible))) return null
  if (!Array.isArray(m.plans) || !m.plans.every(p => p && point(p.position) && Number.isFinite(p.after) && Number.isInteger(p.target))) return null
  if (!Array.isArray(m.samples) || !m.samples.every(s => s && [s.second, s.x, s.z, s.altitude, s.heading, s.battery].every(Number.isFinite)) || !Array.isArray(m.events) || !m.events.every(e => e && Number.isFinite(e.second) && typeof e.message === 'string')) return null
  if ((m.phase === 'transit' || m.phase === 'observe') && (!m.pending || !m.plans.some(p => p.id === m.pending!.id) || m.activeTarget !== m.pending.target)) return null
  const copy: MissionState = JSON.parse(JSON.stringify(m))
  // Re-link the pending plan so arrival/capture updates the report's plan record too.
  copy.pending = copy.pending ? copy.plans.find(p => p.id === copy.pending!.id) || null : null
  if (isMissionRunning(copy)) copy.status = 'paused'
  copy.legDistance = Math.min(copy.legDistance, routeLength(copy.leg))
  return copy
}
