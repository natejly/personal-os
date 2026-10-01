import test from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, mkdtempSync, readFileSync, readdirSync } from 'fs'
import { tmpdir } from 'os'
import { join } from 'path'
import { RotatingLog, redact, registerSecret } from './logging'
import { BACKOFF_MS, RestartPolicy } from './restartPolicy'

test('redact masks headers, key=value pairs, query credentials and provider token shapes', () => {
  const cases: Array<[string, string]> = [
    ['Authorization: Bearer abcdef1234567890', 'abcdef1234567890'],
    ["headers: {'X-Personal-OS-Token': 'tok_abcdefghijkl'}", 'tok_abcdefghijkl'],
    ['{"apiKey": "sk-live-abcdefghijklmnop"}', 'sk-live-abcdefghijklmnop'],
    ['GET https://x.test/v1?key=AIzaSyDUMMYDUMMYDUMMY&q=1', 'AIzaSyDUMMY'],
    ['leaked sk-proj-abcdefghijklmnopqrstuv here', 'sk-proj-abcdefghijklmnopqrstuv'],
    ['ghp_abcdefghijklmnopqrstuvwxyz0123', 'ghp_abcdefghijklmnopqrstuvwxyz0123'],
    ['jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnop', 'eyJzdWIiOiIx']
  ]
  for (const [line, secret] of cases) {
    const out = redact(line)
    assert.ok(!out.includes(secret), `${line} -> ${out}`)
    assert.ok(out.includes('[redacted]'), out)
  }
})

test('redact leaves ordinary lines alone and masks a registered literal in any shape', () => {
  const line = 'spawned pid 4242 on http://127.0.0.1:50321'
  assert.equal(redact(line), line)
  registerSecret('plain-looking-session-token')
  assert.equal(redact('saw plain-looking-session-token here'), 'saw [redacted] here')
})

test('RotatingLog rolls at the size limit and keeps a bounded number of backups', () => {
  const dir = mkdtempSync(join(tmpdir(), 'grain-log-'))
  const path = join(dir, 'main.log')
  const log = new RotatingLog(path, 100, 2)
  for (let i = 0; i < 30; i++) log.write(`line ${i} ${'x'.repeat(20)}`)
  const files = readdirSync(dir).sort()
  assert.deepEqual(files, ['main.log', 'main.log.1', 'main.log.2'])
  assert.ok(!existsSync(`${path}.3`))
  assert.ok(readFileSync(path, 'utf8').includes('line 29'))
  assert.ok(readFileSync(`${path}.1`, 'utf8').length <= 160)
})

test('RotatingLog redacts what it writes', () => {
  const dir = mkdtempSync(join(tmpdir(), 'grain-log-'))
  const log = new RotatingLog(join(dir, 'p.log'))
  log.write('Authorization: Bearer abcdef1234567890')
  assert.ok(!readFileSync(join(dir, 'p.log'), 'utf8').includes('abcdef1234567890'))
})

test('RestartPolicy backs off, then gives up after too many exits inside the window', () => {
  const p = new RestartPolicy(3, 60_000, [1000, 2000, 4000])
  assert.equal(p.next(0), 1000)
  assert.equal(p.next(1_000), 2000)
  assert.equal(p.next(2_000), 4000)
  assert.equal(p.next(3_000), null) // fourth exit in a minute
})

test('RestartPolicy forgets exits that fall out of the window, and on reset', () => {
  const p = new RestartPolicy(2, 60_000, [1000, 2000])
  assert.equal(p.next(0), 1000)
  assert.equal(p.next(10_000), 2000)
  assert.equal(p.next(65_000), 2000) // the first exit has aged out; two remain
  p.reset()
  assert.equal(p.next(70_002), 1000)
  assert.equal(BACKOFF_MS[0], 1000)
})
