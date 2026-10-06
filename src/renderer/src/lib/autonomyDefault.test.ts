import test from 'node:test'
import assert from 'node:assert/strict'
import { startAutonomy } from './autonomyDefault'

const main = { draft: null, mainComposer: true } as const

test('a new chat starts autonomous at Ask as it goes unless the setting is off', () => {
  assert.equal(startAutonomy({ ...main }), 'ask')
  assert.equal(startAutonomy({ ...main, autonomousByDefault: true }), 'ask')
  assert.equal(startAutonomy({ ...main, autonomousByDefault: false }), null)
})

test('the draft choice beats the setting either way', () => {
  assert.equal(startAutonomy({ ...main, draft: 'off' }), null)
  assert.equal(startAutonomy({ ...main, draft: 'plan' }), 'plan')
  assert.equal(startAutonomy({ ...main, draft: 'propose', autonomousByDefault: false }), 'propose')
})

test('composers other than the main new-chat one never arm', () => {
  assert.equal(startAutonomy({ draft: null, mainComposer: false }), null)
  assert.equal(startAutonomy({ draft: 'ask', mainComposer: false }), null)
  assert.equal(startAutonomy({ ...main, agent: 'researcher' }), null)
  assert.equal(startAutonomy({ ...main, private: true }), null)
})
