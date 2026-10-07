import test from 'node:test'
import assert from 'node:assert/strict'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { ReplyAttachments, isImageAttachment } from './Message'
import { attachedNames, parseResult } from '../lib/toolResult'
import { TOOL_CARDS } from './toolcards/registry'
import './toolcards'
import type { ToolEvent } from '@shared/types'

const png = { id: 'd1', name: 'screen.png', mime: 'image/png', size: 10 }
const pdf = { id: 'd2', name: 'report.pdf', mime: 'application/pdf', size: 20 }

test('an assistant image attachment renders as an <img> thumbnail and a PDF as a chip', () => {
  const html = renderToStaticMarkup(createElement(ReplyAttachments, { files: [png, pdf] }))
  assert.match(html, /<img[^>]*alt="screen\.png"/)
  assert.match(html, /class="reply-image"/)
  assert.match(html, /class="file-chip"[^>]*>.*report\.pdf/)
  assert.doesNotMatch(html, /<img[^>]*report\.pdf/)
})

test('no attachments renders nothing, and svg is a file rather than an image', () => {
  assert.equal(renderToStaticMarkup(createElement(ReplyAttachments, { files: null })), '')
  assert.equal(isImageAttachment({ ...png, mime: 'image/svg+xml' }), false)
  assert.equal(isImageAttachment(png), true)
})

const sendFiles = (result: unknown): ToolEvent => ({
  id: 't1', name: 'send_files', arguments: { files: ['d1'], text: 'here' }, result_preview: JSON.stringify(result), duration_ms: 5,
  error: null, pending: false, images: [{ name: 'screen.png', mime: 'image/png', bytes: 3, data: 'data:image/png;base64,AAAA' }]
} as unknown as ToolEvent)

const card = (e: ToolEvent): string => renderToStaticMarkup(createElement(TOOL_CARDS[e.name], { event: e, pending: false, decide: async () => undefined }))

test('the send_files card shows the phone badge only when it went to Telegram', () => {
  const att = [png]
  const phone = card(sendFiles({ attached: att, delivered: 'telegram', text: 'here' }))
  assert.match(phone, /Sent to your phone/)
  assert.match(phone, /class="tool-images"/)
  assert.match(phone, /screen\.png/)
  assert.doesNotMatch(card(sendFiles({ attached: att, delivered: 'app', text: 'here' })), /Sent to your phone/)
})

test('the screenshot card shows the target, the app and the picture', () => {
  const e = { ...sendFiles({ saved: [{ doc_id: 'd1', path: '/x', width: 800, height: 600 }] }), name: 'screenshot', arguments: { target: 'window', app: 'Safari' } } as ToolEvent
  const html = card(e)
  assert.match(html, /Safari/)
  assert.match(html, /800 × 600/)
  assert.match(html, /<img/)
})

test('attachedNames reads the attached list and a cut preview still yields the delivery', () => {
  assert.deepEqual(attachedNames(parseResult(JSON.stringify({ attached: [png, pdf] })).data), ['screen.png', 'report.pdf'])
  assert.deepEqual(attachedNames(null), [])
  assert.equal(parseResult('{"delivered": "telegram", "attached": [{"id": "d1", "na').data?.delivered, 'telegram')
})
