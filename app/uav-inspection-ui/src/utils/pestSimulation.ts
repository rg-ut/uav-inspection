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
export const surveyPoints: Position3[] = [home, [0, 14, 33], [-24, 14, 20], [-24, 14, -22], [0, 14, -22], [0, 14, 20], [24, 14, 20], [24, 14, -22]]
// Route previews show the survey/takeoff portion; review legs are planned only at execution time.
export const simulationRoutes: SimulationRoute[] = [
  { id: 'maize-adaptive', name: '一号试验田 · 全域巡检', description: '区域巡查后自动规划复核视点，逐株完成观察并返航。', points: [...surveyPoints, [0, 14, 33], home] },
  { id: 'maize-orbit', name: '东区 M01 · 重点巡检', description: '前往东区样株，由视点优化逐次规划观察航点。', points: [home, [0, 10, 33], [-16, 10, 0], [0, 10, 33], home] },
  { id: 'maize-boundary', name: '一号试验田 · 快速巡查', description: '沿田块航线完成区域覆盖，也可手动加入优先复核。', points: [...surveyPoints, [0, 14, 33], home] },
]
export interface PlantObservation { target: number; sector: number; visible: number; source: 'flight' | 'viewpoint'; position: Position3; viewId?: string; capturedAt?: number }
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
  plans?: (ViewpointResult & { id: string; state: string })[]; plants?: string[]; outcome?: 'completed' | 'interrupted' | 'in-progress'; progress?: number
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
  return `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>玉米病害巡检报告</title><style>body{max-width:960px;margin:40px auto;padding:0 24px;font:14px system-ui;color:#334155;line-height:1.7}h1{color:#0f172a;border-bottom:3px solid #e11d48;padding-bottom:18px}h2{margin-top:36px;font-size:18px}small{color:#94a3b8}.metrics{display:flex;gap:16px;margin:24px 0}.metrics p{flex:1;background:#f8fafc;border:1px solid #e2e8f0;padding:20px;border-radius:8px}.metrics b{display:block;font-size:24px;color:#0f172a}table{border-collapse:collapse;width:100%;margin-top:20px}td,th{padding:10px;border-bottom:1px solid #e2e8f0;text-align:left}th{background:#f8fafc}footer{margin-top:40px;padding-top:20px;border-top:1px solid #e2e8f0;color:#94a3b8}</style><small>低空智能巡检与决策平台 / 巡检档案</small><h1>玉米病害巡检报告</h1><p>任务：${escape(report.routeName)}<br>编号：${escape(report.id)}<br>生成时间：${escape(report.createdAt)}<br>任务状态：${escape(report.outcome === 'interrupted' ? '中途返航' : stages[report.status] || report.status)}</p><div class="metrics"><p>任务时长<b>${report.elapsed.toFixed(1)} s</b></p><p>累计航程<b>${report.distance.toFixed(1)} m</b></p><p>观察机位<b>${report.viewpoints.length} 次</b></p></div><p>任务完成度：${Number(report.progress ?? (report.status === 'completed' ? 100 : 0)).toFixed(1)}%</p><h2>植株观察记录</h2><table><tr><th>样株编号</th><th>有效观察方向</th><th>复核状态</th><th>观察记录</th></tr>${observationTargets.map((target, i) => `<tr><td>${escape(target.name)}</td><td>${observationCoverage(report.observations || [], i)} 个方向</td><td>${escape(({ pending: '待巡查', surveyed: '已巡查', queued: '待复核', inspecting: '复核中', reviewed: '复核完成', 'needs-review': '待复核' } as Record<string, string>)[report.plants?.[i] || ''] || '—')}</td><td>${(report.observations || []).filter(item => item.target === i).length} 次</td></tr>`).join('')}</table><h2>视点调度</h2>${report.viewpoints.map((item, i) => `<p>机位 ${i + 1}：视野通畅率 ${Math.round((1 - item.after) * 100)}%，高度 ${Number(item.position?.[1] || 0).toFixed(1)} m。</p>`).join('') || '<p>本次按计划航线执行，未单独调度观察机位。</p>'}<h2>飞行轨迹</h2><table><tr><th>任务秒</th><th>X / m</th><th>Z / m</th><th>高度 / m</th><th>电量 / %</th></tr>${rows}</table><footer>任务类型：场景演示 · 本报告记录演示任务的航行与观察过程。</footer></html>`
}
