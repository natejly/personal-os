/**
 * The module list against the shell's own lists: every id a module claims is unique, and each one
 * lands in the slot the shell reads it from (Settings → Modules, the canvas catalog).
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { HOME_MODULES, OPTIONAL_VIEWS } from '../modules'
import { WIDGETS } from '../canvas/registry'
import { MODULES, moduleForView, moduleHome } from './registry'

const unique = (xs: string[]): boolean => new Set(xs).size === xs.length

test('module keys, view ids, widget kinds and home keys are unique', () => {
  assert.ok(MODULES.length > 0)
  assert.ok(unique(MODULES.map((m) => m.key)))
  assert.ok(unique(MODULES.flatMap((m) => (m.view ? [m.view.id] : []))))
  assert.ok(unique(MODULES.flatMap((m) => (m.widget ? [m.widget.kind] : []))))
  assert.ok(unique(MODULES.flatMap((m) => (m.home ? [m.home.key] : []))))
})

test('an optional view is in OPTIONAL_VIEWS and a home card is in HOME_MODULES', () => {
  for (const m of MODULES) {
    if (m.view?.optional) assert.ok(OPTIONAL_VIEWS.some((o) => o.view === m.view!.id), `${m.key}: view ${m.view.id}`)
    if (m.home) assert.ok(HOME_MODULES.some((h) => h.key === m.home!.key), `${m.key}: home ${m.home.key}`)
    if (m.nav) assert.ok(m.view, `${m.key}: nav needs a view`)
  }
})

test('the canvas catalog serves each module widget, and lookups find their module', () => {
  for (const m of MODULES) {
    if (m.widget) assert.equal(WIDGETS[m.widget.kind], m.widget)
    if (m.view) assert.equal(moduleForView(m.view.id), m)
    if (m.home) assert.equal(moduleHome(m.home.key), m)
  }
  assert.equal(moduleForView('chat'), undefined)
})
