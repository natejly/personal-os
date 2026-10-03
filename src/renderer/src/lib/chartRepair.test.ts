import test from 'node:test'
import assert from 'node:assert/strict'
import { parseJsonLoose } from './chartRepair'
import { parseSpec } from '../components/ChartBlock'

test('trailing commas and comments are repaired', () => {
  assert.deepEqual(parseJsonLoose('{"a":[1,2,],// c\n"b":"x,]",}'), { a: [1, 2], b: 'x,]' })
})
test('smart quotes and fences', () => {
  assert.deepEqual(parseJsonLoose('```json\n{“a”: 1}\n```'), { a: 1 })
})
test('valid JSON is untouched and garbage still throws', () => {
  assert.deepEqual(parseJsonLoose('{"a":"it\'s, ]"}'), { a: "it's, ]" })
  assert.throws(() => parseJsonLoose('{nope'))
})
test('parseSpec accepts a spec with a trailing comma and numeric strings', () => {
  const s = parseSpec('{"type":"bar","data":[{"n":"a","v":"3"},{"n":"b","v":"4"},]}')
  assert.equal(s.data[0].v, 3)
})
