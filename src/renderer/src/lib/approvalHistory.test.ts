import assert from 'node:assert/strict'
import { test } from 'node:test'
import { decisionLabel, reviewLine, reviewTitle } from './approvalHistory'

test('decision labels carry their scope', () => {
  assert.equal(decisionLabel({ decision: 'allow_once', scope: 'once' }), 'Allowed once')
  assert.equal(decisionLabel({ decision: 'always', scope: 'global' }), 'Always (everywhere)')
  assert.equal(decisionLabel({ decision: 'always', scope: 'rule' }), 'Always (by rule)')
  assert.equal(decisionLabel({ decision: 'deny', scope: null }), 'Denied')
  assert.equal(decisionLabel({ decision: 'mystery', scope: null }), 'mystery')
})

test('review lines say what the reviewer decided and why', () => {
  assert.equal(reviewLine(null), null)
  assert.equal(reviewLine({ verdict: 'allow', reason: 'matches the request' }), 'Reviewed: allowed — matches the request')
  assert.equal(reviewLine({ verdict: 'ask', reason: '' }), 'Reviewed: asked you')
  assert.equal(reviewLine({ verdict: 'fail', reason: 'no table' }), 'Reviewed: fail — no table')
  assert.equal(reviewTitle({ model: 'm', ms: 120 }), 'm · 120 ms')
  assert.equal(reviewTitle({ model: null }), undefined)
})
