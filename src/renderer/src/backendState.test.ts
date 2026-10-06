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
let statusUrl: string | null = null
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
      return { url: statusUrl, error: statusUrl ? null : 'Backend not running' }
    },
    backendToken: async () => '',
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

// Last: module-level `inited` / `loadedOnce` are shared across the file.
test('a data-load failure after a healthy /health shows the error screen and stays retryable', async () => {
  const realFetch = globalThis.fetch
  const hits: string[] = []
  globalThis.fetch = (async (url: string) => {
    hits.push(String(url))
    if (String(url).endsWith('/health')) return new Response(JSON.stringify({ ok: true }), { status: 200 })
    if (String(url).includes('/conversations')) return new Response(JSON.stringify({ detail: 'db locked' }), { status: 500 })
    return new Response(JSON.stringify([]), { status: 200 })
  }) as typeof fetch
  try {
    useStore.setState({ ready: false, backendError: null })
    statusUrl = 'http://127.0.0.1:1'
    const before = statusCalls
    await useStore.getState().init()
    assert.equal(useStore.getState().ready, true)
    assert.match(useStore.getState().backendError ?? '', /^The backend is running, but your data could not be loaded: db locked/)
    await useStore.getState().init()
    assert.equal(statusCalls, before + 2, 'the guard was released, so init ran the load again')
  } finally {
    globalThis.fetch = realFetch
  }
})
