import { test, expect } from './fixtures.mjs'
import { newChat, say, reply, smallWindow, realErrors } from './helpers/chat.mjs'

const fence = (lang, body) => '```' + lang + '\n' + body + '\n```'
const last = (page) => page.locator('.msg.assistant').last()
const fenceFrame = (page) => page.frames().find((f) => f !== page.mainFrame() && !/\/artifacts\//.test(f.url()) && f.parentFrame() === page.mainFrame())

async function start(grain, settings = {}) {
  await grain.api('/settings', { method: 'PUT', body: { toolDeferAbove: 0, ...settings } })
  await grain.page.reload()
  await newChat(grain.page)
  return grain.page
}

test('an html fence previews in a sandboxed srcdoc frame with a CSP, never in the app origin', async ({ grain }) => {
  const page = await start(grain)
  await page.evaluate(() => { window.__pwn = 0 })
  const html = '<h1 id="h">Fence page</h1><button id="b" onclick="document.title=1">x</button><script>window.top.__pwn = 1; try { parent.__pwn = 2 } catch (e) {}</script>'
  await reply(page, fence('html', html))
  const block = last(page).locator('.art-fence')
  await expect(block).toBeVisible({ timeout: 20_000 })
  const frame = block.locator('iframe.art-fence-frame')
  await expect(frame).toHaveAttribute('sandbox', 'allow-scripts')
  await expect(frame).not.toHaveAttribute('sandbox', /allow-same-origin/)
  await expect(block.locator('.art-fence-note')).toContainText(/Scripts are blocked/)
  await expect.poll(() => !!fenceFrame(page), { timeout: 15_000 }).toBe(true)
  await expect(fenceFrame(page).locator('#h')).toHaveText('Fence page')
  const probe = await fenceFrame(page).evaluate(() => ({ os: typeof window.os, csp: document.querySelector('meta[http-equiv="Content-Security-Policy"]')?.getAttribute('content') || '' }))
  expect(probe.os).toBe('undefined')
  expect(probe.csp).toContain("connect-src 'none'")
  expect(await page.evaluate(() => window.__pwn)).toBe(0)
  // Code / Preview toggle and copy
  await block.getByRole('tab', { name: /Code/ }).click()
  await expect(block.locator('pre code')).toContainText('Fence page')
  await block.getByRole('tab', { name: /Preview/ }).click()
  await expect(frame).toBeVisible()
  expect(realErrors(grain).filter((e) => !/Content Security Policy|Blocked script|Refused to execute/i.test(e))).toEqual([])
})

test('an svg fence draws as an image and its scripts are refused', async ({ grain }) => {
  const page = await start(grain)
  await page.evaluate(() => { window.__pwn = 0 })
  await reply(page, fence('svg', '<svg xmlns="http://www.w3.org/2000/svg" width="80" height="80"><circle cx="40" cy="40" r="30" fill="red"/><script>window.top.__pwn=1</script></svg>'))
  const block = last(page).locator('.art-fence')
  await expect(block.locator('iframe.art-fence-frame.svg')).toBeVisible({ timeout: 20_000 })
  await expect.poll(() => !!fenceFrame(page), { timeout: 15_000 }).toBe(true)
  await expect(fenceFrame(page).locator('circle')).toBeVisible()
  expect(await page.evaluate(() => window.__pwn)).toBe(0)
})

test('Save as artifact files it once (double click), the Pages view lists it, and it renders with its script', async ({ grain }) => {
  const page = await start(grain)
  await reply(page, fence('html', '<!doctype html><title>Saved page</title><h1 id="h">from fence</h1><script>document.getElementById("h").textContent = "script ran"</script>'))
  const save = last(page).locator('.art-fence').getByRole('button', { name: 'Save as artifact' })
  await save.dblclick()
  await expect.poll(async () => (await grain.api('/artifacts')).length, { timeout: 15_000 }).toBe(1)
  await page.waitForTimeout(500)
  expect((await grain.api('/artifacts')).length).toBe(1)
  const [a] = await grain.api('/artifacts')
  expect(a.title).toBe('Saved page')
  await page.locator('.nav-item', { hasText: /Files/ }).first().click()
  await page.getByRole('tab', { name: 'Pages' }).click()
  await expect(page.locator('.art-row')).toHaveCount(1)
  await expect.poll(() => page.frames().find((f) => /\/artifacts\/.+\/render/.test(f.url())) ? 1 : 0, { timeout: 20_000 }).toBe(1)
  const f = page.frames().find((fr) => /\/artifacts\/.+\/render/.test(fr.url()))
  await expect(f.locator('#h')).toHaveText('script ran')
})

test('a malformed or empty html fence does not crash the message', async ({ grain }) => {
  const page = await start(grain)
  await reply(page, fence('html', '<div><span>unclosed <b>tags'))
  await expect(last(page).locator('.art-fence')).toBeVisible({ timeout: 20_000 })
  await reply(page, fence('html', ''))
  await expect(last(page)).toBeVisible()
  await expect(page.getByText(/Something went wrong|Minified React/i)).toHaveCount(0)
  expect(realErrors(grain)).toEqual([])
})

test('artifact_update adds a version; the card survives a relaunch with a fresh signed URL; deleting it leaves a friendly note', async ({ grain }) => {
  const page = await start(grain)
  await say(page, '!!tool artifact_create ' + JSON.stringify({ title: 'Durable', html: '<!doctype html><h1 id="h">v1</h1>' }))
  const [a] = await grain.api('/artifacts')
  await say(page, '!!tool artifact_update ' + JSON.stringify({ artifact_id: a.id, html: '<!doctype html><h1 id="h">v2</h1>', instruction: 'bump' }))
  await expect.poll(async () => (await grain.api(`/artifacts/${a.id}`)).version, { timeout: 20_000 }).toBe(2)
  await grain.relaunch()
  const p2 = grain.page
  await p2.locator('.convo-list .convo-item').first().click()
  await expect(p2.locator('.art-card').first()).toBeVisible({ timeout: 30_000 })
  await expect.poll(() => p2.frames().find((f) => /\/artifacts\/.+\/render/.test(f.url())) ? 1 : 0, { timeout: 30_000 }).toBe(1)
  await expect(p2.frames().find((f) => /\/artifacts\/.+\/render/.test(f.url())).locator('#h')).toHaveText(/v[12]/)
  await grain.api(`/artifacts/${a.id}`, { method: 'DELETE' })
  await p2.reload()
  await p2.locator('.convo-list .convo-item').first().click()
  await expect(p2.getByText(/This artifact was deleted/).first()).toBeVisible({ timeout: 30_000 })
})

test('the Pages view survives 150 artifacts and an unreachable backend', async ({ grain }) => {
  const { page, api } = grain
  for (let i = 0; i < 150; i++) await api('/artifacts', { method: 'POST', body: { title: 'Art ' + i, code: `<!doctype html><h1>${i}</h1>` } })
  await page.locator('.nav-item', { hasText: /Files/ }).first().click()
  await page.getByRole('tab', { name: 'Pages' }).click()
  await expect(page.locator('.art-row')).toHaveCount(150, { timeout: 30_000 })
  await page.getByLabel('Search artifacts').fill('Art 14')
  await expect(page.locator('.art-row')).toHaveCount(11) // Art 14, Art 140..149
  await smallWindow(grain.app)
  await page.getByLabel('Search artifacts').fill('')
  grain.backend.child.kill('SIGKILL')
  await page.getByLabel('Search artifacts').fill('zzz')
  await expect(page.getByRole('alert').filter({ hasText: /Could not load artifacts/ })).toBeVisible({ timeout: 20_000 })
  await expect(page.getByText('No artifacts yet.')).toHaveCount(0)
  await expect(page.getByText(/Something went wrong|Minified React/i)).toHaveCount(0)
})
