import test from 'node:test'
import assert from 'node:assert/strict'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import type { FullDesk, WorkerInfo } from '@shared/types'
import { useStore } from '../store'
import DeskPanel from './DeskPanel'

const CHAT = 'chat-1'
const worker = { id: 'w1', conversation_id: CHAT, title: 'Dig', goal: 'dig', status: 'running', now: '', queue_position: null, started_at: 1, ended_at: null, resume_of: null, resumable: false, pending_approvals: [] } as unknown as WorkerInfo

const render = (desk?: FullDesk): string => {
  // Server rendering reads the store's initial state (its server snapshot), so seed that rather than setState.
  Object.assign(useStore.getInitialState(), {
    workers: { [CHAT]: [worker] },
    plans: { [CHAT]: [{ text: 'one', status: 'in_progress' }] },
    contextOpen: false,
  })
  return renderToStaticMarkup(createElement(DeskPanel, { conversationId: CHAT, desk, onClose: () => {} }))
}

const btn = (html: string, key: string): string | undefined => html.match(new RegExp(`<button[^>]*data-testid="desk-tab-${key}"[^>]*>`))?.[0]

test('a live worker and a checklist add no Checklist or Workers tab, and Context is the active tab', () => {
  const html = render()
  assert.equal(btn(html, 'checklist'), undefined)
  assert.equal(btn(html, 'workers'), undefined)
  assert.match(btn(html, 'context') ?? '', /class="active"/)
  assert.ok(btn(html, 'context'))
  assert.ok(btn(html, 'changes'))
})

test('a desk with a started plan shows Review but still no Checklist tab', () => {
  const desk = { id: 'd1', status: 'running', plan: { status: 'approved' }, outputs: [] } as unknown as FullDesk
  const html = render(desk)
  assert.equal(btn(html, 'checklist'), undefined)
  assert.equal(btn(html, 'workers'), undefined)
  assert.ok(btn(html, 'review'))
})
