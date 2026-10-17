import test from 'node:test'
import assert from 'node:assert/strict'
import type { PermissionPreset, PresetDiff } from '@shared/types'
import { diffRows, heldText, keyLabel, presetOrigin, scopeId, scopesFor, valueText } from './presets'

const diff: PresetDiff = {
  changes: {
    'tools.shell_run': { old: 'ask', new: 'off', loosens: false },
    permissionMode: { old: 'manual', new: 'allow_all', loosens: true },
    alwaysAsk: { old: ['gmail_send', 'run_shortcut'], new: ['gmail_send'], loosens: true },
    shellNetwork: { old: false, new: true, loosens: true }
  },
  not_applied: [], pending: [], loosened: ['permissionMode', 'alwaysAsk', 'shellNetwork'], diff_hash: 'abc'
}

test('a project or a chat can be chosen only while one is open', () => {
  assert.deepEqual(scopesFor(null, null), ['global'])
  assert.deepEqual(scopesFor('p1', null), ['global', 'project'])
  assert.deepEqual(scopesFor('p1', 'c1'), ['global', 'project', 'chat'])
  assert.equal(scopeId('global', 'p1', 'c1'), '')
  assert.equal(scopeId('project', 'p1', 'c1'), 'p1')
  assert.equal(scopeId('chat', 'p1', 'c1'), 'c1')
})

test('the rows of a diff put what allows more first', () => {
  const rows = diffRows(diff)
  assert.deepEqual(rows.map((r) => r.key), ['alwaysAsk', 'permissionMode', 'shellNetwork', 'tools.shell_run'])
  assert.deepEqual(rows.map((r) => r.loosens), [true, true, true, false])
})

test('values and keys read as words', () => {
  assert.equal(keyLabel('tools.shell_run'), 'Tool: shell_run')
  assert.equal(keyLabel('permissionMode'), 'Permission mode')
  assert.equal(keyLabel('somethingNew'), 'somethingNew')
  assert.equal(valueText('permissionMode', 'allow_all'), 'Allow everything')
  assert.equal(valueText('alwaysAsk', []), 'none')
  assert.equal(valueText('alwaysAsk', ['a', 'b']), 'a, b')
  assert.equal(valueText('shellNetwork', true), 'on')
  assert.equal(valueText('permissionRules', { allow: [], deny: ['Bash(rm *)'] }), 'allow: none; deny: Bash(rm *)')
})

test('a preset says where it comes from', () => {
  const p = (source: PermissionPreset['source'], owner: string | null = null): PermissionPreset =>
    ({ id: 'x', name: 'x', description: '', source, owner, readonly: source !== 'user', body: { name: 'x' } })
  assert.equal(presetOrigin(p('builtin')), 'Built in')
  assert.equal(presetOrigin(p('profile', 'Lean')), 'From Lean')
  assert.equal(presetOrigin(p('import')), 'Imported')
  assert.equal(presetOrigin(p('user')), 'Yours')
})

test('a held default reads as the setting, what it is now and what it would become', () => {
  assert.equal(heldText({ key: 'permissions', was: 'manual', set: 'allow_all' }), 'Permission mode from Manual to Allow everything')
  assert.equal(heldText({ key: 'perm:alwaysAsk', was: ['a', 'b'], set: ['a'] }), 'Always ask first from a, b to a')
})
