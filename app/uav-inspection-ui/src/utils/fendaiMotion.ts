type Point = [number, number]

export interface PathDetail {
  sections: [x: number, z: number, height: number, innerWidth: number, outerWidth: number][]
  benches: [x: number, z: number, height: number, yaw: number][]
}

export const BENCH_DEPTH = 0.64
export const BENCH_WIDTH = 1.72
export const BENCH_HEIGHT = 0.90

export interface GardenProfile {
  origin: Point
  step: number
  heights: number[][]
  boundary: Point[]
  path: Point[]
  pathDetail?: PathDetail
  spawn: [number, number, number, number]
  flightLimit: number
  obstacles: { footprint: Point[]; base: number; top: number }[]
  modelPosition: [number, number, number]
  modelRotation: [number, number, number, number]
  modelScale: number
}

export type TravelMode = 'walk' | 'fly'

const EYE_HEIGHT = 1.8
const PLAYER_RADIUS = 0.32
const PLAYER_HEIGHT = 1.92
const GRAVITY = 13
const JUMP_SPEED = 4.8

function clamp(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, value))
}

export function pathSectionAt(detail: PathDetail, x: number, z: number) {
  let nearest: { index: number; t: number; lateral: number; distance: number } | undefined
  for (let i = 0; i < detail.sections.length - 1; i++) {
    const [ax, az] = detail.sections[i]!
    const [bx, bz] = detail.sections[i + 1]!
    const dx = bx - ax
    const dz = bz - az
    const length = Math.hypot(dx, dz)
    const t = clamp(((x - ax) * dx + (z - az) * dz) / (length * length), 0, 1)
    const distance = Math.hypot(x - ax - t * dx, z - az - t * dz)
    if (!nearest || distance < nearest.distance) {
      nearest = { index: i, t, lateral: ((x - ax) * dz - (z - az) * dx) / length, distance }
    }
  }
  if (!nearest) return undefined
  const { index, t, lateral, distance } = nearest
  const [,, a, inner, outer] = detail.sections[index]!
  const [,, b, nextInner, nextOuter] = detail.sections[index + 1]!
  const width = lateral < 0 ? inner + (nextInner - inner) * t : outer + (nextOuter - outer) * t
  const across = clamp(lateral, -3, 3)
  const fade = clamp((index + t) / 0.8, 0, 1)
    * clamp((detail.sections.length - 1 - index - t) / 0.8, 0, 1)
  return {
    width,
    distance,
    fade,
    height: a + (b - a) * t - across * 0.043,
  }
}

export function terrainHeight(profile: GardenProfile, x: number, z: number) {
  const { origin, step, heights } = profile
  const gx = clamp((x - origin[0]) / step, 0, heights[0]!.length - 1)
  const gz = clamp((z - origin[1]) / step, 0, heights.length - 1)
  const col = Math.min(Math.floor(gx), heights[0]!.length - 2)
  const row = Math.min(Math.floor(gz), heights.length - 2)
  const a = heights[row]![col]!
  const b = heights[row]![col + 1]!
  const c = heights[row + 1]![col]!
  const d = heights[row + 1]![col + 1]!
  const u = gx - col
  const v = gz - row
  return a * (1 - u) * (1 - v) + b * u * (1 - v) + c * (1 - u) * v + d * u * v
}

export function groundHeight(profile: GardenProfile, x: number, z: number) {
  const terrain = terrainHeight(profile, x, z)
  const road = profile.pathDetail && pathSectionAt(profile.pathDetail, x, z)
  if (!road) return terrain
  const amount = road.fade * clamp((road.width + 0.5 - road.distance) / 0.7, 0, 1)
  return terrain + (road.height - terrain) * amount
}

/** Minimum signed distance to the edges of a counterclockwise convex footprint. */
function boundaryDistance(polygon: Point[], x: number, z: number) {
  let distance = Infinity
  for (let i = 0; i < polygon.length; i++) {
    const [ax, az] = polygon[i]!
    const [bx, bz] = polygon[(i + 1) % polygon.length]!
    distance = Math.min(distance, ((bx - ax) * (z - az) - (bz - az) * (x - ax)) / Math.hypot(bx - ax, bz - az))
  }
  return distance
}

export class FendaiMotion {
  x = 0
  z = 0
  feet = 0
  grounded = true
  private verticalSpeed = 0

  private readonly profile: GardenProfile
  private readonly obstacles: GardenProfile['obstacles']

  constructor(profile: GardenProfile) {
    this.profile = profile
    this.obstacles = [...profile.obstacles]
    for (const [x, z, height, yaw] of profile.pathDetail?.benches ?? []) {
      const w = BENCH_WIDTH / 2
      const d = BENCH_DEPTH / 2
      const corners: Point[] = [[-w, -d], [w, -d], [w, d], [-w, d]]
      const sin = Math.sin(yaw)
      const cos = Math.cos(yaw)
      this.obstacles.push({
        footprint: corners.map(([dx, dz]) => [x + dx * cos + dz * sin, z - dx * sin + dz * cos]),
        base: height,
        top: height + BENCH_HEIGHT,
      })
    }
    this.reset()
  }

  get eye() {
    return this.feet + EYE_HEIGHT
  }

  get groundClearance() {
    return this.feet - groundHeight(this.profile, this.x, this.z)
  }

  reset() {
    const [x, z] = this.profile.spawn
    this.x = x
    this.z = z
    this.feet = groundHeight(this.profile, x, z)
    this.grounded = true
    this.verticalSpeed = 0
  }

  changeMode() {
    this.verticalSpeed = 0
  }

  jump() {
    if (this.grounded) {
      this.verticalSpeed = JUMP_SPEED
      this.grounded = false
    }
  }

  private canMove(x: number, z: number, feet: number) {
    return boundaryDistance(this.profile.boundary, x, z) >= PLAYER_RADIUS
      && !this.obstacles.some(({ footprint, base, top }) =>
        feet < top && feet + PLAYER_HEIGHT > base && boundaryDistance(footprint, x, z) > -PLAYER_RADIUS)

  }

  advance(delta: number, dx: number, dz: number, rise: number, mode: TravelMode, fast: boolean) {
    if (delta <= 0) return
    const substeps = Math.ceil(delta * 120)
    const dt = delta / substeps
    const speed = mode === 'fly' ? (fast ? 11 : 6) : (fast ? 5.8 : 2.75)
    const inputLength = Math.max(1, Math.hypot(dx, dz, mode === 'fly' ? rise : 0))
    const stepX = dx * speed * dt / inputLength
    const stepZ = dz * speed * dt / inputLength
    for (let i = 0; i < substeps; i++) {
      const move = (x: number, z: number) => {
        const ground = groundHeight(this.profile, x, z)
        if (!this.canMove(x, z, Math.max(this.feet, ground))) return
        this.x = x
        this.z = z
        if (this.grounded && mode === 'walk' && Math.abs(ground - this.feet) <= 0.16) this.feet = ground
      }
      // Resolve each axis independently so movement slides along the boundary or a structure.
      if (stepX) move(this.x + stepX, this.z)
      if (stepZ) move(this.x, this.z + stepZ)
      const ground = groundHeight(this.profile, this.x, this.z)
      this.verticalSpeed = mode === 'fly' ? rise * speed / inputLength : this.verticalSpeed - GRAVITY * dt
      const nextFeet = clamp(this.feet + this.verticalSpeed * dt, ground, ground + this.profile.flightLimit)
      if (this.canMove(this.x, this.z, nextFeet)) {
        this.feet = nextFeet
      } else if (this.verticalSpeed < 0) {
        // Descending onto a pavilion stops at the top of its collision volume.
        for (const { footprint, top } of this.obstacles) {
          if (this.feet >= top && nextFeet < top && boundaryDistance(footprint, this.x, this.z) > -PLAYER_RADIUS) {
            this.feet = top
          }
        }
      }
      this.grounded = this.feet <= ground + 0.001 || (this.verticalSpeed < 0 && this.obstacles.some(
        ({ footprint, top }) => this.feet === top && boundaryDistance(footprint, this.x, this.z) > -PLAYER_RADIUS,
      ))
      if (this.grounded && this.verticalSpeed < 0) this.verticalSpeed = 0
    }
  }
}
