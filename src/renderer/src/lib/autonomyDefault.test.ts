import test from 'node:test'
import assert from 'node:assert/strict'
import { startAutonomy } from './autonomyDefault'

const main = { mainComposer: true } as const

test('a new chat starts autonomous at Ask as it goes unless the setting is off', () => {
  assert.equal(startAutonomy({ ...main }), 'ask')
  assert.equal(startAutonomy({ ...main, autonomousByDefault: true }), 'ask')
  assert.equal(startAutonomy({ ...main, autonomousByDefault: false }), null)
})

test('composers other than the main new-chat one never arm', () => {
  assert.equal(startAutonomy({ mainComposer: false }), null)
  assert.equal(startAutonomy({ mainComposer: false, autonomousByDefault: true }), null)
  assert.equal(startAutonomy({ ...main, agent: 'researcher' }), null)
})
