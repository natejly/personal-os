import test from 'node:test'
import assert from 'node:assert/strict'
import type { CatalogCard, ExtensionConsent } from '@shared/types'
import { alreadyUpToDate, authorSource, badgeKind, blockingFindings, changeLines, contextCostText, contributionChips, installRequest, requiredByText, shortHash } from './extensionsCatalog'

const contributes = { tools: [{ name: 'a', danger: 'read' }, { name: 'b', danger: 'danger' }], views: ['v'], slots: [], hooks: ['h'], commands: [], settingsFields: [], processes: ['p'] }

test('chips flag hooks, processes and dangerous tools', () => {
  const chips = contributionChips({ contributes })
  assert.deepEqual(chips.map((c) => c.label), ['2 tools', '1 view', '1 hook', '1 background process'])
  assert.deepEqual(chips.map((c) => c.warn), [true, false, true, true])
})

test('badge, source line and hash', () => {
  assert.equal(badgeKind({ source: 'bundled', verified: false }), 'bundled')
  assert.equal(badgeKind({ source: 'git', verified: true }), 'verified')
  assert.equal(badgeKind({ source: 'catalog' }), 'unverified')
  assert.equal(authorSource({ author: 'Ann', source: 'git', url: 'https://example.com/a/b' }), 'Ann - Git (example.com)')
  assert.equal(authorSource({ source: 'bundled' }), 'Bundled')
  assert.equal(shortHash('abcdef0123456789'), 'abcdef012345')
})

test('cost and required-by text', () => {
  assert.equal(contextCostText(0), 'Adds nothing to the prompt')
  assert.equal(contextCostText(1200), 'Adds about 1,200 tokens every turn')
  assert.equal(requiredByText({ requiredBy: ['cal', 'mail'] }, { cal: 'Calendar' }), 'Required by Calendar, mail')
  assert.equal(requiredByText({ requiredBy: [] }), '')
})

test('install request splits paths from urls', () => {
  assert.deepEqual(installRequest(' /home/me/ext ', '', ''), { path: '/home/me/ext' })
  assert.deepEqual(installRequest('https://x.io/r.git', ' main ', 'sub'), { url: 'https://x.io/r.git', ref: 'main', subdir: 'sub' })
  assert.ok('error' in installRequest('', '', ''))
  assert.ok('error' in installRequest('relative/dir', '', ''))
})

test('consent helpers', () => {
  const c = { findings: [{ level: 'error', message: 'bad' }, { level: 'info', message: 'ok' }], card: { updateAvailable: false } as CatalogCard, changes: { permissionsAdded: ['net'], hooksAdded: [], toolsAdded: [] } } as unknown as ExtensionConsent
  assert.deepEqual(blockingFindings(c), ['bad'])
  assert.equal(alreadyUpToDate(c), false)
  assert.equal(alreadyUpToDate({ ...c, changes: undefined }), true)
  assert.deepEqual(changeLines(c.changes), ['New permission: net'])
})
