import { test, expect } from './fixtures.mjs'
import { newChat, reply } from './helpers/blocks.mjs'

const PAGE = [
  '<canvas id="c" width="200" height="120"></canvas>',
  '<script>',
  "const c = document.getElementById('c').getContext('2d')",
  "c.fillStyle = '#ff0000'",
  'c.fillRect(0, 0, 200, 120)',
  "document.title = 'drawn'",
  '</script>'
].join('\n')

test('a script in an html fence runs in an isolated frame that cannot reach the app or the backend', async ({ grain }) => {
  const page = grain.page
  await newChat(page)
  await reply(page, 'Here it is:\n\n```html\n' + PAGE + '\n```')
  const msg = page.locator('.msg.assistant').last()
  const iframe = msg.locator('iframe.art-fence-frame')
  await expect(iframe).toHaveAttribute('sandbox', 'allow-scripts')
  await expect(iframe).toHaveAttribute('src', /^grain-preview:\/\/doc\//)

  let frame
  await expect.poll(() => (frame = page.frames().find((f) => f.url().startsWith('grain-preview://'))) !== undefined, { timeout: 15_000 }).toBe(true)
  await expect.poll(() => frame.evaluate(() => document.title), { timeout: 10_000 }).toBe('drawn')

  // The script ran: the canvas is solid red, and the old notice is gone.
  const px = await frame.evaluate(() => Array.from(document.getElementById('c').getContext('2d').getImageData(10, 10, 1, 1).data))
  expect(px).toEqual([255, 0, 0, 255])
  expect(await frame.evaluate(() => document.body.innerText)).not.toContain('Scripts are blocked')
  await expect(page.locator('.art-fence-note')).toHaveCount(0)

  // Isolation: opaque origin, no parent DOM, no preload bridge, no network to the sidecar.
  const probe = await frame.evaluate(async (health) => ({
    origin: window.origin,
    parent: (() => { try { return window.parent.document && 'reached' } catch (e) { return 'blocked' } })(),
    os: typeof window.os,
    fetch: await fetch(health).then(() => 'ok', (e) => `${e.name}: ${e.message}`)
  }), grain.backend.url + '/health')
  console.log('probe', JSON.stringify(probe), 'pixel', JSON.stringify(px))
  expect(probe.origin).toBe('null')
  expect(probe.parent).toBe('blocked')
  expect(probe.os).toBe('undefined')
  expect(probe.fetch).toMatch(/^TypeError/)

  // The reporter sized the frame.
  // The reporter sized the frame to the 120px canvas plus padding: below the 320px default, never 0.
  await expect.poll(async () => iframe.evaluate((el) => parseInt(el.style.height, 10) || 0), { timeout: 5_000 }).toBeGreaterThanOrEqual(120)
  expect(await iframe.evaluate((el) => parseInt(el.style.height, 10))).toBeLessThan(320)

  await iframe.scrollIntoViewIfNeeded()
  await page.screenshot({ path: (process.env.CLAUDE_JOB_DIR || '/tmp') + '/tmp/html-preview-canvas.png' })
})
