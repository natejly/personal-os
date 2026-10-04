import assert from 'node:assert/strict'
import { test } from 'node:test'
import { clampSetting, hostError, networkMode, networkPatch, normalizeHost, sandboxNetMode } from './coworkSettings'

test('hostnames pass, everything else is refused with a reason', () => {
  for (const ok of ['pypi.org', 'files.pythonhosted.org', 'Example.COM', '.github.com']) assert.equal(hostError(ok), null, ok)
  for (const bad of ['', 'https://pypi.org', 'pypi.org/simple', '*.pypi.org', '10.0.0.1', '127.0.0.1', 'localhost', 'a b.com', 'pypi.org:443', 'me@pypi.org', '-bad.com'])
    assert.ok(hostError(bad), `should refuse ${JSON.stringify(bad)}`)
})

test('a stored host is lower-cased without a leading dot', () => {
  assert.equal(normalizeHost('  .PyPI.org '), 'pypi.org')
})

test('network choice maps onto the settings and back', () => {
  assert.deepEqual(networkPatch('off', ['x.com']), { shellNetwork: false, shellRegistryAccess: false, shellAllowedDomains: [] })
  assert.deepEqual(networkPatch('registries', ['x.com']), { shellNetwork: false, shellRegistryAccess: true, shellAllowedDomains: ['x.com'] })
  assert.deepEqual(networkPatch('open', ['x.com']), { shellNetwork: true, shellRegistryAccess: true, shellAllowedDomains: ['x.com'] })
  for (const mode of ['off', 'registries', 'open'] as const) assert.equal(networkMode(networkPatch(mode, mode === 'off' ? [] : ['x.com'])), mode)
})

test('missing flags read as registries; open beats the registry flag; hosts alone keep registries mode', () => {
  assert.equal(networkMode({}), 'registries')
  assert.equal(networkMode({ shellNetwork: true, shellRegistryAccess: false }), 'open')
  assert.equal(networkMode({ shellNetwork: false, shellRegistryAccess: false, shellAllowedDomains: ['a.com'] }), 'registries')
})

test('sandbox network: a legacy true reads as open, unknown or missing reads as off', () => {
  assert.equal(sandboxNetMode(true), 'open')
  assert.equal(sandboxNetMode(false), 'off')
  assert.equal(sandboxNetMode(undefined), 'off')
  assert.equal(sandboxNetMode('proxy'), 'proxy')
  assert.equal(sandboxNetMode('open'), 'open')
})

test('numbers are clamped to the backend range, blank keeps the fallback', () => {
  assert.equal(clampSetting('browserMaxTabs', '99', 4), 12)
  assert.equal(clampSetting('browserMaxTabs', '0', 4), 1)
  assert.equal(clampSetting('deskMaxCost', '-3', 2), 0)
  assert.equal(clampSetting('deskMaxCost', '', 2), 2)
  assert.equal(clampSetting('deskMaxCost', 'abc', 2), 2)
})
