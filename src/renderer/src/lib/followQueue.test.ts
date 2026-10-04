import { test } from 'node:test'
import assert from 'node:assert/strict'
import { EMPTY_QUEUE, enqueue, enterAction, nextOnDone, parseQueue, removeQueued, requeueFront, serializeQueue } from './followQueue'

const two = enqueue(enqueue(EMPTY_QUEUE, 'first', 'a'), 'second', 'b')

test('enqueue keeps order and ignores blank text', () => {
  assert.deepEqual(two.items.map((i) => i.text), ['first', 'second'])
  assert.equal(enqueue(two, '   ', 'c'), two)
})

test('a steer segment done does not dequeue', () => {
  assert.deepEqual(nextOnDone(two, { segment: true }), { queue: two, send: null })
})

test('a final done dequeues exactly one item', () => {
  const r = nextOnDone(two, {})
  assert.equal(r.send?.text, 'first')
  assert.deepEqual(r.queue.items.map((i) => i.id), ['b'])
  const r2 = nextOnDone(r.queue, {})
  assert.equal(r2.send?.text, 'second')
  assert.equal(nextOnDone(r2.queue, {}).send, null)
})

test('an error or a stop pauses instead of sending, and a paused queue stays put', () => {
  for (const done of [{ error: 'boom' }, { stopped: true }]) {
    const r = nextOnDone(two, done)
    assert.equal(r.send, null)
    assert.equal(r.queue.paused, true)
    assert.equal(r.queue.items.length, 2)
    assert.equal(nextOnDone(r.queue, {}).send, null)
  }
})

test('a refused send goes back in front, paused', () => {
  const { queue, send } = nextOnDone(two, {})
  assert.ok(send)
  const back = requeueFront(queue, send)
  assert.deepEqual(back.items.map((i) => i.id), ['a', 'b'])
  assert.equal(back.paused, true)
})

test('removing the last item clears the pause, and an empty queue serializes to nothing', () => {
  const q = removeQueued(removeQueued({ ...two, paused: true }, 'a'), 'b')
  assert.deepEqual(q, EMPTY_QUEUE)
  assert.equal(serializeQueue(q), '')
  assert.deepEqual(parseQueue(serializeQueue(two)), two)
  assert.deepEqual(parseQueue('not json'), EMPTY_QUEUE)
})

test('Enter queues while busy, even with a card open; mod+Enter steers, asking first over a card', () => {
  assert.equal(enterAction({ busy: false, mod: false, cardPending: false }), 'send')
  assert.equal(enterAction({ busy: false, mod: true, cardPending: true }), 'send')
  assert.equal(enterAction({ busy: true, mod: false, cardPending: false }), 'queue')
  assert.equal(enterAction({ busy: true, mod: false, cardPending: true }), 'queue')
  assert.equal(enterAction({ busy: true, mod: true, cardPending: false }), 'steer')
  assert.equal(enterAction({ busy: true, mod: true, cardPending: true }), 'confirm-steer')
  // A desk's run never declines a card on a steer, so there is nothing to confirm.
  assert.equal(enterAction({ busy: true, mod: true, cardPending: true, desk: true }), 'steer')
})
