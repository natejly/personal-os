import test from 'node:test'
import assert from 'node:assert/strict'
import { initialDeskTab, panelTabsHidden, resolveDeskTab, tabShown, type DeskTab, type DeskTabFlags } from './deskTabs'

const all: DeskTabFlags = { hasConversation: true, hasChecklist: true, hasWorkers: true, hasDesk: true, hasTerminal: true }
const TABS: DeskTab[] = ['context', 'files', 'changes', 'checklist', 'workers', 'review', 'terminal']

test('the setting defaults to hidden: missing and true hide, only false shows', () => {
  assert.equal(panelTabsHidden(undefined), true)
  assert.equal(panelTabsHidden(null), true)
  assert.equal(panelTabsHidden({}), true)
  assert.equal(panelTabsHidden({ hideChatPanelTabs: true }), true)
  assert.equal(panelTabsHidden({ hideChatPanelTabs: false }), false)
})

test('hidden drops Checklist and Workers and leaves every other tab alone', () => {
  assert.equal(tabShown('checklist', all, true), false)
  assert.equal(tabShown('workers', all, true), false)
  for (const t of ['context', 'files', 'changes', 'review', 'terminal'] as DeskTab[]) assert.equal(tabShown(t, all, true), true, t)
})

test('not hidden shows the tabs exactly as before', () => {
  for (const t of TABS) assert.equal(tabShown(t, all, false), true, t)
  const none: DeskTabFlags = { hasConversation: false, hasChecklist: false, hasWorkers: false, hasDesk: false, hasTerminal: false }
  assert.deepEqual(TABS.filter((t) => tabShown(t, none, false)), ['context'])
  assert.equal(tabShown('checklist', { ...all, hasChecklist: false }, false), false)
  assert.equal(tabShown('workers', { ...all, hasWorkers: false }, false), false)
})

test('a panel opened with a live worker starts on Files when hidden, Workers otherwise', () => {
  const o = { terminalRequest: false, reviewing: false, liveWorker: true }
  assert.equal(initialDeskTab({ ...o, hidden: true }), 'files')
  assert.equal(initialDeskTab({ ...o, hidden: false }), 'workers')
  assert.equal(initialDeskTab({ ...o, liveWorker: false, hidden: false }), 'files')
  assert.equal(initialDeskTab({ ...o, reviewing: true, hidden: true }), 'review')
  assert.equal(initialDeskTab({ ...o, reviewing: true, terminalRequest: true, hidden: false }), 'terminal')
})

test('the selected tab never lands on a hidden one', () => {
  assert.equal(resolveDeskTab('workers', false, all, true), 'files')
  assert.equal(resolveDeskTab('checklist', false, all, true), 'files')
  assert.equal(resolveDeskTab('checklist', false, { ...all, hasConversation: false }, true), 'context')
  assert.equal(resolveDeskTab('workers', true, all, true), 'context')
})

test('not hidden, the selected tab resolves as before', () => {
  assert.equal(resolveDeskTab('workers', false, all, false), 'workers')
  assert.equal(resolveDeskTab('checklist', false, all, false), 'checklist')
  assert.equal(resolveDeskTab('workers', false, { ...all, hasWorkers: false }, false), 'files')
  assert.equal(resolveDeskTab('review', true, all, false), 'context')
  assert.equal(resolveDeskTab('terminal', false, { ...all, hasConversation: false, hasTerminal: false }, false), 'context')
})
