import test from 'node:test'
import assert from 'node:assert/strict'
import {
  ARTIFACT_PROTOCOL, ARTIFACT_SOURCE, MAX_FRAME_HEIGHT, MAX_RAW_CHARS, MAX_TITLE_CHARS, MIN_FRAME_HEIGHT,
  clampHeight, encode, isQuiet, parseMessage, sanitizeTitle,
  type ArtifactMessage, type ParseResult, type RejectReason
} from './bridge'
import {
  ARTIFACT_KINDS, baseName, decideSave, exportName, extensionFor, forkTitle, kindFromLanguage, mimeFor,
  previewMode, slugify, type ArtifactKind, type SaveAttempt
} from './source'

const wire = (message: ArtifactMessage, over: Record<string, unknown> = {}): Record<string, unknown> => ({ ...encode(message), ...over })
const resize = (over: Record<string, unknown> = {}): Record<string, unknown> => wire({ type: 'resize', height: 400 }, over)
const titled = (over: Record<string, unknown> = {}): Record<string, unknown> => wire({ type: 'setTitle', title: 'Sales chart' }, over)

const rejects = (raw: unknown, reason: RejectReason): ParseResult => {
  const r = parseMessage(raw)
  assert.equal(r.ok, false)
  if (r.ok) throw new Error('unreachable')
  assert.equal(r.reason, reason, `detail: ${r.detail}`)
  assert.equal(typeof r.detail, 'string')
  return r
}
const accepted = (raw: unknown): ArtifactMessage => {
  const r = parseMessage(raw)
  if (!r.ok) throw new Error(`expected accept, got ${r.reason}: ${r.detail}`)
  return r.message
}

// ---------------- bridge: the accepted vocabulary ----------------

test('an encoded message round-trips through the parser', () => {
  for (const m of [{ type: 'resize' as const, height: 400 }, { type: 'setTitle' as const, title: 'Sales chart' }]) {
    assert.deepEqual(accepted(encode(m)), m)
  }
})

test('the envelope carries the protocol tag and version', () => {
  assert.deepEqual(encode({ type: 'resize', height: 400 }), { source: ARTIFACT_SOURCE, v: ARTIFACT_PROTOCOL, type: 'resize', height: 400 })
})

test('a resize outside the frame bounds is clamped, not rejected', () => {
  assert.deepEqual(accepted(resize({ height: 10 ** 9 })), { type: 'resize', height: MAX_FRAME_HEIGHT })
  assert.deepEqual(accepted(resize({ height: -50 })), { type: 'resize', height: MIN_FRAME_HEIGHT })
  assert.deepEqual(accepted(resize({ height: 400.6 })), { type: 'resize', height: 401 })
  assert.equal(clampHeight(500), 500)
})

test('a title is cleaned of control, bidi and zero-width characters', () => {
  assert.deepEqual(accepted(titled({ title: '  Q3\treport\n\n ' })), { type: 'setTitle', title: 'Q3 report' })
  assert.deepEqual(accepted(titled({ title: 'safe\u202e\u202eevil\u200b' })), { type: 'setTitle', title: 'safe evil' })
  assert.equal(sanitizeTitle('a'.repeat(MAX_TITLE_CHARS + 40)).length, MAX_TITLE_CHARS)
})

// ---------------- bridge: hostile input ----------------

test('a message with an unknown type is rejected', () => {
  rejects(wire({ type: 'resize', height: 400 }, { type: 'eval' }), 'unknown-type')
  rejects(titled({ type: 'navigate' }), 'unknown-type')
  rejects(resize({ type: 'toString' }), 'unknown-type')
  rejects(resize({ type: 'constructor' }), 'unknown-type')
})

test('a message with a missing field is rejected', () => {
  const { height: _h, ...noHeight } = resize()
  rejects(noHeight, 'bad-field')
  const { title: _t, ...noTitle } = titled()
  rejects(noTitle, 'bad-field')
  rejects({ v: ARTIFACT_PROTOCOL, type: 'resize', height: 400 }, 'foreign')
  rejects({ source: ARTIFACT_SOURCE, type: 'resize', height: 400 }, 'bad-protocol')
  rejects({}, 'foreign')
})

test('a message with a wrong field type is rejected', () => {
  rejects(resize({ height: '400' }), 'bad-field')
  rejects(resize({ height: null }), 'bad-field')
  rejects(resize({ height: NaN }), 'bad-field')
  rejects(resize({ height: Infinity }), 'bad-field')
  rejects(resize({ height: { valueOf: () => 400 } }), 'bad-field')
  rejects(titled({ title: 42 }), 'bad-field')
  rejects(titled({ title: ['a'] }), 'bad-field')
  rejects(titled({ title: '   \u0000 ' }), 'bad-field')
  rejects(resize({ type: 7 }), 'bad-field')
  rejects(resize({ v: '1' }), 'bad-protocol')
  rejects(resize({ v: 2 }), 'bad-protocol')
})

test('an unexpected extra key is rejected rather than ignored', () => {
  rejects(resize({ title: 'also this' }), 'bad-field')
  rejects(titled({ onload: 'boom' }), 'bad-field')
})

test('a huge payload is rejected without being walked', () => {
  rejects(titled({ title: 'x'.repeat(MAX_RAW_CHARS + 1) }), 'too-large')
  const wide: Record<string, unknown> = resize()
  for (let i = 0; i < 64; i++) wide[`k${i}`] = i
  rejects(wide, 'too-large')
  rejects(resize({ height: { deep: 'y'.repeat(2_000_000) } }), 'bad-field')
})

test('a prototype-pollution attempt is rejected', () => {
  rejects(JSON.parse('{"__proto__":{"polluted":true},"source":"personal-os-artifact","v":1,"type":"resize","height":400}'), 'unsafe-key')
  rejects(JSON.parse('{"constructor":{"prototype":{}},"source":"personal-os-artifact","v":1,"type":"resize","height":400}'), 'unsafe-key')
  rejects(resize({ prototype: {} }), 'unsafe-key')
  assert.equal(({} as Record<string, unknown>).polluted, undefined)
})

test('a non-object is rejected', () => {
  rejects(null, 'not-an-object')
  rejects(undefined, 'not-an-object')
  rejects('resize', 'not-an-object')
  rejects(400, 'not-an-object')
  rejects(true, 'not-an-object')
  rejects([{ source: ARTIFACT_SOURCE, v: ARTIFACT_PROTOCOL, type: 'resize', height: 400 }], 'not-an-object')
  rejects([], 'not-an-object')
})

test('parseMessage never throws, whatever it is handed', () => {
  const throwing = {}
  Object.defineProperty(throwing, 'source', { get: () => { throw new Error('gotcha') }, enumerable: true })
  const circular: Record<string, unknown> = resize()
  circular.self = circular
  const inputs: unknown[] = [
    null, undefined, 0, -0, NaN, '', 'resize', true, [], [1, 2], {}, Symbol('x'), 10n,
    () => 'hi', new Date(), new Map(), new Error('nope'), Object.create(null),
    throwing, circular, resize(), titled(),
    JSON.parse('{"__proto__":{"x":1}}'),
    new Proxy({}, { get: () => { throw new Error('trap') }, ownKeys: () => { throw new Error('trap') } })
  ]
  for (const raw of inputs) {
    const r = parseMessage(raw)
    assert.equal(typeof r.ok, 'boolean')
    if (!r.ok) assert.equal(typeof r.detail, 'string')
  }
  assert.equal(parseMessage(throwing).ok, false)
})

test('somebody else\'s postMessage is a quiet rejection, ours is not', () => {
  const foreign = rejects({ type: 'webpackHotUpdate', data: {} }, 'foreign')
  assert.equal(foreign.ok, false)
  if (foreign.ok) throw new Error('unreachable')
  assert.equal(isQuiet(foreign), true)
  const mine = rejects(resize({ height: '400' }), 'bad-field')
  if (mine.ok) throw new Error('unreachable')
  assert.equal(isQuiet(mine), false)
})

// ---------------- source: names, extensions, preview ----------------

test('every kind maps to an extension, a mime type and a preview mode', () => {
  const ext: Record<ArtifactKind, string> = { html: 'html', svg: 'svg', mermaid: 'mmd', markdown: 'md', code: 'txt', json: 'json', text: 'txt' }
  const mode = { html: 'frame', svg: 'frame', mermaid: 'mermaid', markdown: 'markdown', code: 'code', json: 'code', text: 'code' }
  assert.equal(ARTIFACT_KINDS.length, 7)
  for (const kind of ARTIFACT_KINDS) {
    assert.equal(extensionFor({ kind, language: null }), ext[kind])
    assert.equal(previewMode(kind), mode[kind])
    assert.match(mimeFor(kind), /^[a-z]+\/[a-z0-9.+-]+$/)
  }
})

test('a code artifact takes its extension from the language, falling back to .txt', () => {
  assert.equal(extensionFor({ kind: 'code', language: 'python' }), 'py')
  assert.equal(extensionFor({ kind: 'code', language: ' TypeScript ' }), 'ts')
  assert.equal(extensionFor({ kind: 'code', language: 'zsh' }), 'sh')
  assert.equal(extensionFor({ kind: 'code', language: 'brainfuck' }), 'txt')
  assert.equal(extensionFor({ kind: 'code', language: 'constructor' }), 'txt')
  assert.equal(extensionFor({ kind: 'code' }), 'txt')
})

test('a fence language maps to a kind, or to nothing we can preview', () => {
  assert.equal(kindFromLanguage('mermaid'), 'mermaid')
  assert.equal(kindFromLanguage('HTML'), 'html')
  assert.equal(kindFromLanguage('md'), 'markdown')
  assert.equal(kindFromLanguage('python'), 'code')
  assert.equal(kindFromLanguage('cobol'), null)
  assert.equal(kindFromLanguage(''), null)
  assert.equal(kindFromLanguage(null), null)
  assert.equal(kindFromLanguage('toString'), null)
})

test('a title becomes a slug, and an unusable title becomes untitled', () => {
  assert.equal(slugify('Q3 Sales Chart'), 'q3-sales-chart')
  assert.equal(slugify('  Café — Résumé!  '), 'cafe-resume')
  assert.equal(slugify('../../etc/passwd'), 'etc-passwd')
  assert.equal(slugify('日本語'), '')
  assert.equal(slugify('-'.repeat(10)), '')
  assert.equal(slugify('a'.repeat(200)).length, 60)
  assert.equal(baseName('日本語'), 'untitled')
  assert.equal(baseName(''), 'untitled')
})

test('the export name joins slug and extension and steps aside for names already taken', () => {
  const chart = { title: 'Q3 Sales Chart', kind: 'html' as const, language: null }
  assert.equal(exportName(chart), 'q3-sales-chart.html')
  assert.equal(exportName(chart, ['q3-sales-chart.html']), 'q3-sales-chart-2.html')
  assert.equal(exportName(chart, ['Q3-Sales-Chart.html', 'q3-sales-chart-2.html']), 'q3-sales-chart-3.html')
  assert.equal(exportName({ title: '???', kind: 'mermaid', language: null }), 'untitled.mmd')
  assert.equal(exportName({ title: 'calc', kind: 'code', language: 'python' }), 'calc.py')
})

test('a forked revision gets a numbered title and does not stack the suffix', () => {
  assert.equal(forkTitle('Chart'), 'Chart (revised)')
  assert.equal(forkTitle('Chart', ['Chart (revised)']), 'Chart (revised 2)')
  assert.equal(forkTitle('Chart (revised)', ['Chart (revised)', 'chart (revised 2)']), 'Chart (revised 3)')
  assert.equal(forkTitle('Chart (revised 4)'), 'Chart (revised)')
  assert.equal(forkTitle('   '), 'untitled (revised)')
})

// ---------------- source: the save conflict ----------------

const attempt = (over: Partial<SaveAttempt> = {}): SaveAttempt =>
  ({ base: 3, current: 3, currentBody: 'old', incomingBody: 'new', dirty: false, ...over })

test('a clean revise applies silently', () => {
  const d = decideSave(attempt())
  assert.equal(d.choice, 'apply')
  assert.equal(d.reason, 'clean')
  assert.equal(d.message, '')
})

test('an empty revise never clobbers the stored body', () => {
  for (const incomingBody of ['', '   \n\t']) {
    const d = decideSave(attempt({ incomingBody }))
    assert.equal(d.choice, 'keep-local')
    assert.equal(d.reason, 'empty')
    assert.ok(d.message)
  }
})

test('a revise that changes nothing is a no-op, even with unsaved edits', () => {
  const d = decideSave(attempt({ incomingBody: 'old', dirty: true, current: 9 }))
  assert.equal(d.choice, 'noop')
  assert.equal(d.reason, 'identical')
  assert.equal(d.message, '')
})

test('unsaved hand edits fork instead of being overwritten', () => {
  const d = decideSave(attempt({ dirty: true }))
  assert.equal(d.choice, 'fork')
  assert.equal(d.reason, 'unsaved-edits')
  assert.ok(d.message)
})

test('a saved edit made while the revise ran also forks', () => {
  const d = decideSave(attempt({ current: 4 }))
  assert.equal(d.choice, 'fork')
  assert.equal(d.reason, 'diverged')
  assert.equal(decideSave(attempt({ current: 4, dirty: true })).reason, 'diverged')
})

test('revisions we cannot order go to the user', () => {
  for (const over of [{ base: -1 }, { current: -1 }, { base: 1.5 }, { current: NaN }]) {
    const d = decideSave(attempt(over))
    assert.equal(d.choice, 'ask')
    assert.equal(d.reason, 'unknown-base')
  }
  const rolled = decideSave(attempt({ base: 5, current: 2 }))
  assert.equal(rolled.choice, 'ask')
  assert.equal(rolled.reason, 'inconsistent')
  assert.ok(rolled.message)
})

test('a decision is a fresh object, so a caller cannot poison the table', () => {
  const first = decideSave(attempt())
  first.message = 'mutated'
  assert.equal(decideSave(attempt()).message, '')
})
