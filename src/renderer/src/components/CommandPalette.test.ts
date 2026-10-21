import test from 'node:test'
import assert from 'node:assert/strict'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import type { Settings } from '@shared/types'
import { useStore } from '../store'
import { useCanvas } from '../canvas/store'
import CommandPalette from './CommandPalette'
import { CORE, registerCommand } from '../extensions/host'
import './SettingsModal' // registers the Runtime settings panel, as the app does

// useModal reads the focused element while rendering.
;(globalThis as { document?: unknown }).document = { activeElement: null }

const palette = (settings: Partial<Settings>, query = ''): string => {
  // Server rendering reads the stores' initial state (their server snapshot), so seed that rather than setState.
  Object.assign(useStore.getInitialState(), { settings })
  Object.assign(useCanvas.getInitialState(), { order: ['c1'], canvases: { c1: { id: 'c1', name: 'Canvas 1', windows: [] } } })
  return renderToStaticMarkup(createElement(CommandPalette, { initialQuery: query }))
}

test('the palette lists canvases only while Canvases is on', () => {
  assert.match(palette({ hiddenViews: [] }, 'canvas'), /Canvas 1/)
  assert.doesNotMatch(palette({ hiddenViews: ['spaces'] }, 'canvas'), /Canvas 1/)
})

test('the palette lists the Runtime settings panel', () => {
  assert.match(palette({ hiddenViews: [] }, 'runtime'), /Runtime/)
})

const options = (html: string): number => (html.match(/role="option"/g) ?? []).length
const headings = (html: string): string[] => [...html.matchAll(/palette-heading">([^<]+)</g)].map((m) => m[1])

test('rows sit under section headings in a fixed order', () => {
  assert.deepEqual(headings(palette({ hiddenViews: [] })), ['Chats', 'Files', 'Go to', 'Settings'])
})

test('New chat and New file are listed at rest, under Chats and Files', () => {
  const html = palette({ hiddenViews: [] })
  assert.ok(html.indexOf('heading">Chats') < html.indexOf('New chat'))
  assert.ok(html.indexOf('heading">Files') < html.indexOf('New file'))
  assert.ok(html.indexOf('New file') < html.indexOf('heading">Go to'))
})

test('a section shows at most five rows at rest and more once a query is typed', () => {
  const html = palette({ hiddenViews: [] })
  assert.ok(options(html) <= 20)
  assert.ok(options(palette({ hiddenViews: [] }, 'e')) > options(html))
})

test('projects and non-rail settings panels list only when typed', () => {
  useStore.getInitialState().projects = [{ id: 'p1', name: 'Zephyr project' }] as never
  assert.doesNotMatch(palette({ hiddenViews: [] }), /Zephyr project|Runtime/)
  assert.match(palette({ hiddenViews: [] }, 'zephyr'), /Zephyr project/)
})

test('a registered command lands under its section, defaulting to Go to', () => {
  registerCommand(CORE, { id: 'test:files-cmd', label: 'Zzfiles command', section: 'Files', run: () => undefined })
  registerCommand(CORE, { id: 'test:plain-cmd', label: 'Zzplain command', run: () => undefined })
  const html = palette({ hiddenViews: [] }, 'zz')
  assert.deepEqual(headings(html), ['Files', 'Go to'])
  assert.ok(html.indexOf('Zzfiles command') < html.indexOf('heading">Go to'))
  assert.ok(html.indexOf('heading">Go to') < html.indexOf('Zzplain command'))
})
