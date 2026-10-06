// Shared helpers for the Files view specs.
export const openFiles = async (page) => {
  await page.locator('.nav-item', { hasText: 'Files' }).first().click()
  await page.getByRole('tablist', { name: 'Files section' }).waitFor()
}
export const body = (page) => page.locator('textarea.md-input')
/** Docs open in the reading view; bring the editor up (Edit toggle) unless it already is, as a doc made from New or remembered per doc. */
export const editDoc = async (page) => {
  const t = page.locator('.doc-edit-toggle')
  if ((await t.getAttribute('aria-pressed')) !== 'true') await t.click()
  await body(page).waitFor()
}
export const titleBox = (page) => page.getByLabel('Title', { exact: true })
export const waitSaved = async (page) => {
  await page.waitForFunction(() => !document.querySelector('.doc-save-state'), null, { timeout: 60_000 })
}
export const smallWindow = (g) => g.app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].setSize(820, 520))
export const mkDoc = (g, d = {}) => g.api('/docs', { method: 'POST', body: { title: 'Untitled', content: '', folder: '', project_id: null, ...d } })
export const benign = /favicon|ResizeObserver/
export const errorsOf = (g) => g.consoleErrors.filter((e) => !benign.test(e))
// The e2e box is shared and often at load 30+: give every wait more room than the harness default of 15 s.
export const patient = (g) => { g.page.setDefaultTimeout(60_000) }
export const relaunch = async (g) => { await g.relaunch(); patient(g); return g.page }
export const newDoc = (page) => page.locator('.page-header .newdoc-main')
/** Real pointer drag: press on src, move over dst in steps, release. HTML5 drag-and-drop starts from this in Chromium. */
export const drag = async (page, src, dst) => {
  const a = await src.boundingBox()
  const b = await dst.boundingBox()
  await page.mouse.move(a.x + 20, a.y + a.height / 2)
  await page.mouse.down()
  await page.mouse.move(a.x + 30, a.y + a.height / 2 + 4, { steps: 4 })
  await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2, { steps: 12 })
  await page.mouse.move(b.x + b.width / 2 + 1, b.y + b.height / 2, { steps: 2 })
  await page.mouse.up()
}
/** Fire an app-menu accelerator (e.g. 'CmdOrCtrl+K'): menu accelerators never reach the page from synthetic keys. */
export const menu = (g, accel) => g.app.evaluate(({ Menu }, a) => {
  const walk = (items) => { for (const it of items) { if (it.accelerator === a) { it.click(); return true } if (it.submenu && walk(it.submenu.items)) return true } return false }
  return walk(Menu.getApplicationMenu().items)
}, accel)
