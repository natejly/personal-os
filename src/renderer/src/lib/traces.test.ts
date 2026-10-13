import test from 'node:test'
import assert from 'node:assert/strict'
import type { Message, Span, ToolEvent } from '@shared/types'
import { groupTurn } from './traces'

const sp = (id: string, kind: Span['kind'], start: number, meta: Record<string, unknown> = {}, parent_id?: string, name = id): Span => ({ id, kind, name, start, end: start + 5, meta, error: null, parent_id })
const ev = (name: string, result_preview: string): ToolEvent => ({ id: name + result_preview, name, arguments: {}, result_preview, duration_ms: 1, error: null })

test('groupTurn nests tools under their model round and pairs results in call order', () => {
  const m = { id: 'm', model: 'x', reasoning: 'thought', trace: [
    sp('c', 'context', 1), sp('l1', 'llm', 2, { round: 1 }), sp('t1', 'tool', 3, { round: 1 }, 'l1', 'web_search'),
    sp('t2', 'tool', 4, { round: 1 }, 'l1', 'web_search'), sp('l2', 'llm', 9, { round: 2 })
  ], tool_events: [ev('web_search', 'a'), ev('web_search', 'b')] } as unknown as Message
  const t = groupTurn(m)
  assert.deepEqual(t.rounds.map((r) => [r.round, r.tools.map((x) => x.event?.result_preview)]), [[1, ['a', 'b']], [2, []]])
  assert.equal(t.other.length, 1)
  assert.equal(t.thinking, 'thought')
})
