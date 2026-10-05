import { test, expect } from './fixtures.mjs'
import { enableModules, reload, realErrors } from './helpers/mah.mjs'

test.beforeEach(() => test.setTimeout(240_000))

const ymd = (daysAgo) => {
  const d = new Date(Date.now() - daysAgo * 86400000)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

async function say(page, text) {
  await page.getByRole('button', { name: /New chat/ }).first().click()
  const box = page.getByRole('textbox', { name: 'Message' })
  await box.fill(text)
  await box.press('Enter')
}

test('the assistant logs a reading: it lands labelled "assistant", shows on the tile and on Today', async ({ grain }) => {
  const { page, api } = grain
  await enableModules(api)
  // With the full tool list the model sees only core tools plus tool_search; 0 turns that deferral off.
  await api('/settings', { method: 'PUT', body: { toolDeferAbove: 0 } })
  await reload(page)
  await say(page, `!!tool health_log {"metric":"sleep","value":7.25,"day":"${ymd(0)}","note":"from chat"}`)
  await expect(page.locator('.msg.assistant').last()).toContainText('MOCK: tool done', { timeout: 60_000 })
  const entries = await api('/health/entries?metric=sleep')
  expect(entries).toHaveLength(1)
  expect(entries[0]).toMatchObject({ value: 7.25, source: 'assistant', note: 'from chat' })
  // the page shows it, with its provenance
  await page.getByRole('button', { name: 'Health', exact: true }).click()
  await expect(page.locator('.hl-tile', { has: page.locator('.hl-tile-label', { hasText: 'Sleep' }) }).locator('.hl-tile-value')).toHaveText('7.3 h')
  await expect(page.getByRole('region', { name: 'Sleep history' }).locator('.hl-entries')).toContainText('assistant')
  await expect(page.getByRole('region', { name: 'Sleep history' }).locator('.hl-entries')).toContainText('from chat')
  // a negative value is refused by the store, not stored
  await say(page, `!!tool health_log {"metric":"sleep","value":-4}`)
  await expect(page.locator('.msg.assistant').last()).toContainText('MOCK: tool done', { timeout: 60_000 })
  expect((await api('/health/entries?metric=sleep')).filter((e) => e.value < 0)).toHaveLength(0)
  // an unknown metric is refused too
  await say(page, `!!tool health_log {"metric":"nonsense_metric","value":3}`)
  await expect(page.locator('.msg.assistant').last()).toContainText('MOCK: tool done', { timeout: 60_000 })
  expect((await api('/health/metrics')).some((m) => m.key === 'nonsense_metric')).toBe(false)
  expect((await api('/health/entries?limit=50')).every((e) => e.metric !== 'nonsense_metric')).toBe(true)
  expect(realErrors(grain.consoleErrors)).toEqual([])
})

test('the page-agent context for Health names today and every metric, so questions can be grounded', async ({ grain }) => {
  const { page, api, llm } = grain
  await enableModules(api)
  await api('/health/entries', { method: 'POST', body: { metric: 'steps', value: 4200, day: ymd(0) } })
  await reload(page)
  await page.getByRole('button', { name: 'Health', exact: true }).click()
  await expect(page.locator('.hl-tile').first()).toBeVisible()
  await page.getByRole('button', { name: 'Ask about this page' }).click()
  const box = page.getByRole('textbox', { name: 'Message' }).last()
  await box.fill('how am I doing?')
  await box.press('Enter')
  await expect.poll(() => llm.calls.some((c) => JSON.stringify(c.messages).includes('how am I doing')), { timeout: 60_000 }).toBe(true)
  const sys = JSON.stringify(llm.calls.find((c) => JSON.stringify(c.messages).includes('how am I doing')).messages)
  expect(sys).toContain('4,200 steps')
  expect(sys).toContain('Steps')
  expect(realErrors(grain.consoleErrors)).toEqual([])
})
