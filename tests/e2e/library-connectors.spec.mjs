import { test, expect } from './fixtures.mjs'
import { openLibrary, sendChat, resize, noOverflow, PY, STUB } from './helpers/library.mjs'

const stubBody = (name, mode = 'friendly') => ({ name, transport: 'stdio', command: PY, args: [STUB, '--mode', mode], enabled: true })

async function ready(api, name, n = 1) {
  await expect.poll(async () => {
    const s = (await api('/mcp/servers')).find((x) => x.name === name)
    return s?.live?.ready ? s.tools.length : -1
  }, { timeout: 60_000 }).toBeGreaterThanOrEqual(n)
  return (await api('/mcp/servers')).find((x) => x.name === name)
}

const quoted = (p) => `"${p}"`

test('add the stub through the UI (path with spaces), it connects and lists tools', async ({ grain }) => {
  const { page, api } = grain
  await openLibrary(page, 'Connectors')
  await expect(page.getByText('No connectors yet')).toBeVisible()
  await page.getByRole('button', { name: 'Add custom' }).click()
  await page.getByPlaceholder('Filesystem', { exact: true }).fill('Stub')
  await page.getByPlaceholder(/npx -y/).fill(`${quoted(PY)} ${quoted(STUB)} --mode friendly`)
  await page.getByRole('button', { name: 'Add connector' }).click()
  const head = page.locator('.mcp-server', { hasText: 'Stub' }).first()
  await expect(head.getByText(/connected/)).toBeVisible({ timeout: 60_000 })
  await expect(head.getByText(/5 tools/)).toBeVisible({ timeout: 30_000 })
  // expanded after add: tools, each defaulting to ask
  for (const t of ['echo', 'add', 'note_append', 'boom', 'mutate']) await expect(head.locator('.mcp-tool', { hasText: 'mcp__stub__' + t }).first()).toBeVisible()
  const tools = (await api('/mcp/tools')).tools
  expect(tools.every((t) => t.effective.mode === 'ask')).toBe(true)
  expect(grain.consoleErrors).toEqual([])
})

test('call a stub tool from chat: ask first, then on', async ({ grain }) => {
  const { page, api, llm } = grain
  await api('/mcp/servers', { method: 'POST', body: stubBody('Stub') })
  await ready(api, 'Stub', 5)
  // in "ask" mode a card waits for the user
  await sendChat(page, '!!tool mcp__stub__add {"a":2,"b":40}')
  const allow = page.getByRole('button', { name: /^(Allow|Approve)/ }).first()
  await expect(allow).toBeVisible({ timeout: 30_000 })
  await allow.click()
  await expect(page.locator('.msg.assistant').last()).toContainText('tool done', { timeout: 30_000 })
  const toolMsg = JSON.stringify(llm.calls.flatMap((c) => c.messages.filter((m) => m.role === 'tool')))
  expect(toolMsg).toContain('42')

  // grant 'on' via the UI toggle then it runs without a card
  await openLibrary(page, 'Connectors')
  const row = page.locator('.mcp-tool', { hasText: 'mcp__stub__echo' })
  await page.locator('.mcp-server', { hasText: 'Stub' }).getByRole('button', { name: 'Expand' }).click()
  await row.getByRole('group', { name: 'Permission for echo' }).getByRole('button', { name: 'on' }).click()
  await expect.poll(async () => (await api('/mcp/tools')).tools.find((t) => t.slug === 'mcp__stub__echo').effective.mode).toBe('on')
  await sendChat(page, '!!tool mcp__stub__echo {"text":"hello connector"}')
  await expect(page.locator('.msg.assistant').last()).toContainText('tool done', { timeout: 30_000 })
  expect(JSON.stringify(llm.calls.flatMap((c) => c.messages.filter((m) => m.role === 'tool')))).toContain('hello connector')
  expect(grain.consoleErrors).toEqual([])
})

test('a grant is bound to the schema hash: the server rewriting a tool invalidates it', async ({ grain }) => {
  const { page, api } = grain
  await api('/mcp/servers', { method: 'POST', body: stubBody('Stub') })
  await ready(api, 'Stub', 5)
  for (const t of ['echo', 'mutate']) await api(`/mcp/tools/mcp__stub__${t}/grant`, { method: 'PUT', body: { mode: 'on' } })
  const before = (await api('/mcp/tools')).tools.find((t) => t.slug === 'mcp__stub__echo')
  expect(before.effective.mode).toBe('on')
  expect(before.effective.stale).toBe(false)

  await sendChat(page, '!!tool mcp__stub__mutate {}')
  await expect(page.locator('.msg.assistant').last()).toContainText('tool done', { timeout: 30_000 })
  await expect.poll(async () => (await api('/mcp/tools')).tools.find((t) => t.slug === 'mcp__stub__echo').schema_hash, { timeout: 30_000 }).not.toBe(before.schema_hash)
  const after = (await api('/mcp/tools')).tools.find((t) => t.slug === 'mcp__stub__echo')
  expect(after.effective.stale).toBe(true)
  expect(after.effective.mode).toBe('ask') // decayed from on
  // untouched sibling keeps its grant
  expect((await api('/mcp/tools')).tools.find((t) => t.slug === 'mcp__stub__mutate').effective.mode).toBe('on')

  await openLibrary(page, 'Connectors')
  await page.locator('.mcp-server', { hasText: 'Stub' }).getByRole('button', { name: 'Expand' }).click()
  const row = page.locator('.mcp-tool', { hasText: 'mcp__stub__echo' })
  await expect(row.getByText('changed since approved')).toBeVisible()
  await expect(row.getByText(/Definition changed|Quarantined/)).toBeVisible()
  // the next call asks again
  await sendChat(page, '!!tool mcp__stub__echo {"text":"after change"}')
  await expect(page.getByRole('button', { name: /^(Allow|Approve)/ }).first()).toBeVisible({ timeout: 30_000 })
})

test('remove connector (two-step) and persistence across relaunch', async ({ grain }) => {
  const { api } = grain
  await api('/mcp/servers', { method: 'POST', body: stubBody('Stub') })
  await ready(api, 'Stub', 5)
  const page = await grain.relaunch()
  await openLibrary(page, 'Connectors')
  const srv = page.locator('.mcp-server', { hasText: 'Stub' }).first()
  await expect(srv.getByText(/connected/)).toBeVisible({ timeout: 60_000 })
  await srv.getByRole('button', { name: 'Expand' }).click()
  await srv.getByRole('button', { name: 'Remove' }).click()
  await srv.getByRole('button', { name: 'Keep' }).click()
  expect((await api('/mcp/servers')).length).toBe(1)
  await srv.getByRole('button', { name: 'Remove' }).click()
  await srv.getByRole('button', { name: 'Really remove' }).click()
  await expect(page.getByText('No connectors yet')).toBeVisible()
  expect(await api('/mcp/servers')).toEqual([])
  expect(grain.consoleErrors).toEqual([])
})

test('a bad command shows a clear error; restart / logs / check do not crash', async ({ grain }) => {
  const { page, api } = grain
  await openLibrary(page, 'Connectors')
  await page.getByRole('button', { name: 'Add custom' }).click()
  await page.getByPlaceholder('Filesystem', { exact: true }).fill('Broken')
  await page.getByPlaceholder(/npx -y/).fill('/definitely/not/a/binary --flag')
  await page.getByRole('button', { name: 'Check it first' }).click()
  await expect(page.locator('.mcp-report').first()).toBeVisible({ timeout: 60_000 })
  await page.getByRole('button', { name: 'Add connector' }).click()
  const srv = page.locator('.mcp-server', { hasText: 'Broken' }).first()
  await expect(srv.getByText('failed')).toBeVisible({ timeout: 60_000 })
  await expect(srv.locator('.mcp-detail')).toBeVisible()
  const detail = await srv.locator('.mcp-detail').innerText()
  expect(detail.length).toBeGreaterThan(5)
  await srv.getByRole('button', { name: 'Restart' }).click()
  await srv.getByRole('button', { name: 'Logs' }).click()
  await srv.getByRole('button', { name: /Check/ }).click()
  await expect(srv.locator('.mcp-report').first()).toBeVisible({ timeout: 60_000 })
  // empty command refused client-side
  await page.getByRole('button', { name: 'Add custom' }).click()
  await page.getByRole('button', { name: 'Add connector' }).click()
  await expect(page.getByText('Enter the command that starts the server')).toBeVisible()
  expect((await api('/mcp/servers')).length).toBe(1)
})

test('remote HTTP connector form validation, paste of config JSON', async ({ grain }) => {
  const { page, api } = grain
  await openLibrary(page, 'Connectors')
  await page.getByRole('button', { name: 'Add custom' }).click()
  await page.getByRole('button', { name: 'Remote' }).click()
  await page.getByRole('button', { name: 'Add connector' }).click()
  await expect(page.getByText(/Enter the server's https:\/\/ URL/).first()).toBeVisible()
  for (const bad of ['ftp://x.example/mcp', 'not a url', 'https://']) {
    await page.getByPlaceholder('https://example.com/mcp').fill(bad)
    await page.getByRole('button', { name: 'Add connector' }).click()
    await expect(page.getByText(/Enter the server's https:\/\/ URL/).first()).toBeVisible()
  }
  expect(await api('/mcp/servers')).toEqual([])
  // paste a config block into the URL field
  const url = page.getByPlaceholder('https://example.com/mcp')
  await url.fill('')
  await url.evaluate((el) => {
    const dt = new DataTransfer()
    dt.setData('text', '{"mcpServers":{"remote-one":{"url":"http://127.0.0.1:9/mcp","headers":{"Authorization":"Bearer x"}}}}')
    el.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }))
  })
  await expect(url).toHaveValue('http://127.0.0.1:9/mcp')
  await expect(page.getByLabel('Header name')).toHaveValue('Authorization')
  await page.getByRole('button', { name: 'Add connector' }).click()
  const srv = page.locator('.mcp-server', { hasText: 'remote-one' }).first()
  await expect(srv).toBeVisible()
  await expect(srv.locator('.mcp-detail, small.muted', { hasText: /failed|connecting/ }).first()).toBeVisible({ timeout: 30_000 })
  const s = (await api('/mcp/servers'))[0]
  expect(JSON.stringify(s)).not.toContain('Bearer x') // header values are never returned
})

test('enable/disable toggle; 820x520 layout; hostile server is flagged', async ({ grain }) => {
  const { page, api } = grain
  await api('/mcp/servers', { method: 'POST', body: stubBody('Stub') })
  await api('/mcp/servers', { method: 'POST', body: stubBody('Hostile', 'hostile') })
  await ready(api, 'Stub', 5)
  await resize(grain)
  await openLibrary(page, 'Connectors')
  const srv = page.locator('.mcp-server', { hasText: 'Stub' }).first()
  await srv.locator('label.switch-wrap').click()
  await expect(srv.getByText('off')).toBeVisible({ timeout: 30_000 })
  await expect.poll(async () => (await api('/mcp/servers')).find((x) => x.name === 'Stub').enabled).toBe(false)
  await srv.locator('label.switch-wrap').click()
  await expect(srv.getByText(/connected/)).toBeVisible({ timeout: 60_000 })
  await noOverflow(page)
  await page.locator('.mcp-server', { hasText: 'Hostile' }).getByRole('button', { name: 'Expand' }).click()
  await page.locator('.mcp-server', { hasText: 'Hostile' }).getByRole('button', { name: /Check/ }).click()
  await expect(page.locator('.mcp-server', { hasText: 'Hostile' }).locator('.mcp-report').first()).toBeVisible({ timeout: 90_000 })
  await noOverflow(page)
  expect(grain.consoleErrors).toEqual([])
})

test('double-clicking Add connector adds one; a silent server ends in a clear error', async ({ grain }) => {
  const { page, api } = grain
  await openLibrary(page, 'Connectors')
  await page.getByRole('button', { name: 'Add custom' }).click()
  await page.getByPlaceholder('Filesystem', { exact: true }).fill('Quiet')
  await page.getByPlaceholder(/npx -y/).fill(quoted(PY) + ' ' + quoted(STUB) + ' --mode silent')
  await page.getByRole('button', { name: 'Add connector' }).dblclick()
  await expect(page.locator('.mcp-server', { hasText: 'Quiet' }).first()).toBeVisible()
  await page.waitForTimeout(800)
  expect((await api('/mcp/servers')).length).toBe(1)
  const srv = page.locator('.mcp-server', { hasText: 'Quiet' }).first()
  await expect(srv.locator('.mcp-detail')).toBeVisible({ timeout: 90_000 })
  expect((await srv.locator('.mcp-detail').innerText()).toLowerCase()).toMatch(/time|connect|handshake|respond/)
})
