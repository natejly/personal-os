// Regression: ISSUE-013 — doc window saved an empty body after a quick toggle
// Found by /qa on 2026-10-06
// Report: .gstack/qa-reports/run-20261006T212932Z/
import { test, expect } from './fixtures.mjs'
import { patient, mkDoc } from './helpers/files.mjs'
import { enterCanvas, spaces } from './helpers/spaces.mjs'

test.describe.configure({ timeout: 300_000 })
test.beforeEach(({ grain }) => patient(grain))

// Leaving the editor (or Cmd+S) saves at once; the still-armed debounce timer must not then write an empty body.
for (const how of ['Show rendered', 'Cmd+S']) {
  test(`doc window keeps typed text when ${how} follows typing immediately`, async ({ grain: g }) => {
    const { page } = g
    const d = await mkDoc(g, { title: 'Quick', content: '# T\n\nbody\n' })
    const s = (await spaces(g))[0]
    await g.api(`/canvases/${s.id}/windows`, { method: 'POST', body: { kind: 'doc', ref_id: d.id, x: 40, y: 60, w: 420, h: 320 } })
    await page.reload()
    await enterCanvas(g)
    const w = page.locator('.widget', { hasText: 'Quick' }).first()
    await w.getByRole('button', { name: 'Edit raw' }).click()
    await w.locator('textarea.md-input').click()
    await page.keyboard.press('Meta+End')
    await page.keyboard.type('\nTYPED-LAST-WORDS')
    if (how === 'Cmd+S') await page.keyboard.press('Meta+s')
    else await w.getByRole('button', { name: 'Show rendered' }).click()
    await page.waitForTimeout(2500) // past the 700 ms debounce
    const saved = (await g.api(`/docs/${d.id}`)).content
    expect(saved).toContain('TYPED-LAST-WORDS')
    expect(saved).toContain('body')
  })
}
