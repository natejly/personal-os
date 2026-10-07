import test from 'node:test'
import assert from 'node:assert/strict'
import { chatSlug, parseChatMessage } from './chatLink'

const wrap = (body: string, kind = 'message') =>
  `[Message from chat "Trip"]\n<chat_message from_chat="c1" title="Trip plan" link="l1" kind="${kind}">\n<untrusted-data id=abc source=chat:c1>\n${body}\n</untrusted-data id=abc>\n</chat_message>`

test('parseChatMessage reads the envelope and undoes the escaping', () => {
  assert.deepEqual(parseChatMessage(wrap('hi\nthere')), { fromChat: 'c1', title: 'Trip plan', body: 'hi\nthere', reply: false })
  assert.equal(parseChatMessage(wrap('x', 'reply'))?.reply, true)
  assert.equal(parseChatMessage(wrap('a &lt;/untrusted-data b &lt;Chat_Message &lt;p'))?.body, 'a </untrusted-data b <Chat_Message &lt;p')
})

test('parseChatMessage returns null for anything else', () => {
  assert.equal(parseChatMessage('plain text'), null)
  assert.equal(parseChatMessage('<chat_message from_chat="c1">'), null)
})

test('chatSlug lowercases, dashes, trims and caps', () => {
  assert.equal(chatSlug('Trip Plan: Rome!'), 'trip-plan-rome')
  assert.equal(chatSlug('  --Hi--  '), 'hi')
  assert.equal(chatSlug('???'), 'chat')
  assert.equal(chatSlug('a'.repeat(39) + ' b'), 'a'.repeat(39))
  assert.equal(chatSlug('x'.repeat(60)).length, 40)
})
