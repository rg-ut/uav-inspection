import { request } from '../utils/request'

export interface AcceptanceVehicle {
  car: string
  real_car: string
  plate: string
  /** spawn 真值表里的车牌，与 plate 逐字比对后才算「认定」 */
  plate_truth?: string
  match?: boolean
  /** 真值表里的停放形态：normal / encroach / angled */
  state?: string
  confidence: number
  distance_m: number
  leg: string
  note: string
  card: string | null
  card_url: string | null
}

export interface AcceptanceViolation {
  car: string
  real_car: string
  slot: string
  lateral_m: number
  longitudinal_m: number
  margin_m: number
  shape_error_m: number
  verdict: string
  truth: string
}

export interface AcceptanceData {
  available: boolean
  batch: {
    start: string
    end: string
    fsm: string
    exit_code: number
    batch_dir: string
    mission_id?: string
    wall_clock_s?: number | null
  }
  summary: Array<{ label: string; value: string; note: string }>
  vehicles: AcceptanceVehicle[]
  violations: AcceptanceViolation[]
  /** 违停余量来自哪一轮复算（本轮只做检测/识别/覆盖时不重算几何） */
  geometry?: { batch: string; start: string; note: string }
  reruns?: Array<{ 启动: string; 墙钟_s: number; 建图: number }>
  retry: { 轮次: number; 触发: string; 补拍: string; 结果: string }
}

export interface ReplayPhase {
  key: string
  label: string
  kind: string
  t0: number
  t1: number
  seconds: number
  detail: string
  cover: string
}

export interface ReplayEvent {
  t: number
  kind: string
  text: string
}

/** 逐腿路径与视点：由任务日志的 GOTO 序列重建，供四宫格第四格画俯视路径图 */
export interface PlatePathStep {
  i: number
  t: number
  car: string
  leg: string
  d: number
  x: number
  y: number
  yaw: number
  kind: 'overview' | 'capture' | 'transit'
  /** 仅 front/retry 腿有：VP 决定补拍时打的原因（如「1 条腿全部格式不合法」） */
  why?: string
}

export interface PlatePathCar {
  name: string
  x: number
  y: number
  yaw: number
  plate: string
}

export interface PlatePathData {
  source: string
  note: string
  area: { x0: number; x1: number; y0: number; y1: number }
  cars: PlatePathCar[]
  steps: PlatePathStep[]
}

export interface ReplayData {
  available?: boolean
  mission: {
    run: string
    id: string
    started_at: string
    scenario: string
    platform: string
    barrier: string
  }
  kpi: {
    final: string
    note: string
    c_cap: number
    n_cars: number
    n_captured: number
    missing: string[]
    inspect_round: number
    rounds: Array<{
      ok: boolean
      rc: number
      run_dir: string
      cars: number
      captured: number
      new: number
      reason: string
    }>
  }
  budget: { wall_clock_s: number; elapsed_s: number; budget_s: number }
  video: {
    src: string
    poster: string
    seconds: number
    frames: number
    fps: number
    size: string
    offset_s: number
    note: string
  }
  phases: ReplayPhase[]
  events: ReplayEvent[]
}

export interface CoverageData {
  available?: boolean
  source: {
    map_dir: string
    frames_used: number | null
    cell_m: number
    grid: number[]
    rect: number[]
    note: string
  }
  area: { site_cells: number; car_cells: number; observable_cells: number }
  coverage: Array<{ min_frames: number; cells: number; ratio: number }>
  depth: {
    frames_p50: number
    frames_p95: number
    dist_p50: number
    dist_p95: number
    pts_p50: number
  }
  rows: Array<{
    label: string
    x: number
    cells: number
    ge1: number
    ge3: number
    median_frames: number
  }>
  slots: Array<{
    name: string
    x: number
    y: number
    frames: number
    points: number
    min_dist: number
  }>
  image: string
  /** RViz 全流程录屏：点云 + 占据栅格的深度融合过程（2026-11-16 加） */
  rviz?: {
    src: string
    poster?: string
    seconds: number
    width: number
    height: number
    fps: number
    run: string
    note: string
    /** 录屏起点相对任务起跑的秒数；>0 表示前段（通常是 SURVEY）不在画面内 */
    offset_s?: number
    /** 与覆盖质量同批次时为 true；不同批次前端要显式提示 */
    same_batch: boolean
  }
}

export const plateApi = {
  getHealth() {
    return request<{
      status: string
      service_status: string
      detector_loaded: boolean
      recognizer_available: boolean
      acceptance_available: boolean
    }>({ url: '/plate/health', method: 'GET' })
  },
  getAcceptance() {
    return request<AcceptanceData>({ url: '/plate/acceptance', method: 'GET' })
  },
  getReplay() {
    return request<ReplayData>({ url: '/plate/replay', method: 'GET' })
  },
  getCoverage() {
    return request<CoverageData>({ url: '/plate/coverage', method: 'GET' })
  },
  /**
   * 俯视路径图数据。
   *
   * 先走 /plate/path；拿不到就回退 /plate/media/path.json（同一份文件，走静态挂载）。
   * 为什么要回退：后端 uvicorn 没开 --reload，用户双击「打开车牌巡检.bat」常驻的那个
   * 8002 进程还是旧代码，没有这条路由，直接 404 会让第四格变成「没有路径数据」。
   * 数据文件本身就挂在 media 下，回退这条路在新旧两代后端都通。
   */
  async getPath() {
    try {
      return await request<PlatePathData>({ url: '/plate/path', method: 'GET' })
    } catch {
      // 静态文件是裸 JSON（顶层就是 source/note/area/cars/steps，没有 {code,message,data}
      // 外壳），这里就地补一个外壳，调用方仍按 result.data 取，不必为兜底单开分支。
      const raw = await request<PlatePathData>({ url: '/plate/media/path.json', method: 'GET' })
      return { code: 200, message: 'ok', data: raw as unknown as PlatePathData }
    }
  },
}
