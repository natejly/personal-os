import test from 'node:test'
import assert from 'node:assert/strict'
import type { ToolEvent } from '@shared/types'
import { trailFromEvents } from './researchTrail'

const ev = (name: string, research?: ToolEvent['research']): ToolEvent =>
  ({ id: name, name, arguments: {}, result_preview: '', duration_ms: 1, error: null, research })

test('no deep_research event, or one without a trail, gives nothing', () => {
  assert.equal(trailFromEvents(undefined), null)
  assert.equal(trailFromEvents([ev('web_search'), ev('deep_research')]), null)
})

test('the trail is shaped from the last deep_research event', () => {
  const early = { plan: ['old'], steps: [], dropped: 0, sources_considered: [] }
  const late = {
    plan: ['a', 'b', 'c'],
    steps: [
      { q: 'a', status: 'done', sources: [{ url: 'https://x.test/1', title: 'One' }, { url: 'https://x.test/2', title: 'Two' }], claims: 3 },
      { q: 'b', status: 'done', sources: [{ url: 'https://x.test/1', title: 'One' }], claims: 1 },
      { q: 'c', status: 'failed', sources: [], claims: 0 }
    ],
    dropped: 2,
    sources_considered: [{ url: 'https://x.test/1', title: 'One', n: 1 }, { url: 'https://x.test/2', title: 'Two', n: 2 }]
  }
  const t = trailFromEvents([ev('deep_research', early), ev('web_search'), ev('deep_research', late)])!
  assert.deepEqual(t.plan, ['a', 'b', 'c'])
  assert.deepEqual(t.steps.map((s) => s.label), ['2 sources', '1 source', 'no sourced claims'])
  assert.equal(t.sources.length, 2)
  assert.equal(t.dropped, 2)
})
