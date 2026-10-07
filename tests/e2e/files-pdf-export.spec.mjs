import { statSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { test, expect } from './fixtures.mjs'
import { openFiles, mkDoc, errorsOf, patient, titleBox } from './helpers/files.mjs'

test.describe.configure({ timeout: 300_000 })
test.beforeEach(({ grain }) => patient(grain))

const CONTENT = '# Heading one\n\nSome text with math $x^2 + y^2 = z^2$.\n\n$$\\int_0^1 x\\,dx = \\tfrac12$$\n\n```python\nprint("hello pdf")\n```\n\n| a | b |\n|---|---|\n| 1 | 2 |\n'

const openDoc = async (g, title) => {
  await mkDoc(g, { title, content: CONTENT })
  await openFiles(g.page)
  await g.page.locator('.doc-row', { hasText: title }).last().click()
  await expect(titleBox(g.page)).toHaveValue(title)
  await g.page.getByTitle('Export').click()
}

test('Download PDF asks where to save and writes the chosen path', async ({ grain: g }) => {
  // The native save sheet is stubbed to pick a path. showItemInFolder is only the toast's "Show in Finder".
  const dest = join(g.downloadsDir, 'Pset 1.pdf')
  await g.app.evaluate(({ dialog, shell }, filePath) => {
    dialog.showSaveDialog = async () => ({ canceled: false, filePath })
    shell.showItemInFolder = () => undefined
  }, dest)
  await openDoc(g, 'Pset 1')
  await g.page.getByRole('menuitem', { name: 'Download PDF' }).click()
  await expect.poll(() => { try { return statSync(dest).size } catch { return 0 } }, { timeout: 60_000 }).toBeGreaterThan(1024)
  expect(readFileSync(dest).subarray(0, 5).toString()).toBe('%PDF-')
  await expect(g.page.getByText('Saved Pset 1.pdf')).toBeVisible()
  expect(errorsOf(g)).toEqual([])
})

test('Export PDF to Uploads files the PDF as an upload', async ({ grain: g }) => {
  await openDoc(g, 'Pset 2')
  await g.page.getByRole('menuitem', { name: 'Export PDF to Uploads' }).click()
  await expect.poll(async () => (await g.api('/documents')).filter((d) => d.name === 'Pset 2.pdf').length, { timeout: 60_000 }).toBe(1)
  const [d] = (await g.api('/documents')).filter((x) => x.name === 'Pset 2.pdf')
  expect(d.size).toBeGreaterThan(1024)
})
