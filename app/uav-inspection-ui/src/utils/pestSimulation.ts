export type Position3 = [number, number, number]
export type MissionStage = 'idle' | 'running' | 'paused' | 'returning' | 'completed'
export type ViewStrategy = 'fixed' | 'coverage' | 'semantic'
export interface SimulationRoute { id: string; name: string; description: string; points: Position3[] }
const home: Position3 = [0, 0.7, 33]
// These are authored scenarios, not detections or model confidence values.
export const observationTargets = [
  { name: 'M01 · 东区样株', point: [-16, 2.9, 0] as Position3, cue: .48, symptom: '褐色椭圆斑（示意）' },
  { name: 'M02 · 中区样株', point: [8, 2.9, 8] as Position3, cue: .56, symptom: '叶片条斑（示意）' },
  { name: 'M03 · 北区样株', point: [24, 2.9, -16] as Position3, cue: .92, symptom: '明显病斑（示意）' },
  { name: 'M04 · 对照样株', point: [-8, 2.9, -16] as Position3, cue: .08, symptom: '健康对照（预设）' },
]
export function reviewTargets() { return observationTargets.map((target, index) => ({ ...target, index })).filter(target => target.cue >= .2 && target.cue <= .8) }
const coarse: Position3[] = [home, [0, 14, 33], [-24, 14, 20], [-24, 14, -22], [0, 14, -22], [0, 14, 20], [24, 14, 20], [24, 14, -22]]
export function makeAdaptivePlan() {
  const points: Position3[] = [...coarse]
  const coarseEnd = routeLength(points)
  const visits: { target: number; distance: number; position: Position3 }[] = []
  // Simplified nearest-neighbour ordering, then three low-altitude views per uncertain plant.
  const remaining = reviewTargets()
  while (remaining.length) {
    remaining.sort((a, b) => segmentLength(points.at(-1)!, a.point) - segmentLength(points.at(-1)!, b.point))
    const target = remaining.shift()!
    for (const angle of [0, Math.PI * 2 / 3, Math.PI * 4 / 3]) {
      const position: Position3 = [target.point[0] + Math.cos(angle) * 6, 5.5, target.point[2] + Math.sin(angle) * 6]
      points.push(position); visits.push({ target: target.index, distance: routeLength(points), position })
    }
  }
  const fineEnd = routeLength(points)
  points.push([0, 14, 33], home)
  return { points, coarseEnd, fineEnd, visits }
}
export const adaptivePlan = makeAdaptivePlan()
export const simulationRoutes: SimulationRoute[] = [
  { id: 'maize-adaptive', name: '一号试验田 · 全域巡检', description: '覆盖区域巡查，并对重点样株开展低空多角度复核。', points: adaptivePlan.points },
  { id: 'maize-orbit', name: '东区 M01 · 重点巡检', description: '环绕东区样株采集不同方向的叶片观察记录。', points: [home, [0, 12, 33], ...Array.from({ length: 13 }, (_, i): Position3 => { const angle = i / 12 * Math.PI * 2; return [-16 + 8 * Math.cos(angle), 6, 8 * Math.sin(angle)] }), [0, 12, 33], home] },
  { id: 'maize-boundary', name: '一号试验田 · 快速巡查', description: '沿田块航线完成高空覆盖，适合快速查看区域概况。', points: [...coarse, [0, 14, 33], home] },
]
export function missionPhase(routeId: string, distance: number) {
  if (routeId !== 'maize-adaptive') return routeId === 'maize-orbit' ? '单株环绕观察' : '仅粗巡 · 未精查'
  return distance < adaptivePlan.coarseEnd ? '① 高空粗巡' : distance < adaptivePlan.fineEnd ? '② 低空精查' : '③ 返航归档'
}
export function routeObservations(routeId: string) {
  if (routeId === 'maize-adaptive') return adaptivePlan.visits
  const route = simulationRoutes.find(item => item.id === routeId)
  if (routeId !== 'maize-orbit' || !route) return []
  return route.points.slice(2, -2).map((position, index) => ({ target: 0, position, distance: routeLength(route.points.slice(0, index + 3)) }))
}
export interface PlantObservation { target: number; sector: number; visible: number; source: 'flight' | 'viewpoint'; position: Position3 }
export function viewSector(position: Position3, target: Position3) { return Math.floor(((Math.atan2(position[2] - target[2], position[0] - target[0]) + Math.PI * 2) % (Math.PI * 2)) / (Math.PI / 4)) }
export function observationCoverage(observations: PlantObservation[], target: number) { return new Set(observations.filter(item => item.target === target && item.visible >= .5).map(item => item.sector)).size }
export function candidateScore(strategy: ViewStrategy, visible: number, novel: boolean, cue: number) {
  if (strategy === 'fixed') return 0
  if (strategy === 'coverage') return visible * .55 + Number(novel) * .45
  return visible * .65 + Number(novel) * .25 + (1 - Math.abs(cue - .5) * 2) * visible * .1
}
export function segmentLength(a: Position3, b: Position3) { return Math.hypot(b[0] - a[0], b[1] - a[1], b[2] - a[2]) }
export function routeLength(points: Position3[]) { return points.slice(1).reduce((total, point, i) => total + segmentLength(points[i]!, point), 0) }
export function sampleRoute(points: Position3[], distance: number) {
  if (points.length < 2) return { position: points[0] ?? [0, 0, 0] as Position3, heading: 0, waypoint: 0 }
  let remaining = Math.max(0, distance)
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1]!, b = points[i]!, length = segmentLength(a, b)
    if (remaining <= length || i === points.length - 1) {
      const t = length && remaining < length - 1e-8 ? Math.min(1, remaining / length) : 1
      return { position: t === 1 ? [...b] as Position3 : a.map((value, axis) => value + (b[axis]! - value) * t) as Position3, heading: Math.atan2(b[0] - a[0], b[2] - a[2]) * 180 / Math.PI, waypoint: t === 1 ? i : i - 1 }
    }
    remaining -= length
  }
  return { position: points.at(-1)!, heading: 0, waypoint: points.length - 1 }
}
export function returnRoute(position: Position3): Position3[] {
  const height = Math.max(12, position[1])
  return [[...position], [position[0], height, position[2]], [home[0], height, home[2]], [...home]]
}
export function simulationBattery(elapsedSeconds: number) { return Math.max(0, 100 - elapsedSeconds / 18) }
export function simulationCommand(text: string): 'start' | 'pause' | 'resume' | 'return' | 'optimize' | 'status' | 'help' {
  if (/暂停|停止/.test(text)) return 'pause'
  if (/继续|恢复/.test(text)) return 'resume'
  if (/返航|回家/.test(text)) return 'return'
  if (/视点|优化|遮挡/.test(text)) return 'optimize'
  if (/开始|起飞|巡航/.test(text)) return 'start'
  if (/状态|进度|电量/.test(text)) return 'status'
  return 'help'
}
export interface SimulationSample { second: number; x: number; altitude: number; z: number; battery: number; heading: number }
export interface ViewpointResult { before: number; after: number; candidates: number; position: Position3; strategy?: ViewStrategy; target?: number; sector?: number; yaw?: number; pitch?: number }
export interface SimulationReport {
  id: string; createdAt: string; routeName: string; status: MissionStage; elapsed: number; distance: number
  samples: SimulationSample[]; viewpoints: ViewpointResult[]; observations?: PlantObservation[]
}
export function readReports(raw: string | null): SimulationReport[] {
  try {
    const values: unknown = JSON.parse(raw || '[]')
    if (!Array.isArray(values)) return []
    return values.filter((value): value is SimulationReport => Boolean(value && typeof value.id === 'string' && typeof value.routeName === 'string' && typeof value.createdAt === 'string' && Number.isFinite(value.elapsed) && Number.isFinite(value.distance) && Array.isArray(value.samples) && Array.isArray(value.viewpoints))).slice(0, 5)
  } catch { return [] }
}
export function simulationReportHtml(report: SimulationReport) {
  const escape = (value: unknown) => String(value).replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]!)
  const stages: Record<MissionStage, string> = { idle: '待命', running: '巡检中', paused: '已暂停', returning: '返航中', completed: '已结束' }
  const rows = report.samples.slice(0, 600).map(row => `<tr><td>${escape(row.second)}</td><td>${Number(row.x).toFixed(1)}</td><td>${Number(row.z).toFixed(1)}</td><td>${Number(row.altitude).toFixed(1)}</td><td>${Number(row.battery).toFixed(1)}</td></tr>`).join('')
  return `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>玉米病害巡检报告</title><style>body{max-width:960px;margin:40px auto;padding:0 24px;font:14px system-ui;color:#334155;line-height:1.7}h1{color:#0f172a;border-bottom:3px solid #e11d48;padding-bottom:18px}h2{margin-top:36px;font-size:18px}small{color:#94a3b8}.metrics{display:flex;gap:16px;margin:24px 0}.metrics p{flex:1;background:#f8fafc;border:1px solid #e2e8f0;padding:20px;border-radius:8px}.metrics b{display:block;font-size:24px;color:#0f172a}table{border-collapse:collapse;width:100%;margin-top:20px}td,th{padding:10px;border-bottom:1px solid #e2e8f0;text-align:left}th{background:#f8fafc}footer{margin-top:40px;padding-top:20px;border-top:1px solid #e2e8f0;color:#94a3b8}</style><small>低空智能巡检与决策平台 / 巡检档案</small><h1>玉米病害巡检报告</h1><p>任务：${escape(report.routeName)}<br>编号：${escape(report.id)}<br>生成时间：${escape(report.createdAt)}<br>任务状态：${escape(stages[report.status] || report.status)}</p><div class="metrics"><p>任务时长<b>${report.elapsed.toFixed(1)} s</b></p><p>累计航程<b>${report.distance.toFixed(1)} m</b></p><p>观察机位<b>${report.viewpoints.length} 次</b></p></div><h2>植株观察记录</h2><table><tr><th>样株编号</th><th>有效观察方向</th><th>观察记录</th></tr>${observationTargets.map((target, i) => `<tr><td>${escape(target.name)}</td><td>${observationCoverage(report.observations || [], i)} / 8</td><td>${(report.observations || []).filter(item => item.target === i).length} 次</td></tr>`).join('')}</table><h2>视点调度</h2>${report.viewpoints.map((item, i) => `<p>机位 ${i + 1}：视野通畅率 ${Math.round((1 - item.after) * 100)}%，高度 ${Number(item.position?.[1] || 0).toFixed(1)} m。</p>`).join('') || '<p>本次按计划航线执行，未单独调度观察机位。</p>'}<h2>飞行轨迹</h2><table><tr><th>任务秒</th><th>X / m</th><th>Z / m</th><th>高度 / m</th><th>电量 / %</th></tr>${rows}</table><footer>任务类型：场景演示 · 本报告记录演示任务的航行与观察过程。</footer></html>`
}
