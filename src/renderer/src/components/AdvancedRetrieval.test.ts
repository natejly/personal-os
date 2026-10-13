import test from 'node:test'
import assert from 'node:assert/strict'
import type { ReactElement } from 'react'
import type { Settings } from '@shared/types'
import AdvancedRetrieval, { rebuildIndex } from './AdvancedRetrieval'

test('Rebuild index re-chunks, then backfills', async () => {
  const calls: string[] = []
  const r = await rebuildIndex({
    reindexAll: async () => { calls.push('reindex'); return { chunks: 3 } },
    embedBackfill: async () => { calls.push('backfill'); return { embedded: 3, remaining: 0 } }
  })
  assert.deepEqual(calls, ['reindex', 'backfill'])
  assert.equal(r.embedded, 3)
})

type El = ReactElement<{ children?: unknown; title?: string; onChange?: (on: boolean) => void; checked?: boolean }>
const flat = (n: unknown): El[] => {
  if (Array.isArray(n)) return n.flatMap(flat)
  if (!n || typeof n !== 'object' || !('props' in n)) return []
  const el = n as El
  return [el, ...flat(el.props.children)]
}
test('toggling Search my Docs patches useDocsInContext', () => {
  const patches: Partial<Settings>[] = []
  const tree = AdvancedRetrieval({ draft: {} as Settings, patch: (p) => patches.push(p) })
  const row = flat(tree).find((e) => e.props.title?.includes('Search my Docs'))
  assert.equal(row?.props.checked, true)
  row?.props.onChange?.(false)
  assert.deepEqual(patches, [{ useDocsInContext: false }])
})
