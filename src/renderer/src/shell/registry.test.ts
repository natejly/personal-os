/**
 * The module list against the shell's own lists: every id a module claims is unique, and each one
 * lands in the slot the shell reads it from (Settings → Sidebar, the canvas catalog).
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { OPTIONAL_VIEWS } from '../modules'
import { WIDGETS } from '../canvas/registry'
import { navEntries } from './nav'
import { MODULES, moduleForView } from './registry'

const unique = (xs: string[]): boolean => new Set(xs).size === xs.length

test('module keys, view ids, widget kinds are unique', () => {
  assert.ok(MODULES.length > 0)
  assert.ok(unique(MODULES.map((m) => m.key)))
  assert.ok(unique(MODULES.flatMap((m) => (m.view ? [m.view.id] : []))))
  assert.ok(unique(MODULES.flatMap((m) => (m.widget ? [m.widget.kind] : []))))
})

test('an optional view is in OPTIONAL_VIEWS', () => {
  for (const m of MODULES) {
    if (m.view?.optional) assert.ok(OPTIONAL_VIEWS.some((o) => o.view === m.view!.id), `${m.key}: view ${m.view.id}`)
    if (m.nav) assert.ok(m.view, `${m.key}: nav needs a view`)
  }
})

test('every nav entry says what it is for', () => {
  for (const e of navEntries()) assert.ok(e.description?.trim(), `${e.view} needs a description`)
})

test('the canvas catalog serves each module widget, and lookups find their module', () => {
  for (const m of MODULES) {
    if (m.widget) assert.equal(WIDGETS[m.widget.kind], m.widget)
    if (m.view) assert.equal(moduleForView(m.view.id), m)
  }
  assert.equal(moduleForView('chat'), undefined)
})
