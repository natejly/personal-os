/**
 * The renderer follows the main process's supervisor: a restart shows state without tearing the UI down,
 * a failure shows the error screen, and a recovery re-runs init when nothing was ever loaded.
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { useStore } from './store'
import type { BackendInfo } from '@shared/types'

let push: ((i: BackendInfo) => void) | null = null
let statusCalls = 0
const info = (state: BackendInfo['state'], error: string | null = null): BackendInfo =>
  ({ state, url: '', error, restarts: [], logDir: '', appVersion: '0', electron: '0' })

;(globalThis as unknown as { window: unknown }).window = {
  os: {
    onMenu: () => () => undefined,
    onBackendState: (cb: (i: BackendInfo) => void) => {
      push = cb
      return () => undefined
    },
    backendStatus: async () => {
      statusCalls++
      return { url: null, error: 'Backend not running' }
    },
    restartBackend: async () => info('ready')
  }
}

test('restarting is only a state; failed shows the error; ready re-runs init when nothing had loaded', async () => {
  await useStore.getState().init()
  assert.equal(statusCalls, 1)
  assert.ok(push, 'init subscribes to the supervisor')

  push!(info('restarting'))
  assert.equal(useStore.getState().backendState, 'restarting')

  push!(info('failed', 'crashed 5 times'))
  assert.equal(useStore.getState().backendState, 'failed')
  assert.equal(useStore.getState().backendError, 'crashed 5 times')

  push!(info('ready'))
  await new Promise((r) => setTimeout(r, 0))
  assert.equal(useStore.getState().backendState, 'ready')
  assert.equal(statusCalls, 2) // init ran again: the first start had failed, so nothing was loaded yet
})

test('restartBackend asks the main process and applies its answer', async () => {
  await useStore.getState().restartBackend()
  assert.equal(useStore.getState().backendState, 'ready')
})
