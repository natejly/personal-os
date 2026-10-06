import test from 'node:test'
import assert from 'node:assert/strict'
import type { Memory } from '@shared/types'
import { memorySections } from './memorySections'

const mem = (id: string, o: Partial<Memory> = {}): Memory => ({ id, project_id: null, content: id, kind: 'fact', source: 'auto', pinned: 0, created_at: 1, updated_at: 1, ...o })
const ids = (ms: Memory[]): string[] => ms.map((m) => m.id)

test('profile wins over notes and log; pins come first', () => {
  const s = memorySections([
    mem('pref-expiring', { kind: 'preference', expires_at: 500 }),
    mem('pinned-note', { pinned: 1, expires_at: 500 }),
    mem('instr', { kind: 'instruction' }),
    mem('pinned-fact', { pinned: 1 }),
    mem('fact'),
  ])
  assert.deepEqual(ids(s.profile), ['pinned-note', 'pinned-fact', 'pref-expiring', 'instr'])
  assert.deepEqual(ids(s.notes), [])
  assert.deepEqual(ids(s.log), ['fact'])
})

test('notes are the remaining expiring rows, soonest expiry first', () => {
  const s = memorySections([mem('late', { expires_at: 900 }), mem('soon', { expires_at: 100 }), mem('plain')])
  assert.deepEqual(ids(s.notes), ['soon', 'late'])
  assert.deepEqual(ids(s.log), ['plain'])
})

test('log is newest first by valid_from, falling back to created_at', () => {
  const s = memorySections([mem('old', { created_at: 10 }), mem('dated', { created_at: 1, valid_from: 50 }), mem('new', { created_at: 30 })])
  assert.deepEqual(ids(s.log), ['dated', 'new', 'old'])
})
