import test from 'node:test'
import assert from 'node:assert/strict'
import { ApiError } from './apiError'
import { confirmDiff, partialDimensions, pickedId, runtimeChip, runtimeReasons, slotNoun, sourceOn, sourceShown, sourceText, toggleSource } from './runtime'
import type { RuntimeResolution } from '@shared/types'

const res = (over: Partial<RuntimeResolution>): RuntimeResolution => ({
  slot: 'sandbox.backend', provider: 'acme/box', extra: [], source: 'project', reason: 'Using Box (sandbox backend) because this project selects it.',
  default: false, fell_back_from: null, refused: false, locality: null, enforcement: null, ...over
})

test('the footer chip names each non-default slot and its provider, and nothing when there is none', () => {
  assert.equal(runtimeChip(undefined), '')
  assert.equal(runtimeChip([]), '')
  assert.equal(runtimeChip([res({}), res({ slot: 'tools.source', provider: 'core/builtin', extra: ['acme/a', 'acme/b'] })]), 'Sandbox backend: box · Tool source: a, b')
  assert.equal(runtimeReasons([res({}), res({ reason: 'Second.' })]), 'Using Box (sandbox backend) because this project selects it.\nSecond.')
  assert.equal(slotNoun('agent.loop'), 'Agent loop')
})

test('a source reads as where the choice was made', () => {
  assert.deepEqual(['chat', 'project', 'global', 'default', 'profile:lean'].map(sourceText), ['This chat', 'This project', 'Settings', 'Default', 'Profile lean'])
})

test('partial dimensions are listed per mode, and a backend that enforces everything lists none', () => {
  assert.deepEqual(partialDimensions({ enforcement: { 'workspace-write': { fs_write: 'full', network: 'partial' }, 'read-only': { fs_write: 'partial' } } }),
    [{ mode: 'workspace-write', dimension: 'network' }, { mode: 'read-only', dimension: 'fs_write' }])
  assert.deepEqual(partialDimensions({ enforcement: { 'workspace-write': { fs_write: 'full' } } }), [])
  assert.deepEqual(partialDimensions({ enforcement: null }), [])
})

test('tool sources switch on and off per scope with enable and disable lists', () => {
  assert.equal(pickedId('acme/x'), 'acme/x')
  assert.equal(pickedId({ enable: ['a'] }), '')
  assert.equal(sourceOn(undefined, 'a'), false)
  const on = toggleSource(undefined, 'a', true)
  assert.deepEqual(on, { enable: ['a'], disable: [] })
  assert.equal(sourceOn(on, 'a'), true)
  const off = toggleSource(on, 'a', false)
  assert.deepEqual(off, { enable: [], disable: ['a'] })
  assert.equal(sourceOn(off, 'a'), false)
  assert.deepEqual(toggleSource(toggleSource(off, 'b', true), 'a', true), { enable: ['b', 'a'], disable: [] })
})

test('a narrower scope shows a tool source that a wider one switched on', () => {
  const inForce = { extra: ['a'] }
  assert.equal(sourceShown('chat', undefined, inForce, 'a'), true)
  assert.equal(sourceShown('chat', { enable: [], disable: ['a'] }, { extra: [] }, 'a'), false)
  assert.equal(sourceShown('global', undefined, inForce, 'a'), false)
})

test('a 409 with a diff asks for a confirmation; any other error does not', () => {
  const asked = new ApiError('Request failed: Conflict.', { status: 409, body: { detail: { needs_confirm: true, diff: ['Box enforces less: network: partial instead of full'] } } })
  assert.deepEqual(confirmDiff(asked), ['Box enforces less: network: partial instead of full'])
  assert.equal(confirmDiff(new ApiError('x', { status: 409, body: { detail: 'plain' } })), null)
  assert.equal(confirmDiff(new ApiError('x', { status: 422, body: { detail: { needs_confirm: true, diff: [] } } })), null)
  assert.equal(confirmDiff(new Error('x')), null)
})
