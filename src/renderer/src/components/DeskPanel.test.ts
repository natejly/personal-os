import test from 'node:test'
import assert from 'node:assert/strict'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import type { WorkerInfo } from '@shared/types'
import { useStore } from '../store'
import DeskPanel from './DeskPanel'

const CHAT = 'chat-1'
const worker = { id: 'w1', conversation_id: CHAT, title: 'Dig', goal: 'dig', status: 'running', now: '', queue_position: null, started_at: 1, ended_at: null, resume_of: null, resumable: false, pending_approvals: [] } as unknown as WorkerInfo

const render = (hideChatPanelTabs: boolean): string => {
  // Server rendering reads the store's initial state (its server snapshot), so seed that rather than setState.
  Object.assign(useStore.getInitialState(), {
    settings: { hideChatPanelTabs },
    workers: { [CHAT]: [worker] },
    plans: { [CHAT]: [{ text: 'one', status: 'in_progress' }] },
    contextOpen: false,
  })
  return renderToStaticMarkup(createElement(DeskPanel, { conversationId: CHAT, onClose: () => {} }))
}

const btn = (html: string, key: string): string | undefined => html.match(new RegExp(`<button[^>]*data-testid="desk-tab-${key}"[^>]*>`))?.[0]

test('hidden: no Checklist or Workers tab, and a live worker does not pull the panel onto Workers', () => {
  const html = render(true)
  assert.equal(btn(html, 'checklist'), undefined)
  assert.equal(btn(html, 'workers'), undefined)
  assert.match(btn(html, 'files') ?? '', /class="active"/)
  assert.ok(btn(html, 'context'))
  assert.ok(btn(html, 'changes'))
})

test('shown: both tabs render, and a live worker opens Workers', () => {
  const html = render(false)
  assert.ok(btn(html, 'checklist'))
  assert.match(btn(html, 'workers') ?? '', /class="active"/)
})
