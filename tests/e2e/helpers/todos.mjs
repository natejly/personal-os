export async function openTodos(page) {
  await page.getByRole('button', { name: 'Todos', exact: true }).first().click()
  await page.getByPlaceholder('Add a todo…').waitFor()
}
export const addBox = (page) => page.getByPlaceholder('Add a todo…')
export const seed = async (api, n, mk = (i) => ({ title: `bulk todo ${i}` }), conc = 20) => {
  const out = []
  for (let i = 0; i < n; i += conc) {
    out.push(...(await Promise.all(Array.from({ length: Math.min(conc, n - i) }, (_, j) => api('/todos', { method: 'POST', body: mk(i + j) })))))
  }
  return out
}
export const dayStr = (off = 0) => {
  const d = new Date(); d.setDate(d.getDate() + off)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}
export const ignoreErrs = (errs) => errs.filter((e) => !/favicon|DevTools/.test(e))
export const row = (page, title) => page.locator('.todo', { hasText: title }).first()
