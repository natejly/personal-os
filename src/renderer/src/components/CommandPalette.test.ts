import test from 'node:test'
import assert from 'node:assert/strict'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import type { Settings } from '@shared/types'
import { useStore } from '../store'
import { useCanvas } from '../canvas/store'
import CommandPalette from './CommandPalette'
import './SettingsModal' // registers the Runtime settings panel, as the app does

// useModal reads the focused element while rendering.
;(globalThis as { document?: unknown }).document = { activeElement: null }

const palette = (settings: Partial<Settings>): string => {
  // Server rendering reads the stores' initial state (their server snapshot), so seed that rather than setState.
  Object.assign(useStore.getInitialState(), { settings })
  Object.assign(useCanvas.getInitialState(), { order: ['c1'], canvases: { c1: { id: 'c1', name: 'Canvas 1', windows: [] } } })
  return renderToStaticMarkup(createElement(CommandPalette))
}

test('the palette lists canvases only while Canvases is on', () => {
  assert.match(palette({ hiddenViews: [] }), /Canvas 1/)
  assert.doesNotMatch(palette({ hiddenViews: ['spaces'] }), /Canvas 1/)
})

test('the palette lists the Runtime settings panel', () => {
  assert.match(palette({ hiddenViews: [] }), /Runtime/)
})
