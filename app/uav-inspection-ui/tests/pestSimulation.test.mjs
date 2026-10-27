import test from 'node:test'
import assert from 'node:assert/strict'
import { routeLength, sampleRoute, returnRoute, simulationRoutes, simulationBattery, simulationCommand, readReports, simulationReportHtml, observationTargets, candidateScore, observationCoverage, viewSector } from '../src/utils/pestSimulation.ts'

test('visibility and novel directions drive coverage; duplicate or blocked views add no coverage', () => {
  assert(candidateScore('coverage', .9, true, .5) > candidateScore('coverage', .9, false, .5))
  assert(candidateScore('semantic', 1, true, .5) > candidateScore('semantic', 0, true, .5))
  const plant = observationTargets[0].point
  assert.equal(viewSector([plant[0] + 6, 5, plant[2]], plant), 0)
  const observations = [{ target: 0, sector: 0, visible: 1 }, { target: 0, sector: 0, visible: 1 }, { target: 0, sector: 1, visible: .2 }, { target: 1, sector: 2, visible: 1 }]
  assert.equal(observationCoverage(observations, 0), 1)
  assert.equal(observationCoverage(observations, 1), 1)
})

test('flight interpolation is distance-based and finishes on the final waypoint', () => {
  const points = [[0, 0, 0], [0, 3, 4], [10, 3, 4]]
  assert.equal(routeLength(points), 15)
  assert.deepEqual(sampleRoute(points, 2.5).position, [0, 1.5, 2])
  assert.deepEqual(sampleRoute(points, 10).position, [5, 3, 4])
  assert.deepEqual(sampleRoute(points, 999).position, [10, 3, 4])
})
test('every route starts and lands at home and emergency return climbs before crossing the park', () => {
  for (const route of simulationRoutes) {
    assert.deepEqual(route.points[0], route.points.at(-1))
    assert(routeLength(route.points) > 50)
    assert.equal(sampleRoute(route.points, routeLength(route.points)).waypoint, route.points.length - 1)
    assert.deepEqual(sampleRoute(route.points, routeLength(route.points)).position, route.points.at(-1))
  }
  assert.deepEqual(returnRoute([20, 4, -10]), [[20, 4, -10], [20, 12, -10], [0, 12, 33], [0, .7, 33]])
})
test('assistant dispatches supported simulation commands without inventing execution', () => {
  assert.equal(simulationCommand('暂停巡航'), 'pause')
  assert.equal(simulationCommand('返航'), 'return')
  assert.equal(simulationCommand('优化当前视点'), 'optimize')
  assert.equal(simulationCommand('今天会下雨吗'), 'help')
  assert.equal(simulationBattery(180), 90)
  assert.equal(simulationBattery(99999), 0)
})
test('reports reject corrupt storage and HTML escapes names and declares simulated data', () => {
  assert.deepEqual(readReports('{bad'), [])
  assert.deepEqual(readReports('[null,{}]'), [])
  const report = { id: 'test', createdAt: 'now', routeName: '<script>alert(1)</script>', status: 'completed', elapsed: 12, distance: 20, samples: [], viewpoints: [] }
  assert.equal(readReports(JSON.stringify([report])).length, 1)
  const html = simulationReportHtml(report)
  assert(!html.includes('<script>'))
  assert(html.includes('&lt;script&gt;'))
  assert(html.includes('任务类型：场景演示'))
  const interrupted = simulationReportHtml({ ...report, outcome: 'interrupted', progress: 10.4 })
  assert(interrupted.includes('中途返航'))
  assert(interrupted.includes('10.4%'))
})
