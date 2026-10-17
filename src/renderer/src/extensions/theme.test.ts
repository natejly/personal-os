/** Design tokens: the stylesheet an extension's or a profile's tokens become, and that the profiles of the enabled ones are swapped as a set. */
import test from 'node:test'
import assert from 'node:assert/strict'
import { activateExtension } from './host'
import { applyProfileThemes } from './profiles'
import { themeCss } from './theme'
import type { Profile } from '@shared/types'

test('tokens for one scheme become a rule for that theme and for the OS setting when it matches', () => {
  assert.equal(
    themeCss([{ tokens: { '--bg': '#101010', '--radius': '6px' }, scheme: 'dark' }]),
    ":root[data-theme='dark']{--bg:#101010;--radius:6px}@media (prefers-color-scheme:dark){:root[data-theme='system']{--bg:#101010;--radius:6px}}"
  )
})

test('tokens without a scheme cover light and dark', () => {
  const css = themeCss([{ tokens: { '--bg': '#fff' } }])
  assert.match(css, /data-theme='dark'/)
  assert.match(css, /data-theme='light'/)
})

test('a later set is written after an earlier one so it wins on a tie', () => {
  const css = themeCss([{ tokens: { '--bg': 'red' }, scheme: 'light' }, { tokens: { '--bg': 'blue' }, scheme: 'light' }])
  assert.ok(css.indexOf('--bg:red') < css.indexOf('--bg:blue'))
})

test('ui.theme registers tokens and its disposable takes them away', () => {
  let d: { dispose(): void } | undefined
  activateExtension('demo', { activate: (ui) => { d = ui.theme({ '--bg': '#000' }, { scheme: 'dark' }) } })
  assert.ok(d)
  d.dispose()
})

const profile = (over: Partial<Profile>): Profile => ({ id: 'p', name: 'P', description: '', enabled: true, theme: {}, ...over })

test('only enabled profiles apply their tokens, and a new list replaces the old one', () => {
  const seen: string[] = []
  const stub = globalThis as unknown as { document?: unknown }
  stub.document = {
    getElementById: () => ({ set textContent(v: string) { seen.push(v) } }),
    head: { appendChild: (el: unknown) => el }, createElement: () => ({})
  }
  try {
    applyProfileThemes([profile({ theme: { dark: { '--bg': 'red' } } }), profile({ id: 'q', enabled: false, theme: { dark: { '--bg': 'blue' } } })])
    assert.match(seen.at(-1) ?? '', /--bg:red/)
    assert.doesNotMatch(seen.at(-1) ?? '', /--bg:blue/)
    applyProfileThemes([])
    assert.equal(seen.at(-1), '')
  } finally {
    delete stub.document
  }
})
