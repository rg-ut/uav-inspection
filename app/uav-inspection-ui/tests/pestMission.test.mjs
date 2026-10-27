import test from 'node:test'
import assert from 'node:assert/strict'
import { createMission, advanceMission, startMission, pauseMission, optimizeMission, returnMission, missionPose, missionProgress, restoreMission, CAPTURE_SECONDS, REQUIRED_VIEWS, HOME } from '../src/utils/pestMission.ts'
import { observationTargets, observationCoverage, viewSector, routeLength } from '../src/utils/pestSimulation.ts'

function services(visibility = 1) {
  const calls = []
  return {
    calls,
    plan(target, origin, strategy, history, avoided) {
      calls.push({ target, origin: [...origin], strategy })
      const seen = new Set(history.filter(o => o.target === target).map(o => o.sector))
      const sector = Array.from({ length: 8 }, (_, i) => i).find(i => !seen.has(i) && !avoided.includes(i)) ?? 0
      const center = observationTargets[target].point, angle = (sector + .3) * Math.PI / 4
      const position = [center[0] + Math.cos(angle) * 6, 6.2, center[2] + Math.sin(angle) * 6]
      return { before: .5, after: 1 - visibility, position, target, sector: viewSector(position, center), candidates: 36, strategy, yaw: 45, pitch: -30 }
    },
    observe(target, position) { calls.push({ capture: target, position: [...position] }); return visibility },
  }
}
function until(m, svc, predicate, limit = 10000) {
  for (let i = 0; !predicate(m) && i < limit; i++) advanceMission(m, .1, 20, 'semantic', svc)
  assert(predicate(m), `condition not reached: ${m.status}/${m.phase}`)
}
test('full mission plans NBV legs, flies, hovers and captures three views per suspect plant before returning', () => {
  const m = createMission(), svc = services()
  startMission(m)
  until(m, svc, s => s.status === 'completed')
  assert.equal(m.aborted, false)
  assert.equal(missionProgress(m), 100)
  assert.deepEqual(missionPose(m).position, HOME)
  assert.deepEqual(m.plants, ['reviewed', 'reviewed', 'reviewed', 'surveyed'])
  assert.equal(m.observations.length, 9)
  assert.equal(m.plans.length, 9)
  assert(m.plans.every(plan => plan.state === 'captured' && plan.capturedAt >= plan.arrivedAt + CAPTURE_SECONDS - 1e-6))
  for (const record of m.observations) {
    const plan = m.plans.find(p => p.id === record.viewId)
    assert.deepEqual(record.position, plan.position)
    assert.equal(record.capturedAt, plan.capturedAt)
    assert.equal(record.source, 'flight')
  }
})
test('planning and transit never generate observations; only a completed hover does', () => {
  const m = createMission('maize-orbit'), svc = services()
  startMission(m); until(m, svc, s => s.phase === 'transit')
  assert.equal(m.observations.length, 0)
  assert.equal(m.pending.state, 'planned')
  const destination = [...m.pending.position]
  until(m, svc, s => s.phase === 'observe')
  assert.deepEqual(missionPose(m).position, destination)
  assert.equal(m.observations.length, 0)
  advanceMission(m, .5, 20, 'semantic', svc)
  assert.equal(m.observations.length, 0)
  until(m, svc, s => s.observations.length === 1)
  assert.deepEqual(m.observations[0].position, destination)
})
test('paused replanning replaces a flight leg without teleporting or recording the cancelled plan', () => {
  const m = createMission('maize-orbit'), svc = services()
  startMission(m); until(m, svc, s => s.phase === 'transit')
  advanceMission(m, .3, 20, 'semantic', svc); pauseMission(m)
  const position = [...missionPose(m).position], firstId = m.pending.id, firstDestination = [...m.pending.position]
  optimizeMission(m, m.activeTarget, 'coverage', svc)
  assert.equal(m.status, 'paused')
  assert.deepEqual(missionPose(m).position, position)
  assert.deepEqual(m.leg[0], position)
  assert.notDeepEqual(m.pending.position, firstDestination)
  assert.equal(m.plans.find(p => p.id === firstId).state, 'cancelled')
  assert.equal(m.observations.length, 0)
  const before = JSON.stringify(m)
  advanceMission(m, 1, 20, 'coverage', svc)
  assert.equal(JSON.stringify(m), before)
  startMission(m); until(m, svc, s => s.observations.length > 0)
  assert(!m.observations.some(o => o.viewId === firstId))
})
test('pause and reload during hover preserve remaining capture time and pending plan identity', () => {
  let m = createMission('maize-orbit'); const svc = services()
  startMission(m); until(m, svc, s => s.phase === 'observe')
  advanceMission(m, .4, 20, 'semantic', svc)
  m = restoreMission(JSON.parse(JSON.stringify(m)))
  assert(m)
  assert.equal(m.status, 'paused')
  const remaining = m.captureRemaining, elapsed = m.elapsed
  advanceMission(m, 1, 20, 'semantic', svc)
  assert.equal(m.captureRemaining, remaining); assert.equal(m.elapsed, elapsed)
  startMission(m); until(m, svc, s => s.observations.length === 1)
  assert.equal(m.plans[0].state, 'captured')
})
test('early return cancels a pending hover and preserves unfinished work instead of claiming 100%', () => {
  const m = createMission('maize-orbit'), svc = services()
  startMission(m); until(m, svc, s => s.phase === 'observe')
  const oldId = m.pending.id, before = missionProgress(m)
  returnMission(m)
  assert.equal(m.pending, null)
  assert.equal(m.plans.find(p => p.id === oldId).state, 'cancelled')
  until(m, svc, s => s.status === 'completed')
  assert.deepEqual(missionPose(m).position, HOME)
  assert.equal(m.observations.length, 0)
  assert.equal(m.plants[0], 'needs-review')
  assert.equal(missionProgress(m), before)
  assert(missionProgress(m) < 100)
  const count = m.plans.length
  optimizeMission(m, 0, 'semantic', svc)
  assert.equal(m.plans.length, count)
})
test('idle optimization queues work, while a different plant requested during review waits its turn', () => {
  const m = createMission('maize-orbit'), svc = services()
  optimizeMission(m, 2, 'semantic', svc)
  assert.equal(svc.calls.length, 0); assert.equal(m.observations.length, 0)
  startMission(m); until(m, svc, s => s.phase === 'transit')
  assert.equal(m.activeTarget, 2)
  optimizeMission(m, 1, 'semantic', svc)
  assert.equal(m.activeTarget, 2); assert.equal(m.queue[0], 1)
  until(m, svc, s => s.activeTarget === 1)
  assert.equal(observationCoverage(m.observations, 2), REQUIRED_VIEWS)
  optimizeMission(m, 2, 'semantic', svc)
  assert(!m.queue.includes(2))
})
test('quick survey has no automatic NBV work, but an explicitly queued plant uses the same review pipeline', () => {
  const m = createMission('maize-boundary'), svc = services()
  startMission(m); until(m, svc, s => s.status === 'completed')
  assert.equal(m.plans.length, 0); assert.equal(m.observations.length, 0)
  const withReview = createMission('maize-boundary')
  startMission(withReview); optimizeMission(withReview, 1, 'semantic', svc)
  until(withReview, svc, s => s.status === 'completed')
  assert.equal(withReview.plants[1], 'reviewed')
  assert.equal(withReview.observations.length, 3)
})
test('blocked observations are retried finitely and remain pending review, never marked successful', () => {
  const m = createMission('maize-orbit'), svc = services(0)
  startMission(m); until(m, svc, s => s.status === 'completed')
  assert.equal(m.observations.length, 6)
  assert.equal(observationCoverage(m.observations, 0), 0)
  assert.equal(m.plants[0], 'needs-review')
})
test('corrupt checkpoints are rejected and each planned leg clears the maize canopy', () => {
  assert.equal(restoreMission({}), null)
  const m = createMission('maize-orbit'), svc = services()
  startMission(m); until(m, svc, s => s.phase === 'transit')
  assert(m.leg[1][1] >= 10 && m.pending.position[1] >= 5.5)
  assert(routeLength(m.leg) > 0)
  const broken = JSON.parse(JSON.stringify(m)); broken.pending.id = 'missing'
  assert.equal(restoreMission(broken), null)
})
