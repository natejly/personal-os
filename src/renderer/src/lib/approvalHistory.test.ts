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

test('automatic review and allow-all rows are labelled plainly', () => {
  assert.equal(decisionLabel({ decision: 'auto', scope: 'auto-review' }), 'Reviewed automatically: allowed')
  assert.equal(decisionLabel({ decision: 'review-ask', scope: 'auto-review' }), 'Reviewed automatically: sent to you')
  assert.equal(decisionLabel({ decision: 'deny', scope: 'auto-review' }), 'Reviewed automatically: denied')
  assert.equal(decisionLabel({ decision: 'auto', scope: 'allow-all' }), 'Allowed (allow-all mode)')
  assert.equal(reviewLine({ verdict: 'allow', reason: 'fine', confidence: 'high' }), 'Reviewed: allowed — fine (high confidence)')
})
