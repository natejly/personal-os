import test from 'node:test'
import assert from 'node:assert/strict'
import { diffLines, diffStat, hunks, toUnified, wordDiff } from './diff'

const render = (before: string, after: string): string =>
  diffLines(before, after)
    .map((l) => (l.op === 'add' ? '+' : l.op === 'del' ? '-' : ' ') + l.text)
    .join('\n')

test('identical text produces only unchanged lines and no hunks', () => {
  const lines = diffLines('a\nb\nc', 'a\nb\nc')
  assert.ok(lines.every((l) => l.op === 'same'))
  assert.deepEqual(diffStat(lines), { added: 0, removed: 0, changed: 0 })
  assert.deepEqual(hunks(lines), [])
})

test('a pure insertion in the middle is reported as added lines only', () => {
  assert.equal(render('a\nc', 'a\nb\nc'), ' a\n+b\n c')
  assert.deepEqual(diffStat(diffLines('a\nc', 'a\nb\nc')), { added: 1, removed: 0, changed: 0 })
})

test('a pure deletion is reported as removed lines only', () => {
  assert.equal(render('a\nb\nc', 'a\nc'), ' a\n-b\n c')
  assert.deepEqual(diffStat(diffLines('a\nb\nc', 'a\nc')), { added: 0, removed: 1, changed: 0 })
})

test('line numbers track both sides independently', () => {
  const lines = diffLines('a\nb\nc', 'a\nx\ny\nc')
  const del = lines.find((l) => l.op === 'del')!
  const adds = lines.filter((l) => l.op === 'add')
  assert.equal(del.oldNo, 2)
  assert.equal(del.newNo, null)
  assert.deepEqual(adds.map((l) => l.newNo), [2, 3])
  assert.ok(adds.every((l) => l.oldNo === null))
  // the trailing shared line keeps its place on each side
  const last = lines[lines.length - 1]
  assert.deepEqual([last.oldNo, last.newNo], [3, 4])
})

test('appending to the end does not re-diff the whole document', () => {
  const before = 'one\ntwo\nthree'
  assert.equal(render(before, before + '\nfour'), ' one\n two\n three\n+four')
})

test('a reworded line is paired and refined to the words that moved', () => {
  const lines = diffLines('The data suggests the effect is large.', 'The data show the effect is small.')
  const del = lines.find((l) => l.op === 'del')!
  const add = lines.find((l) => l.op === 'add')!
  assert.ok(del.parts, 'the deletion should carry word parts')
  assert.ok(add.parts, 'the addition should carry word parts')
  // shared words stay unmarked, so the eye lands on the real change
  assert.ok(del.parts!.some((p) => !p.changed && p.text.includes('The data')))
  assert.ok(add.parts!.some((p) => p.changed && p.text.includes('show')))
  assert.ok(del.parts!.some((p) => p.changed && p.text.includes('suggests')))
  // reconstructing the parts gives the original lines back
  assert.equal(del.parts!.map((p) => p.text).join(''), 'The data suggests the effect is large.')
  assert.equal(add.parts!.map((p) => p.text).join(''), 'The data show the effect is small.')
  assert.deepEqual(diffStat(lines), { added: 1, removed: 1, changed: 1 })
})

test('an unrelated replacement is not refined into a rewording', () => {
  const lines = diffLines('Chapter on hydrology.', 'Entirely different subject matter here.')
  assert.ok(lines.every((l) => !l.parts), 'dissimilar lines should not be word-paired')
})

test('wordDiff marks an insertion without touching the surrounding words', () => {
  const { before, after } = wordDiff('a c', 'a b c')
  assert.equal(before.filter((p) => p.changed).length, 0)
  assert.ok(after.some((p) => p.changed && p.text.includes('b')))
  assert.equal(after.map((p) => p.text).join(''), 'a b c')
})

test('LaTeX and markdown survive a word diff intact', () => {
  const { after } = wordDiff('Then $E = mc^2$ applies.', 'Then $E = \\gamma mc^2$ applies.')
  assert.equal(after.map((p) => p.text).join(''), 'Then $E = \\gamma mc^2$ applies.')
  assert.ok(after.some((p) => p.changed && p.text.includes('gamma')))
})

test('hunks collapse untouched regions but keep context around each change', () => {
  const before = Array.from({ length: 40 }, (_, i) => `line ${i + 1}`).join('\n')
  const after = before.replace('line 20', 'line 20 edited')
  const hs = hunks(diffLines(before, after), 3)
  assert.equal(hs.length, 1, 'one edit is one hunk')
  // 3 lines of context either side, plus the del/add pair
  assert.equal(hs[0].lines.length, 8)
  assert.equal(hs[0].oldStart, 17)
})

test('two distant edits produce two separate hunks', () => {
  const before = Array.from({ length: 60 }, (_, i) => `line ${i + 1}`).join('\n')
  const after = before.replace('line 5', 'line 5 edited').replace('line 50', 'line 50 edited')
  assert.equal(hunks(diffLines(before, after), 3).length, 2)
})

test('adjacent edits merge into one hunk rather than two', () => {
  const before = Array.from({ length: 20 }, (_, i) => `line ${i + 1}`).join('\n')
  const after = before.replace('line 10', 'X').replace('line 11', 'Y')
  assert.equal(hunks(diffLines(before, after), 3).length, 1)
})

test('empty documents are handled at both ends', () => {
  // an empty doc is zero lines, so writing into one is pure insertion
  assert.deepEqual(diffStat(diffLines('', 'hello')), { added: 1, removed: 0, changed: 0 })
  assert.deepEqual(diffStat(diffLines('hello', '')), { added: 0, removed: 1, changed: 0 })
  assert.deepEqual(diffStat(diffLines('', '')), { added: 0, removed: 0, changed: 0 })
  // a genuine trailing blank line is still a line
  assert.deepEqual(diffStat(diffLines('a', 'a\n')), { added: 1, removed: 0, changed: 0 })
})

test('toUnified emits a patch header and signed lines', () => {
  const patch = toUnified('a\nb\nc', 'a\nB\nc')
  assert.match(patch, /^@@ -1,3 \+1,3 @@/m)
  assert.match(patch, /^-b$/m)
  assert.match(patch, /^\+B$/m)
  assert.equal(toUnified('same', 'same'), '')
})

test('a large rewrite still terminates and accounts for every line', () => {
  const before = Array.from({ length: 400 }, (_, i) => `para ${i} of the original manuscript`).join('\n')
  const after = Array.from({ length: 380 }, (_, i) => `para ${i} of the revised manuscript`).join('\n')
  const stat = diffStat(diffLines(before, after))
  assert.equal(stat.added, 380)
  assert.equal(stat.removed, 400)
  assert.ok(stat.changed > 0, 'similar paragraphs should be paired as rewordings')
})
