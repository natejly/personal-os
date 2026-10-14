import test from 'node:test'
import assert from 'node:assert/strict'
import {
  clampDim, createTermHub, isSessionId, MAX_PTYS, pickArgv, Ring, RING_CAP, scrubEnv, WRITE_CAP,
  type HubDeps, type Pty, type SessionInfo
} from './terminal'

const SID = 'a1b2c3d4e5f60718'
const sid = (n: number): string => n.toString(16).padStart(16, '0')

class FakePty implements Pty {
  data: (d: string) => void = () => {}
  exit: (c: number | null) => void = () => {}
  written: string[] = []
  sizes: Array<[number, number]> = []
  killed = false
  onData(cb: (d: string) => void): void { this.data = cb }
  onExit(cb: (c: number | null) => void): void { this.exit = cb }
  write(d: string): void { this.written.push(d) }
  resize(c: number, r: number): void { this.sizes.push([c, r]) }
  kill(): void { this.killed = true }
}

function rig(over: Partial<HubDeps> = {}, session: SessionInfo | null = { agent: 'claude', external_id: 'deadbeef', worktree: '/work/tree' }) {
  const ptys: FakePty[] = []
  const spawns: Array<{ argv: string[]; cwd: string; env: Record<string, string> }> = []
  const sent: Array<{ senderId: number; channel: string; payload: Record<string, unknown> }> = []
  let n = 0
  const hub = createTermHub({
    spawn: (argv, o) => { const p = new FakePty(); ptys.push(p); spawns.push({ argv, cwd: o.cwd, env: o.env }); return p },
    fetchSession: async () => session,
    isDirectory: (p) => p === '/work/tree',
    send: (senderId, channel, payload) => { sent.push({ senderId, channel, payload }) },
    env: { SHELL: '/bin/zsh', HOME: '/home/u', PERSONAL_OS_AUTH_TOKEN: 'secret' },
    platform: 'darwin',
    isPackaged: false,
    newId: () => `t${++n}`,
    ...over
  })
  return { hub, ptys, spawns, sent }
}
const sleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms))

test('pickArgv opencode mode runs a fixed argv with the id and folder as parameters', () => {
  const ok = { agent: 'opencode', session_id: 'ses_ee1ad8d25ffeAw2DdV0gMLBAkm', status: 'done', worktree: '/w t', mode: 'opencode', shell: '/bin/zsh' }
  assert.deepEqual(pickArgv(ok), ['/bin/zsh', '-l', '-c', 'exec opencode -s "$1" "$2"', 'opencode', 'ses_ee1ad8d25ffeAw2DdV0gMLBAkm', '/w t'])
  for (const bad of [{ agent: 'claude' }, { session_id: 'ses_a b' }, { session_id: null }, { status: 'working' }, { worktree: 'rel' }]) {
    assert.ok('error' in (pickArgv({ ...ok, ...bad }) as object), JSON.stringify(bad))
  }
})

test('session id validation is strict', () => {
  assert.ok(isSessionId(SID))
  for (const bad of ['', 'A1B2C3D4E5F60718', 'a1b2c3d4', SID + '0', '../etc/passwd', `${SID}/x`, `${SID}\n`, 5, null, undefined, {}]) {
    assert.equal(isSessionId(bad), false, String(bad))
  }
})

test('argv: attach only for claude with a valid external id', () => {
  assert.deepEqual(pickArgv({ agent: 'claude', external_id: 'deadbeef', mode: 'attach', shell: '/bin/zsh' }), ['/bin/zsh', '-l', '-c', 'claude attach deadbeef'])
  for (const agent of ['opencode', 'pi', 'x', undefined]) {
    assert.ok('error' in (pickArgv({ agent, external_id: 'deadbeef', mode: 'attach', shell: '/bin/zsh' }) as object))
  }
  for (const id of [null, '', 'DEADBEEF', 'deadbee', 'deadbeef1', 'deadbeef; rm -rf /', '$(id)abcd']) {
    assert.ok('error' in (pickArgv({ agent: 'claude', external_id: id, mode: 'attach', shell: '/bin/zsh' }) as object), String(id))
  }
  assert.ok('error' in (pickArgv({ agent: 'claude', external_id: 'deadbeef', mode: 'run', shell: '/bin/zsh' }) as object))
})

test('argv: shell mode is a login shell for any agent', () => {
  for (const agent of ['claude', 'opencode', 'pi']) {
    assert.deepEqual(pickArgv({ agent, external_id: null, mode: 'shell', shell: '/bin/bash' }), ['/bin/bash', '-l'])
  }
})

test('default shell falls back by platform', async () => {
  const a = rig({ env: {}, platform: 'darwin' })
  await a.hub.open(1, SID, 'shell', 80, 24)
  assert.equal(a.spawns[0].argv[0], '/bin/zsh')
  const b = rig({ env: {}, platform: 'linux' })
  await b.hub.open(1, SID, 'shell', 80, 24)
  assert.equal(b.spawns[0].argv[0], '/bin/bash')
})

test('clampDim', () => {
  assert.equal(clampDim(0), 1)
  assert.equal(clampDim(-5), 1)
  assert.equal(clampDim(10000), 500)
  assert.equal(clampDim(80.6), 81)
  assert.equal(clampDim(NaN), 80)
  assert.equal(clampDim(Infinity, 24), 24)
  assert.equal(clampDim('9', 24), 24)
})

test('env scrub removes secrets and sets terminal vars', () => {
  const out = scrubEnv({
    PATH: '/bin', HOME: '/h', PERSONAL_OS_AUTH_TOKEN: 'a', ANTHROPIC_API_KEY: 'b', FIREWORKS_API_KEY: 'c',
    GITHUB_TOKEN: 'd', AWS_SECRET_ACCESS_KEY: 'e', DB_PASSWORD: 'f', MY_CREDENTIALS: 'g', TERM: 'dumb'
  })
  assert.deepEqual(Object.keys(out).sort(), ['COLORTERM', 'HOME', 'PATH', 'TERM'])
  assert.equal(out.TERM, 'xterm-256color')
  assert.equal(out.COLORTERM, 'truecolor')
})

test('ring buffer drops the oldest output', () => {
  const r = new Ring(10)
  r.push('abcdef')
  r.push('ghijkl')
  assert.equal(r.get(), 'cdefghijkl')
  const big = new Ring()
  big.push('x'.repeat(RING_CAP + 50))
  assert.equal(big.get().length, RING_CAP)
})

test('open spawns from backend fields only, with scrubbed env', async () => {
  const { hub, spawns } = rig()
  const r = await hub.open(1, SID, 'attach', 100, 30)
  assert.ok('termId' in r && r.fresh && r.replay === '')
  assert.deepEqual(spawns[0].argv, ['/bin/zsh', '-l', '-c', 'claude attach deadbeef'])
  assert.equal(spawns[0].cwd, '/work/tree')
  assert.equal(spawns[0].env.PERSONAL_OS_AUTH_TOKEN, undefined)
})

test('open rejects bad ids, modes, missing sessions and missing folders', async () => {
  const { hub, spawns } = rig()
  assert.ok('error' in (await hub.open(1, 'nope', 'shell', 80, 24)))
  assert.ok('error' in (await hub.open(1, SID, 'bash', 80, 24)))
  assert.ok('error' in (await rig({}, null).hub.open(1, SID, 'shell', 80, 24)))
  assert.ok('error' in (await rig({}, { agent: 'claude', worktree: '/nope' }).hub.open(1, SID, 'shell', 80, 24)))
  assert.ok('error' in (await rig({}, { agent: 'claude', worktree: 'relative' }).hub.open(1, SID, 'shell', 80, 24)))
  assert.ok('error' in (await rig({}, { agent: 'opencode', external_id: 'deadbeef', worktree: '/work/tree' }).hub.open(1, SID, 'attach', 80, 24)))
  assert.equal(spawns.length, 0)
})

test('write cap, foreign termId and foreign sender', async () => {
  const { hub, ptys } = rig()
  const r = await hub.open(1, SID, 'shell', 80, 24)
  assert.ok('termId' in r)
  const id = (r as { termId: string }).termId
  assert.equal(hub.write(1, id, 'ls\n'), true)
  assert.equal(hub.write(1, id, 'x'.repeat(WRITE_CAP)), true)
  assert.equal(hub.write(1, id, 'x'.repeat(WRITE_CAP + 1)), false)
  assert.equal(hub.write(1, id, 42), false)
  assert.equal(hub.write(1, 'unknown', 'a'), false)
  assert.equal(hub.write(2, id, 'a'), false)
  assert.equal(hub.resize(2, id, 10, 10), false)
  assert.equal(hub.close(2, id), false)
  assert.equal(hub.resize(1, 'unknown', 10, 10), false)
  assert.equal(hub.close(1, 'unknown'), false)
  assert.equal(ptys[0].written.length, 2)
  assert.equal(ptys[0].killed, false)
  assert.equal(hub.resize(1, id, 9999, 0), true)
  assert.deepEqual(ptys[0].sizes.at(-1), [500, 1])
  assert.equal(hub.close(1, id), true)
  assert.equal(ptys[0].killed, true)
  assert.equal(hub.write(1, id, 'a'), false)
})

test('another window cannot reattach a terminal it does not own', async () => {
  const { hub } = rig()
  await hub.open(1, SID, 'shell', 80, 24)
  assert.ok('error' in (await hub.open(2, SID, 'shell', 80, 24)))
})

test('at most MAX_PTYS terminals', async () => {
  const { hub } = rig()
  for (let i = 0; i < MAX_PTYS; i++) assert.ok('termId' in (await hub.open(1, sid(i), 'shell', 80, 24)))
  assert.ok('error' in (await hub.open(1, sid(99), 'shell', 80, 24)))
  assert.equal(hub.size(), MAX_PTYS)
})

test('re-open returns the same pty with a replay, then batches output', async () => {
  const { hub, ptys, sent, spawns } = rig()
  const a = (await hub.open(1, SID, 'shell', 80, 24)) as { termId: string }
  ptys[0].data('hello ')
  ptys[0].data('world')
  await sleep(40)
  const flushed = sent.filter((s) => s.channel === 'term:data')
  assert.equal(flushed.length, 1)
  assert.deepEqual(flushed[0].payload, { termId: a.termId, data: 'hello world' })
  const b = await hub.open(1, SID, 'shell', 120, 40)
  assert.deepEqual(b, { termId: a.termId, replay: 'hello world', fresh: false })
  assert.equal(spawns.length, 1)
  assert.deepEqual(ptys[0].sizes.at(-1), [120, 40])
  // concurrent opens share one spawn
  const [x, y] = await Promise.all([hub.open(1, sid(5), 'shell', 80, 24), hub.open(1, sid(5), 'shell', 80, 24)])
  assert.deepEqual(x, y)
  assert.equal(spawns.length, 2)
})

test('exit flushes, reports, and lets the next open respawn', async () => {
  const { hub, ptys, sent, spawns } = rig()
  const a = (await hub.open(1, SID, 'shell', 80, 24)) as { termId: string }
  ptys[0].data('bye')
  ptys[0].exit(3)
  assert.deepEqual(sent.map((s) => s.channel), ['term:data', 'term:exit'])
  assert.deepEqual(sent[1].payload, { termId: a.termId, code: 3 })
  assert.equal(hub.write(1, a.termId, 'x'), false)
  const b = (await hub.open(1, SID, 'shell', 80, 24)) as { termId: string; fresh: boolean }
  assert.equal(b.fresh, true)
  assert.equal(spawns.length, 2)
})

test('killSender and killAll', async () => {
  const { hub, ptys } = rig()
  await hub.open(1, sid(1), 'shell', 80, 24)
  await hub.open(2, sid(2), 'shell', 80, 24)
  hub.killSender(1)
  assert.deepEqual(ptys.map((p) => p.killed), [true, false])
  hub.killAll()
  assert.equal(ptys[1].killed, true)
  assert.equal(hub.size(), 0)
})

test('test-only fake command is honoured unpackaged and ignored when packaged', async () => {
  const dev = rig({ fakeCmd: 'cat -u', isPackaged: false })
  await dev.hub.open(1, SID, 'shell', 80, 24)
  assert.deepEqual(dev.spawns[0].argv, ['cat', '-u'])
  assert.equal(dev.spawns[0].cwd, '/work/tree')
  const packaged = rig({ fakeCmd: 'cat -u', isPackaged: true })
  await packaged.hub.open(1, SID, 'shell', 80, 24)
  assert.deepEqual(packaged.spawns[0].argv, ['/bin/zsh', '-l'])
})
