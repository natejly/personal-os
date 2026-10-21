import test from 'node:test'
import assert from 'node:assert/strict'
import { scopeLabel, sourceText, valueText } from './settingSource'

test('a scope reads as a chip label', () => {
  assert.deepEqual(['global', 'project', 'chat'].map(scopeLabel), ['Global', 'Project', 'Chat'])
})

test('a source reads as a phrase after "Set by"', () => {
  assert.deepEqual(
    ['default', 'user', 'project', 'chat', 'ext:acme', 'other'].map(sourceText),
    ['the built-in default', 'your settings', 'this project', 'this chat', 'the acme extension', 'other']
  )
})

test('values show short', () => {
  assert.equal(valueText(5), '5')
  assert.equal(valueText('on'), 'on')
  assert.equal(valueText(undefined), 'undefined')
  assert.equal(valueText('x'.repeat(80)).length, 63)
})
