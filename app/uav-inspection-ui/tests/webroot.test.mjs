import assert from 'node:assert/strict'
import test from 'node:test'
import { webPath, wsPath } from '../src/utils/webroot.ts'

test('root deployment produces a same-origin API path, not a protocol-relative URL', () => {
  assert.equal(webPath('/api/v1/fendai/scene', '/'), '/api/v1/fendai/scene')
  assert.equal(webPath('/api/deployment/status', '/'), '/api/deployment/status')
})

test('subpath deployment prefixes API paths once and preserves queries and trailing slashes', () => {
  assert.equal(webPath('/api/v1/fendai/scene', '/uav/'), '/uav/api/v1/fendai/scene')
  assert.equal(webPath('/api/v1/fendai/scene', '/uav'), '/uav/api/v1/fendai/scene')
  assert.equal(webPath('/assets/?kind=models', '/uav/'), '/uav/assets/?kind=models')
  assert.equal(webPath('/assets/', '/uav/'), '/uav/assets/')
  assert.equal(webPath('/', '/uav/'), '/uav/')
})

test('external and relative resources are preserved', () => {
  for (const path of ['', 'models/flower.ksplat', 'https://cdn.example/flower.ksplat', '//cdn.example/flower.ksplat', 'blob:model', 'data:text/plain,model']) {
    assert.equal(webPath(path, '/uav/'), path)
  }
})

test('root WebSocket path uses the current host and secure scheme', () => {
  globalThis.location = { protocol: 'https:', host: 'inspection.example' }
  assert.equal(wsPath('/ws/events'), 'wss://inspection.example/ws/events')
  delete globalThis.location
})
