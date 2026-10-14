// Saved html dashboards run inline scripts in a frame with no network, no storage and no way out.
import { test, expect } from './fixtures.mjs'
import { newChat, say } from './helpers/blocks.mjs'
import http from 'node:http'
import net from 'node:net'
import { readFileSync } from 'node:fs'

const TABLE = '| day | kcal |\n| --- | --- |\n| mon | 1800 |\n| tue | 2000 |\n'

const dashFrame = async (page) => {
  let frame
  await expect.poll(() => (frame = page.frames().find((f) => f.url().startsWith('grain-preview://dash/'))) !== undefined, { timeout: 15_000 }).toBe(true)
  return frame
}

async function counter() {
  const hits = []
  const server = http.createServer((req, res) => { hits.push(req.url); res.writeHead(200, { 'Access-Control-Allow-Origin': '*' }); res.end('x') })
  await new Promise((r) => server.listen(0, '127.0.0.1', r))
  return { hits, url: `http://127.0.0.1:${server.address().port}`, close: () => server.close() }
}

// Raw TCP: even a failed TLS handshake against https://127.0.0.1:<port> counts as a connection.
async function tcpCounter() {
  const c = { n: 0 }
  const server = net.createServer((sock) => { c.n++; sock.on('error', () => {}); sock.destroy() })
  await new Promise((r) => server.listen(0, '127.0.0.1', r))
  return { c, url: `https://127.0.0.1:${server.address().port}`, close: () => server.close() }
}

async function saveAndOpen(grain, title, html, extra = {}) {
  await grain.api('/docs', { method: 'POST', body: { title: 'Food Log', content: TABLE } })
  await grain.api('/saved-dashboards', { method: 'POST', body: { title, format: 'html', html, bindings: [{ name: 'food', source: 'doc', doc: 'Food Log' }], ...extra } })
  await newChat(grain.page)
  await say(grain.page, '!!tool dashboard_open ' + JSON.stringify({ name: title }))
}

const READS = `
<p id="out">waiting</p><button id="b">go</button>
<script>
var d = JSON.parse(document.getElementById('grain-data').textContent)
document.getElementById('out').textContent = 'rows:' + d.food.length + ':' + d.food[1].kcal
document.getElementById('b').onclick = function () { document.getElementById('out').textContent = 'clicked' }
</script>`

test('an html dashboard script reads grain-data and handles clicks, inline and in the side panel', async ({ grain }) => {
  const page = grain.page
  await saveAndOpen(grain, 'Interactive', READS)
  const frame = await dashFrame(page)
  await expect(frame.locator('#out')).toHaveText('rows:2:2000')
  await frame.locator('#b').click()
  await expect(frame.locator('#out')).toHaveText('clicked')

  // Same dashboard in the side panel.
  await page.getByRole('button', { name: 'Open in side panel' }).first().click()
  const panel = page.getByRole('complementary', { name: 'Side panel' })
  await expect(panel.locator('iframe.art-fence-frame')).toHaveAttribute('src', /^grain-preview:\/\/dash\//)
  await expect.poll(() => page.frames().filter((f) => f.url().startsWith('grain-preview://dash/')).length).toBe(2)
  for (const f of page.frames().filter((f) => f.url().startsWith('grain-preview://dash/'))) await expect(f.locator('#out')).toHaveText(/rows:2:2000|clicked/)
  // No Save/Open buttons of its own inside a dashboard.
  await expect(page.locator('.dashboard-view .art-fence-actions button[aria-label*="Save"]')).toHaveCount(0)
})

test('a dashboard script has no network, storage, WebRTC or way to navigate out', async ({ grain }) => {
  const page = grain.page
  const spy = await counter()
  const tls = await tcpCounter()
  try {
    await grain.app.evaluate(({ shell }) => { globalThis.__opened = []; shell.openExternal = (u) => { globalThis.__opened.push(u); return Promise.resolve() } })
    const html = `<style>@font-face { font-family: x; src: url(${tls.url}/font) } body { font-family: x }</style>
<link rel="stylesheet" href="${tls.url}/css">
<script src="${tls.url}/script"></script>
<p id="out">waiting</p><script>
var r = {}, base = ${JSON.stringify(spy.url)}, sec = ${JSON.stringify(tls.url)}
function t(k, f) { try { r[k] = String(f()) } catch (e) { r[k] = 'threw:' + e.name } }
t('cookie', function () { return document.cookie })
t('ls', function () { localStorage.setItem('a', 'b'); return 'ok' })
t('rtc', function () { return typeof RTCPeerConnection + typeof webkitRTCPeerConnection })
t('img', function () { new Image().src = base + '/img'; return 'sent' })
t('imgs', function () { new Image().src = sec + '/img'; return 'sent' })
t('fetchs', function () { fetch(sec + '/fetch').catch(function () {}); return 'sent' })
Promise.all([
  fetch(base + '/fetch').then(function () { r.fetch = 'ok' }, function (e) { r.fetch = 'failed:' + e.name }),
  fetch('https://example.com').then(function () { r.ext = 'ok' }, function (e) { r.ext = 'failed:' + e.name })
]).then(function () {
  document.getElementById('out').textContent = JSON.stringify(r)
  setTimeout(function () { location.href = base + '/nav?d=1' }, 100)
})
</script>`
    await saveAndOpen(grain, 'Hostile', html)
    const frame = await dashFrame(page)
    await expect(frame.locator('#out')).toContainText('{', { timeout: 15_000 })
    const r = JSON.parse(await frame.locator('#out').textContent())
    expect(r.cookie === '' || r.cookie.startsWith('threw:')).toBe(true)
    expect(r.ls).toMatch(/^threw:/)
    expect(r.rtc).toBe('undefinedundefined')
    expect(r.fetch).toMatch(/^failed/)
    expect(r.ext).toMatch(/^failed/)
    await page.waitForTimeout(1500) // the image load and the location change had time to happen
    expect(spy.hits).toEqual([])
    expect(tls.c.n).toBe(0) // https: loads are blocked too, not only http:
    expect(await grain.app.evaluate(() => globalThis.__opened)).toEqual([])
  } finally { spy.close(); tls.close() }
})

test('a script-free html dashboard still renders, and a spec dashboard still draws', async ({ grain }) => {
  const page = grain.page
  await saveAndOpen(grain, 'Plain', '<h2 id="h">Total</h2>{{food}}<p id="last">{{food.kcal}}</p>')
  const frame = await dashFrame(page)
  await expect(frame.locator('#last')).toHaveText('2000')
  await expect(frame.locator('table')).toBeVisible()
  await grain.api('/saved-dashboards', { method: 'POST', body: { title: 'Spec', bindings: [{ name: 'food', source: 'doc', doc: 'Food Log' }], views: [{ kind: 'chart', spec: { type: 'line', x: 'day', series: ['kcal'], data: '$food' } }] } })
  await say(page, '!!tool dashboard_open {"name":"Spec"}')
  await expect(page.locator('.dashboard-view').last().locator('iframe')).toHaveCount(0)
  await expect(page.locator('.dashboard-view').last().locator('.chart-block, svg').first()).toBeVisible()
})

const METRICS = process.env.METRICS_HTML
test('a real one-file dashboard (day chips, 7/14 toggle) works', async ({ grain }) => {
  test.skip(!METRICS, 'set METRICS_HTML to a local file to run this')
  await saveAndOpen(grain, 'Metrics', readFileSync(METRICS, 'utf8'))
  const frame = await dashFrame(grain.page)
  await expect(frame.locator('.tbtn[data-window="7"]')).toHaveClass(/is-on/, { timeout: 15_000 })
  await frame.locator('.tbtn[data-window="14"]').click()
  await expect(frame.locator('.tbtn[data-window="14"]')).toHaveClass(/is-on/)
  await expect(frame.locator('.tbtn[data-window="7"]')).not.toHaveClass(/is-on/)
})
