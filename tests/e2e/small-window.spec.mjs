import { test, expect } from './fixtures.mjs'
import { enableModules, reload, small, realErrors } from './helpers/mah.mjs'

test.beforeEach(() => test.setTimeout(240_000))

const overflow = (page) => page.evaluate(() => {
  const wide = []
  for (const el of document.querySelectorAll('main *')) {
    const r = el.getBoundingClientRect()
    if (r.width > 0 && r.right > window.innerWidth + 1) {
      // inside its own scroller is fine
      let p = el.parentElement, scrolled = false
      while (p && p !== document.body) { const o = getComputedStyle(p).overflowX; if (o === 'auto' || o === 'scroll' || o === 'hidden') { scrolled = true; break } p = p.parentElement }
      if (!scrolled) wide.push(`${el.tagName}.${String(el.className).slice(0, 40)} right=${Math.round(r.right)}`)
    }
  }
  return { page: document.documentElement.scrollWidth - document.documentElement.clientWidth, wide: wide.slice(0, 5) }
})

test('Health page and its two panels fit 820x520', async ({ grain }) => {
  const { page, api, app } = grain
  await small(app)
  await enableModules(api)
  await reload(page)
  await page.getByRole('button', { name: 'Health', exact: true }).click()
  await expect(page.locator('.hl-tile').first()).toBeVisible()
  let o = await overflow(page)
  expect(o.page).toBeLessThanOrEqual(0)
  await page.getByRole('button', { name: 'Choose and edit metrics' }).click()
  await expect(page.getByRole('region', { name: 'Metrics' })).toBeVisible()
  o = await overflow(page)
  expect(o.page).toBeLessThanOrEqual(0)
  expect(o.wide).toEqual([])
  await page.getByRole('button', { name: 'Connected services' }).click()
  o = await overflow(page)
  expect(o.page).toBeLessThanOrEqual(0)
  expect(realErrors(grain.consoleErrors)).toEqual([])
})
