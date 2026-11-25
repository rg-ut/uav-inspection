type Point = [number, number]

export interface GardenProfile {
  origin: Point
  step: number
  heights: number[][]
  boundary: Point[]
  path: Point[]
  spawn: [number, number, number, number]
  flightLimit: number
  obstacles: { footprint: Point[]; base: number; top: number }[]
  modelPosition: [number, number, number]
  modelRotation: [number, number, number, number]
  modelScale: number
}

export type TravelMode = 'walk' | 'fly'

const EYE_HEIGHT = 1.65
const PLAYER_RADIUS = 0.32
const PLAYER_HEIGHT = 1.8
const GRAVITY = 13
const JUMP_SPEED = 4.8

function clamp(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, value))
}

export function groundHeight(profile: GardenProfile, x: number, z: number) {
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

  constructor(profile: GardenProfile) {
    this.profile = profile
    this.reset()
  }

  get eye() {
    return this.feet + EYE_HEIGHT
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
      && !this.profile.obstacles.some(({ footprint, base, top }) =>
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
        for (const { footprint, top } of this.profile.obstacles) {
          if (this.feet >= top && nextFeet < top && boundaryDistance(footprint, this.x, this.z) > -PLAYER_RADIUS) {
            this.feet = top
          }
        }
      }
      this.grounded = this.feet <= ground + 0.001 || (this.verticalSpeed < 0 && this.profile.obstacles.some(
        ({ footprint, top }) => this.feet === top && boundaryDistance(footprint, this.x, this.z) > -PLAYER_RADIUS,
      ))
      if (this.grounded && this.verticalSpeed < 0) this.verticalSpeed = 0
    }
  }
}
