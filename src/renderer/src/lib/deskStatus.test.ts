import test from 'node:test'
import assert from 'node:assert/strict'
import { AUTONOMY } from './deskStatus'

test('the three modes keep their values, user-facing names and a one-line description', () => {
  assert.deepEqual(AUTONOMY.map((a) => [a.value, a.label]), [['plan', 'Plan first'], ['ask', 'Ask as it goes'], ['propose', 'Autonomous']])
  for (const a of AUTONOMY) assert.ok(a.hint.length > 0 && !a.hint.includes('\n'))
})
