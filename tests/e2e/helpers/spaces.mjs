// Shared helpers for the Spaces specs.
export const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

/** Click a native application-menu item by label (global accelerators cannot be pressed from the page). */
export async function menuClick(app, label) {
  const ok = await app.evaluate(({ Menu }, label) => {
    const find = (items) => {
      for (const it of items) {
        if (it.label === label && it.enabled !== false) return it
        if (it.submenu) { const f = find(it.submenu.items); if (f) return f }
      }
    }
    const it = find(Menu.getApplicationMenu().items)
    if (!it) return false
    it.click()
    return true
  }, label)
  if (!ok) throw new Error('no menu item ' + label)
}

export const spaces = (g) => g.api('/canvases')
export const windowsOf = async (g, id) => (await g.api('/canvases/' + id)).windows

/** Enter the canvas view in the first space. */
export async function enterCanvas(g) {
  await g.page.waitForSelector('.sidebar')
  await sleep(500)
  // The menu IPC can land before the renderer listens; toggle again only while the bar is still absent.
  for (let i = 0; i < 4 && !(await g.page.locator('.spaces-bar').count()); i++) {
    await menuClick(g.app, 'Toggle Spaces')
    await g.page.locator('.spaces-bar').waitFor({ timeout: 3000 }).catch(() => {})
  }
  await g.page.locator('.spaces-bar').waitFor()
}

/** Pin zoom/pan back to 1/0 through the API (gestures drift). */
export async function resetView(g, id) {
  await g.api('/canvases/' + id, { method: 'PUT', body: { zoom: 1, pan_x: 0, pan_y: 0 } })
}

/** The ⋯ button of a sidebar space row, by exact space name. */
export const actionsBtn = (page, name) => page.getByRole('button', { name: `Actions for ${name}`, exact: true })
