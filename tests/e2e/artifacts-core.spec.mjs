import { test, expect } from './fixtures.mjs'
import { newChat, say, reply, smallWindow, realErrors } from './helpers/chat.mjs'

const PAGE = (body = '', script = '') => `<!doctype html><html><head><title>Probe</title></head><body><h1 id="h">Probe page</h1>${body}<script>${script}</script></body></html>`

async function allowTools(api) {
  // Deferred tools need a tool_search round trip the mock cannot make; load everything up front.
  await api('/settings', { method: 'PUT', body: { toolDeferAbove: 0 } })
}

async function makeViaTool(grain, title, html) {
  const { page, api } = grain
  await allowTools(api)
  await newChat(page)
  await say(page, '!!tool artifact_create ' + JSON.stringify({ title, html }))
  return page
}

const artFrame = (page) => page.frames().find((f) => f !== page.mainFrame() && /\/artifacts\/.+\/render/.test(f.url()))

async function openPages(page) {
  await page.locator('.nav-item', { hasText: /Files/ }).first().click()
  await page.getByRole('tab', { name: 'Pages' }).first().click()
}

test('artifact_create renders a sandboxed card; the frame cannot reach the app or the backend', async ({ grain }) => {
  const { page, api, backend } = grain
  const probe = `
    const out = {}
    out.os = typeof window.os; out.grain = typeof window.grain; out.require = typeof require; out.process = typeof process
    try { out.parent = String(window.parent.document.title) } catch (e) { out.parent = 'blocked' }
    try { out.ls = String(localStorage.length) } catch (e) { out.ls = 'blocked' }
    fetch(${JSON.stringify(backend.url)} + '/health').then(() => { out.fetch = 'reached' }).catch(() => { out.fetch = 'blocked' }).finally(() => { document.body.setAttribute('data-probe', JSON.stringify(out)) })`
  await makeViaTool(grain, 'Probe', PAGE('', probe))
  const card = page.locator('.art-card').last()
  await expect(card).toContainText('Probe')
  const iframe = card.locator('iframe.art-frame')
  await expect(iframe).toHaveAttribute('sandbox', 'allow-scripts')
  await expect.poll(() => !!artFrame(page), { timeout: 30_000 }).toBe(true)
  const f = artFrame(page)
  await expect(f.locator('#h')).toHaveText('Probe page')
  await expect.poll(() => f.evaluate(() => document.body.getAttribute('data-probe')), { timeout: 15_000 }).toBeTruthy()
  const out = JSON.parse(await f.evaluate(() => document.body.getAttribute('data-probe')))
  expect(out).toMatchObject({ os: 'undefined', grain: 'undefined', require: 'undefined', process: 'undefined', parent: 'blocked', fetch: 'blocked' })
  expect(out.ls).toBe('blocked')
  const arts = await api('/artifacts')
  expect(arts.length).toBe(1)
  expect(realErrors(grain).filter((e) => !/Content Security Policy|Refused to connect|Failed to fetch/i.test(e))).toEqual([])
})

test('Pages view lists it; open, view source, copy, download, delete', async ({ grain }) => {
  const { page, api } = grain
  await makeViaTool(grain, 'Listed one', PAGE('<p>alpha</p>'))
  await api('/artifacts', { method: 'POST', body: { title: 'Second one', code: PAGE('<p>beta</p>') } })
  await openPages(page)
  await expect(page.locator('.art-row')).toHaveCount(2)
  await page.getByLabel('Search artifacts').fill('Second')
  await expect(page.locator('.art-row')).toHaveCount(1)
  await page.getByLabel('Search artifacts').fill('')
  await page.locator('.art-row', { hasText: 'Listed one' }).click()
  await expect(page.locator('.art-view.inline h2')).toHaveText('Listed one')
  await page.getByRole('button', { name: 'View source' }).click()
  await expect(page.locator('.art-source')).toContainText('<p>alpha</p>')
  // Electron routes real downloads through a save dialog; record what the anchor was asked to save instead.
  await page.evaluate(() => { window.__dl = []; HTMLAnchorElement.prototype.click = function () { window.__dl.push(this.download) } })
  await page.getByRole('button', { name: 'Download HTML' }).click()
  await expect.poll(() => page.evaluate(() => window.__dl[0])).toBe('listed-one.html')
  // delete
  page.once('dialog', (d) => d.accept())
  await page.getByRole('button', { name: 'Delete artifact' }).click()
  await expect(page.locator('.art-row')).toHaveCount(1)
  expect((await api('/artifacts')).length).toBe(1)
})

test('open from the chat card, fullscreen, Escape closes; copy puts the source on the clipboard', async ({ grain }) => {
  const { page, app } = grain
  await app.context().grantPermissions(['clipboard-read', 'clipboard-write']).catch(() => {})
  await makeViaTool(grain, 'Openable', PAGE('<p>gamma</p>'))
  const card = page.locator('.art-card').last()
  await card.getByRole('button', { name: 'Open', exact: true }).click()
  const dlg = page.locator('.art-view.full')
  await expect(dlg).toBeVisible()
  await dlg.getByRole('button', { name: 'Full screen' }).click()
  await expect(dlg).toHaveClass(/fs/)
  await page.keyboard.press('Escape')
  await expect(dlg).not.toHaveClass(/fs/)
  await expect(dlg).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(dlg).toHaveCount(0)
  await card.getByRole('button', { name: 'Copy HTML' }).click()
  await expect.poll(() => page.evaluate(() => navigator.clipboard.readText()).catch(() => ''), { timeout: 10_000 }).toContain('gamma')
})

test('artifact_edit patches it into v2; versions listed and restorable; the card shows the edit', async ({ grain }) => {
  const { page, api } = grain
  await makeViaTool(grain, 'Patchable', PAGE('<p id="t">before</p>'))
  const [a] = await api('/artifacts')
  await say(page, '!!tool artifact_edit ' + JSON.stringify({ artifact_id: a.id, edits: [{ search: 'before', replace: 'after' }] }))
  await expect.poll(async () => (await api(`/artifacts/${a.id}`)).version, { timeout: 20_000 }).toBe(2)
  expect((await api(`/artifacts/${a.id}`)).code).toContain('after')
  const vs = await api(`/artifacts/${a.id}/versions`)
  expect(vs.length).toBe(2)
  const card = page.locator('.art-card').last()
  await expect(card.locator('.tag', { hasText: 'v2' })).toBeVisible()
  await expect.poll(() => artFrame(page) && artFrame(page).locator('#t').textContent().catch(() => ''), { timeout: 20_000 }).toBeTruthy()
  // a failing patch changes nothing and says so
  await say(page, '!!tool artifact_edit ' + JSON.stringify({ artifact_id: a.id, edits: [{ search: 'no such text', replace: 'x' }] }))
  await expect(page.locator('.art-card').last()).toContainText(/nothing was changed|failed/i)
  expect((await api(`/artifacts/${a.id}`)).version).toBe(2)
  // restore v1 from the viewer
  await openPages(page)
  await page.locator('.art-view.inline select[aria-label="Version"]').selectOption({ label: vs.find((v) => v.version === 1) ? /v1/ : '' }).catch(async () => {
    await page.locator('.art-view.inline select[aria-label="Version"]').selectOption({ index: 1 })
  })
  await page.getByRole('button', { name: /Restore/ }).click()
  await expect.poll(async () => (await api(`/artifacts/${a.id}`)).version, { timeout: 15_000 }).toBe(3)
  expect((await api(`/artifacts/${a.id}`)).code).toContain('before')
})

test('the artifact iframe is not reloaded when the message list re-renders', async ({ grain }) => {
  const { page, app } = grain
  await makeViaTool(grain, 'Stable', PAGE('<p>stable</p>'))
  await expect.poll(() => !!artFrame(page), { timeout: 30_000 }).toBe(true)
  let navs = 0
  page.on('framenavigated', (f) => { if (/\/artifacts\/.+\/render/.test(f.url())) navs++ })
  // mark the document: a reload would drop this flag
  await artFrame(page).evaluate(() => { window.__marker = 'alive' })
  for (let i = 0; i < 3; i++) await reply(page, 'filler ' + i)
  await page.locator('.msg.assistant').first().scrollIntoViewIfNeeded()
  await smallWindow(app)
  await smallWindow(app, 1200, 800)
  await page.waitForTimeout(500)
  expect(navs).toBe(0)
  expect(await artFrame(page).evaluate(() => window.__marker)).toBe('alive')
  // and a Details toggle / taller preview does not reload it either
  await page.locator('.art-card').first().getByRole('button', { name: /Taller preview/ }).click()
  await page.locator('.art-details-toggle').first().click()
  await page.waitForTimeout(300)
  expect(navs).toBe(0)
  expect(await artFrame(page).evaluate(() => window.__marker)).toBe('alive')
})

test('a ~390 KB artifact loads; one past the limit is refused with a clear message', async ({ grain }) => {
  const { page, api } = grain
  const big = PAGE('<p id="end">end</p>', '').replace('</body>', '<!--' + 'x'.repeat(390_000) + '--></body>')
  const a = await api('/artifacts', { method: 'POST', body: { title: 'Big', code: big } })
  await newChat(page)
  await openPages(page)
  await expect(page.locator('.art-view.inline h2')).toHaveText('Big')
  await expect.poll(() => !!artFrame(page), { timeout: 30_000 }).toBe(true)
  await expect(artFrame(page).locator('#end')).toHaveText('end', { timeout: 30_000 })
  const r = await api('/artifacts', { method: 'POST', body: { title: 'Huge', code: 'y'.repeat(2 * 1024 * 1024) }, raw: true })
  expect(r.status).toBe(422)
  expect(await r.text()).toMatch(/limit/)
  expect(a.id).toBeTruthy()
})

test('a 2 MB html fence in a reply survives (code view, copy), and Save as artifact reports the limit', async ({ grain }) => {
  const { page, api } = grain
  // One message is bounded by the context window; widen it so a 2 MB reply can be sent at all.
  await api('/settings', { method: 'PUT', body: { contextWindow: 4_000_000 } })
  await page.reload()
  await newChat(page)
  const huge = '<p id="e">end</p><!--' + 'x'.repeat(2 * 1024 * 1024) + '-->'
  await page.getByRole('textbox', { name: 'Message' }).fill('!!reply ```html\n' + huge + '\n```')
  await page.getByRole('textbox', { name: 'Message' }).press('Enter')
  const block = page.locator('.art-fence').last()
  await expect(block).toBeVisible({ timeout: 90_000 })
  await expect(block.locator('iframe.art-fence-frame')).toBeVisible({ timeout: 60_000 })
  await block.getByRole('button', { name: 'Save as artifact' }).click()
  await expect(page.locator('.toast, [role="status"], [role="alert"]').filter({ hasText: /limit|large|characters/i }).first()).toBeVisible({ timeout: 20_000 })
})
