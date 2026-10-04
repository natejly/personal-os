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

type El = ReactElement<{ children?: unknown; type?: string; onChange?: (e: unknown) => void; checked?: boolean }>
const flat = (n: unknown): El[] => {
  if (Array.isArray(n)) return n.flatMap(flat)
  if (!n || typeof n !== 'object' || !('props' in n)) return []
  const el = n as El
  return [el, ...flat(el.props.children)]
}
const text = (n: unknown): string => flat(n).map((e) => [e.props.children].flat().filter((c) => typeof c === 'string').join('')).join(' ')

test('toggling Include my Docs patches useDocsInContext', () => {
  const patches: Partial<Settings>[] = []
  const tree = AdvancedRetrieval({ draft: {} as Settings, patch: (p) => patches.push(p), models: [] })
  const row = flat(tree).find((e) => e.type === 'label' && text(e.props.children).includes('Include my Docs'))
  const box = flat(row?.props.children).find((e) => e.type === 'input')
  assert.equal(box?.props.checked, true)
  box?.props.onChange?.({ target: { checked: false } })
  assert.deepEqual(patches, [{ useDocsInContext: false }])
})

test('fetch cache is entered in minutes and stored in seconds', () => {
  const patches: Partial<Settings>[] = []
  const tree = AdvancedRetrieval({ draft: { fetchCacheSeconds: 600 } as Settings, patch: (p) => patches.push(p), models: [] })
  const row = flat(tree).find((e) => e.type === 'label' && text(e.props.children).includes('Reuse fetched web pages'))
  const box = flat(row?.props.children).find((e) => e.type === 'input') as ReactElement<{ value: number; onChange: (e: unknown) => void }>
  assert.equal(box.props.value, 10)
  box.props.onChange({ target: { value: '5' } })
  assert.deepEqual(patches, [{ fetchCacheSeconds: 300 }])
})
