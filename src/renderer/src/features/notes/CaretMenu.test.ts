import test from 'node:test'
import assert from 'node:assert/strict'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import CaretMenu from './CaretMenu'

test('a menu row renders the name and its description in separate elements', () => {
  const html = renderToStaticMarkup(createElement(CaretMenu, {
    label: 'Commands', active: 0, onPick: () => {}, onHover: () => {},
    items: [{ key: 'loop', label: '/loop', hint: 'Run a prompt on an interval until stopped' }]
  }))
  assert.match(html, /<span class="caret-menu-label">\/loop<\/span><span class="caret-menu-hint">Run a prompt on an interval until stopped<\/span>/)
})
