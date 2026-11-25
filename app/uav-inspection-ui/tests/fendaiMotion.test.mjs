import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { FendaiMotion, groundHeight } from '../src/utils/fendaiMotion.ts'

const profile = {
  origin: [0, 0],
  step: 10,
  heights: [[0, 1.5], [0.5, 2]],
  boundary: [[0, 0], [10, 0], [10, 10], [0, 10]],
  spawn: [2, 5, 0, 0],
  flightLimit: 7,
  obstacles: [{ footprint: [[5, 4], [7, 4], [7, 6], [5, 6]], base: 0, top: 3 }],
}

function travel(player, duration, dx, dz, rise, mode = 'walk', fast = false) {
  for (let frame = 0; frame < Math.round(duration * 60); frame++) {
    player.advance(1 / 60, dx, dz, rise, mode, fast)
  }
}

test('walking follows terrain and sprinting covers more ground', () => {
  const walker = new FendaiMotion(profile)
  const runner = new FendaiMotion(profile)
  travel(walker, 0.5, 0, 1, 0)
  travel(runner, 0.5, 0, 1, 0, 'walk', true)
  assert(runner.z > walker.z)
  assert.equal(walker.feet, groundHeight(profile, walker.x, walker.z))
  assert.equal(runner.feet, groundHeight(profile, runner.x, runner.z))
})

test('jump rises from the surface, cannot retrigger midair and settles on the ground', () => {
  const player = new FendaiMotion(profile)
  const ground = player.feet
  assert(Math.abs(player.eye - player.feet - 1.8) < 1e-10)
  player.jump()
  travel(player, 0.25, 0, 0, 0)
  assert(player.feet > ground + 0.6)
  assert(!player.grounded)
  player.jump()
  travel(player, 0.6, 0, 0, 0)
  assert.equal(player.feet, ground)
  assert(player.grounded)
})

test('both modes respect obstacles and the edge, with flight above the roof and a ceiling', () => {
  const player = new FendaiMotion(profile)
  travel(player, 2, 1, 0, 0, 'walk', true)
  assert(player.x > 4.5 && player.x < 4.7)
  travel(player, 2, 0, 0, 1, 'fly')
  assert.equal(player.feet, groundHeight(profile, player.x, player.z) + profile.flightLimit)
  travel(player, 0.25, 1, 0, 0, 'fly')
  assert(player.x > 5.5 && player.x < 7)
  travel(player, 2, 0, 0, -1, 'fly')
  assert.equal(player.feet, 3)
  travel(player, 2, 1, 0, 0, 'fly', true)
  assert(player.x > 9.5 && player.x < 9.7)
  player.changeMode()
  travel(player, 1, 0, 0, 0)
  assert.equal(player.feet, groundHeight(profile, player.x, player.z))
  player.reset()
  assert.deepEqual([player.x, player.z], profile.spawn.slice(0, 2))
})

test('the calibrated spawn is on the path and clear of the boundary and pavilions', () => {
  const garden = JSON.parse(readFileSync(new URL(
    '../../uav-inspection-backend/fendaiapp/garden.json', import.meta.url,
  ), 'utf8'))
  const [x, z, yaw] = garden.spawn
  assert(Math.abs(groundHeight(garden, x, z) - 4.73) < 0.07)
  assert(garden.path.some(([px, pz]) => px === x && pz === z))
  const player = new FendaiMotion(garden)
  travel(player, 1, -Math.sin(yaw), -Math.cos(yaw), 0)
  assert(Math.hypot(player.x - x, player.z - z) > 2.7)
  assert.equal(player.feet, groundHeight(garden, player.x, player.z))
})

test('registered bench models sit on the path and block walking', () => {
  const withBench = {
    ...profile,
    spawn: [2, 5, 0, 0],
    obstacles: [],
    pathDetail: {
      sections: [[-4, 5, 0.5, 1.2, 1.2], [0, 5, 0.5, 1.2, 1.2], [10, 5, 0.5, 1.2, 1.2], [14, 5, 0.5, 1.2, 1.2]],
      benches: [[5, 5, 0.5, 0]],
    },
  }
  const player = new FendaiMotion(withBench)
  assert.equal(player.feet, 0.5)
  travel(player, 2, 1, 0, 0)
  assert(player.x > 3.7 && player.x < 3.83)
  assert.equal(player.feet, 0.5)
})
