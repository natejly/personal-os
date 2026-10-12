import test from 'node:test'
import assert from 'node:assert/strict'
import { statusWord } from './statusWord'
import { fmtCost, usageLine } from './chatMeta'
import { detailText } from './apiError'

test('status codes read as words', () => {
  assert.equal(statusWord('awaiting_approval'), 'Needs approval')
  assert.equal(statusWord('in_progress'), 'In progress')
  assert.equal(statusWord('needs_reauth'), 'Needs sign-in')
  assert.equal(statusWord('some_new_state'), 'Some new state')
  assert.equal(statusWord(undefined), '')
})

test('a tiny cost is not shown as $0.0000', () => {
  assert.equal(fmtCost(0.00002), '<$0.0001')
  assert.equal(fmtCost(0.0042), '$0.0042')
  assert.equal(fmtCost(0), '$0')
})

test('the usage line says calls without a price, not a bare count', () => {
  const totals = { calls: 10, tokens: 1200, cost: 0.5, unpriced: 5 }
  assert.match(usageLine({ totals } as never) ?? '', /5 calls without a price/)
})

test('request failures never show JSON or a bare status line', () => {
  assert.equal(detailText('nope', 409, 'Conflict'), 'nope')
  assert.equal(detailText([{ loc: ['body', 'title'], msg: 'Field required', type: 'missing' }], 422, 'Unprocessable Entity'), 'title: Field required')
  assert.equal(detailText(undefined, 500, 'Internal Server Error'), 'Something went wrong in the backend. Try again.')
  assert.equal(detailText(undefined, 404, 'Not Found'), 'Not found.')
})
