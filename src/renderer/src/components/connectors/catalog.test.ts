import { test } from 'node:test'
import assert from 'node:assert/strict'
import type { McpCatalogEntry, McpRegistryResult } from '@shared/types'
import { connectorName, detectionLabel, fieldsValid, filterCatalog, iconFor, initialValues, oneClick, registryDraft, runtimeWarning, tokenize } from './catalog'
import { Plug } from 'lucide-react'

const entry = (over: Partial<McpCatalogEntry>): McpCatalogEntry => ({
  id: 'x', name: 'X', description: '', category: 'Developer', icon: 'plug', publisher: 'Acme', official: false, docs: '',
  transport: 'stdio', runtime: 'node', auth: 'none', fields: [], installed: [], ...over
})

test('filterCatalog matches text across name, description and publisher, and narrows by category', () => {
  const list = [
    entry({ id: 'github', name: 'GitHub', description: 'Repos and issues', publisher: 'GitHub' }),
    entry({ id: 'pg', name: 'Postgres', description: 'Query a database', category: 'Data', publisher: 'MCP' })
  ]
  assert.deepEqual(filterCatalog(list, 'ISSUES', '').map((e) => e.id), ['github'])
  assert.deepEqual(filterCatalog(list, '', 'Data').map((e) => e.id), ['pg'])
  assert.deepEqual(filterCatalog(list, '', 'All').length, 2)
  assert.deepEqual(filterCatalog(list, 'mcp', 'Developer'), [])
})

test('fieldsValid lists blank required fields only; initialValues carries defaults', () => {
  const e = entry({ fields: [
    { id: 'token', label: 'Token', secret: true, required: true },
    { id: 'dir', label: 'Folder', required: true, default: '~/Documents' },
    { id: 'note', label: 'Note' }
  ] })
  assert.deepEqual(initialValues(e), { token: '', dir: '~/Documents', note: '' })
  assert.deepEqual(fieldsValid(e, initialValues(e)), ['token'])
  assert.deepEqual(fieldsValid(e, { token: '  ', dir: '/tmp' }), ['token'])
  assert.deepEqual(fieldsValid(e, { token: 'test-token-123', dir: '' }), ['dir'])
})

test('iconFor resolves known names and falls back to a plug', () => {
  assert.notEqual(iconFor('github'), Plug)
  assert.equal(iconFor('no-such-icon'), Plug)
  assert.equal(iconFor(undefined), Plug)
})

test('runtimeWarning names the missing launcher and says nothing when it is found or remote', () => {
  const rts = { node: { command: 'npx', found: false, hint: 'Install Node.js from nodejs.org' }, docker: { command: 'docker', found: true, hint: '' } }
  assert.equal(runtimeWarning({ runtime: 'node' }, rts), 'npx not found. Install Node.js from nodejs.org')
  assert.equal(runtimeWarning({ runtime: 'docker' }, rts), '')
  assert.equal(runtimeWarning({ runtime: 'remote' }, rts), '')
})

test('connectorName prefers the event, then reads the tool name', () => {
  assert.equal(connectorName('mcp__github__create_issue', { server: 'GitHub' }), 'GitHub')
  assert.equal(connectorName('mcp__my-server__do_thing'), 'my server')
  assert.equal(connectorName('send_email'), '')
})

test('registryDraft pre-fills a local hit with empty secret keys and a remote hit with header names', () => {
  const base: McpRegistryResult = {
    id: 'io.example/thing', name: 'thing', description: 'd', version: '1.0.0', repository: null, verified: false,
    transport: 'stdio', install: { command: 'npx', args: ['-y', 'thing'], url: '', env: {} }, secret_keys: ['API_KEY'], env_keys: ['API_KEY', 'REGION']
  }
  const local = registryDraft(base)
  assert.deepEqual(tokenize(local.argv), ['npx', '-y', 'thing'])
  assert.equal(local.envText, 'REGION=')
  assert.equal(local.secretsText, 'API_KEY=')
  const remote = registryDraft({ ...base, transport: 'http', install: { command: '', args: [], url: 'https://example.com/mcp', env: {} }, secret_keys: ['Authorization'], env_keys: [] })
  assert.equal(remote.url, 'https://example.com/mcp')
  assert.deepEqual(remote.headerRows, [{ k: 'Authorization', v: '' }])
  assert.equal(remote.argv, '')
})

test('every icon the bundled catalog names has a real lucide component, not the fallback', async () => {
  const { readFileSync } = await import('node:fs')
  const doc = JSON.parse(readFileSync('backend/personal_os/data/mcp_catalog.json', 'utf8')) as { entries: { id: string; icon: string }[] }
  const missing = doc.entries.filter((e) => iconFor(e.icon) === Plug).map((e) => `${e.id}:${e.icon}`)
  assert.deepEqual(missing, [])
})

test('detection shows the path when found and the hint when missing; only a found entry with no fields is one click', () => {
  const found = entry({ detected: { found: true, path: '/usr/local/bin/claude', hint: 'x' } })
  const missing = entry({ detected: { found: false, path: '', hint: 'Install it first.' } })
  assert.deepEqual(detectionLabel(found), { found: true, text: 'Detected at /usr/local/bin/claude' })
  assert.deepEqual(detectionLabel(missing), { found: false, text: 'Install it first.' })
  assert.equal(detectionLabel(entry({})), null)
  assert.equal(oneClick(found), true)
  assert.equal(oneClick(missing), false)
  assert.equal(oneClick(entry({})), false)
  assert.equal(oneClick({ ...found, fields: [{ id: 'k', label: 'K' }] }), false)
  assert.equal(runtimeWarning(missing, { node: { command: 'npx', found: false, hint: 'h' } }), '')
})
