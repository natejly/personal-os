import test from 'node:test'
import assert from 'node:assert/strict'
import { tapeSpans, tapeTimings, workerRows } from './workerTranscript'

test('a worker history becomes chat rows: labelled user rows, one reply per turn with its tool results', () => {
  const rows = workerRows([
    { role: 'system', content: 'sys' },
    { role: 'user', content: 'Find flights', from: 'agent' },
    { role: 'assistant', content: 'Looking.', tool_calls: [{ id: 'c1', function: { name: 'web_search', arguments: '{"q":"flights"}' } }] },
    { role: 'tool', content: 'three results', tool_call_id: 'c1' },
    { role: 'assistant', content: null, tool_calls: [{ id: 'c2', function: { name: 'fetch_url', arguments: 'not json' } }] },
    { role: 'user', content: 'cheaper please', from: 'user' },
    { role: 'assistant', content: 'Done.' }
  ], 'conv', true)
  assert.deepEqual(rows.map((r) => [r.message.role, r.from]), [['user', 'agent'], ['assistant', undefined], ['user', 'user'], ['assistant', undefined]])
  const reply = rows[1].message
  assert.equal(reply.content, 'Looking.')
  assert.equal(reply.conversation_id, 'conv')
  assert.deepEqual(reply.tool_events!.map((e) => [e.name, e.result_preview, e.pending]), [['web_search', 'three results', false], ['fetch_url', '', true]])
  assert.deepEqual(reply.tool_events![0].arguments, { q: 'flights' })
  assert.deepEqual(reply.tool_events![1].arguments, {})
  assert.equal(workerRows([{ role: 'assistant', content: null, tool_calls: [{ id: 'x', function: { name: 't', arguments: '{}' } }] }], 'c', false)[0].message.tool_events![0].pending, false)
})

test("a worker's NO_REPLY is not shown in its transcript", () => {
  const rows = workerRows([
    { role: 'user', content: 'Check the build', from: 'agent' },
    { role: 'assistant', content: 'NO_REPLY' }
  ], 'conv', false)
  assert.equal(rows[1].message.content, '')
  const mixed = workerRows([{ role: 'assistant', content: 'Build is green.\n\nNO_REPLY' }], 'conv', false)
  assert.equal(mixed[0].message.content, 'Build is green.')
})

test('tool rows carry measured durations and approval waits from the tape', () => {
  const tape = [
    { seq: 1, event: 'tool_call', data: { id: 'c1', name: 'shell_run', arguments: { command: 'ls' } } },
    { seq: 2, event: 'tool_result', data: { id: 'c1', name: 'shell_run', duration_ms: 4200, wait_ms: 3000 } }
  ]
  const rows = workerRows([
    { role: 'assistant', content: null, tool_calls: [{ id: 'c1', function: { name: 'shell_run', arguments: '{}' } }] },
    { role: 'tool', content: 'ok', tool_call_id: 'c1' }
  ], 'conv', false, tapeTimings(tape))
  const ev = rows[0].message.tool_events![0]
  assert.equal(ev.duration_ms, 4200)
  assert.equal(ev.wait_ms, 3000)
})

test('the tape becomes spans: one per model round, tools under the round that issued them', () => {
  const span = (end: number | null) => ({ id: 'l1', kind: 'llm', name: 'm', start: 1000, end, meta: { effort: 'high' }, error: null })
  const spans = tapeSpans([
    { seq: 1, event: 'span', data: { span: span(null) } },
    { seq: 2, event: 'span', data: { span: span(1500) } },
    { seq: 3, event: 'tool_call', data: { id: 'c1', name: 'web_search', arguments: { query: 'x' } } },
    { seq: 4, event: 'tool_result', data: { id: 'c1', name: 'web_search', duration_ms: 700, wait_ms: 0 } }
  ])
  assert.deepEqual(spans.map((s) => [s.id, s.kind, s.start, s.end, s.parent_id]), [['l1', 'llm', 1000, 1500, undefined], ['t-c1', 'tool', 1500, 2200, 'l1']])
})
