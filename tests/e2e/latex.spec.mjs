import { execFileSync } from 'node:child_process'
import { test, expect } from './fixtures.mjs'
import { openFiles, mkDoc, errorsOf, patient } from './helpers/files.mjs'

test.describe.configure({ timeout: 300_000 })
const HAVE_LATEXMK = (() => { try { execFileSync('which', ['latexmk'], { stdio: 'ignore' }); return true } catch { return false } })()
const NEEDS_LATEXMK = 'latexmk is not installed'
test.beforeEach(({ grain }) => patient(grain))

const cm = (page) => page.locator('.lx-editor .cm-content')
const fileBtn = (page, name) => page.locator('.lx-files button', { hasText: name })
const MAIN = '\\documentclass{article}\n\\begin{document}\n\\input{intro}\n\\end{document}\n'

async function project(g) {
  const main = await mkDoc(g, { title: 'main.tex', folder: 'thesis', content: MAIN })
  const intro = await mkDoc(g, { title: 'intro.tex', folder: 'thesis', content: 'INTRO BODY' })
  await mkDoc(g, { title: 'refs.bib', folder: 'thesis', content: '@book{a,title={T}}' })
  return { main, intro }
}

test('switching between the project files: file list, Files tree and a problem jump', async ({ grain: g }) => {
  const { page } = g
  await project(g)
  await openFiles(page)
  await page.locator('.doc-folder-name', { hasText: 'thesis' }).click()
  await page.locator('.doc-tree .doc-row', { hasText: 'main.tex' }).last().click()
  await expect(cm(page)).toContainText('documentclass')

  // the editor's own file list
  await fileBtn(page, 'intro.tex').click()
  await expect(cm(page)).toContainText('INTRO BODY')
  await expect(fileBtn(page, 'intro.tex')).toHaveClass(/active/)
  await fileBtn(page, 'refs.bib').click()
  await expect(cm(page)).toContainText('@book')
  await fileBtn(page, 'main.tex').click()
  await expect(cm(page)).toContainText('documentclass')

  // the Files tree while main.tex is open
  await page.locator('.doc-tree .doc-row', { hasText: 'intro.tex' }).last().click()
  await expect(cm(page)).toContainText('INTRO BODY')
  await expect(fileBtn(page, 'intro.tex')).toHaveClass(/active/)

  // switching saves the file you leave and never writes it into the next one
  await cm(page).click()
  await page.keyboard.insertText('EDITED ')
  await fileBtn(page, 'main.tex').click()
  await expect(cm(page)).toContainText('documentclass')
  await expect(cm(page)).not.toContainText('EDITED')
  const docs = await g.api('/docs')
  const body = async (t) => (await g.api(`/docs/${docs.find((d) => d.title === t).id}`)).content
  expect(await body('intro.tex')).toContain('EDITED')
  expect(await body('main.tex')).toBe(MAIN)

  expect(errorsOf(g)).toEqual([])
})

test('a problem in another file opens that file at its line; CRLF files switch too', async ({ grain: g }) => {
  test.skip(!HAVE_LATEXMK, NEEDS_LATEXMK)
  const { page } = g
  const main = await mkDoc(g, { title: 'main.tex', folder: 'paper', content: '\\documentclass{article}\r\n\\begin{document}\r\n\\input{intro}\r\n\\end{document}\r\n' })
  await mkDoc(g, { title: 'intro.tex', folder: 'paper', content: 'ok line\r\n\\undefinedmacro\r\nmore\r\n' })
  await openFiles(page)
  await page.locator('.doc-folder-name', { hasText: 'paper' }).click()
  await page.locator('.doc-tree .doc-row', { hasText: 'main.tex' }).last().click()
  await expect(cm(page)).toContainText('documentclass')
  // opening a CRLF file is not an edit: no unsaved dot, nothing rewritten
  await page.waitForTimeout(2500)
  await expect(page.locator('.doc-save-state')).toHaveCount(0)
  expect((await g.api(`/docs/${main.id}`)).content).toContain('\r\n')
  await fileBtn(page, 'intro.tex').click()
  await expect(cm(page)).toContainText('undefinedmacro')
  await fileBtn(page, 'main.tex').click()
  await expect(cm(page)).toContainText('documentclass')

  await page.getByRole('button', { name: /Compile/ }).click()
  const problem = page.locator('.lx-problems li button', { hasText: 'intro.tex' }).first()
  await expect(problem).toBeVisible({ timeout: 120_000 })
  await problem.click()
  await expect(cm(page)).toContainText('undefinedmacro')
  await expect(fileBtn(page, 'intro.tex')).toHaveClass(/active/)
  await expect(page.locator('.lx-editor .cm-activeLine')).toContainText('undefinedmacro')
  expect(errorsOf(g)).toEqual([])
})

test('New file in the file list: unique default, suffix, subfolder, bad names refused; \\input compiles', async ({ grain: g }) => {
  test.skip(!HAVE_LATEXMK, NEEDS_LATEXMK)
  const { page } = g
  await mkDoc(g, { title: 'main.tex', folder: 'book', content: '\\documentclass{article}\n\\begin{document}\n\\input{intro}\n\\input{chapters/two}\n\\end{document}\n' })
  await openFiles(page)
  await page.locator('.doc-folder-name', { hasText: 'book' }).click()
  await page.locator('.doc-tree .doc-row', { hasText: 'main.tex' }).last().click()
  await expect(cm(page)).toContainText('documentclass')
  const name = page.getByLabel('New file name')

  await page.getByRole('button', { name: 'New file', exact: true }).click()
  await expect(name).toHaveValue('untitled.tex')
  await page.keyboard.press('Escape')
  await expect(name).toHaveCount(0)

  // a bad name is refused and nothing is made
  await page.getByRole('button', { name: 'New file', exact: true }).click()
  await name.fill('../evil')
  await name.press('Enter')
  await name.fill('-x.tex')
  await name.press('Enter')
  await expect(name).toBeVisible()
  expect((await g.api('/docs')).filter((d) => /evil|-x/.test(d.title))).toEqual([])
  await name.fill('intro') // no suffix: .tex is appended
  await name.press('Enter')
  await expect(fileBtn(page, 'intro.tex')).toHaveClass(/active/)
  await expect(page.getByLabel('File name')).toHaveValue('intro.tex')
  let docs = await g.api('/docs')
  expect(docs.find((d) => d.title === 'intro.tex').folder).toBe('book')
  await expect(page.locator('.doc-tree .doc-row', { hasText: 'intro.tex' }).last()).toBeVisible()

  await cm(page).click()
  await page.keyboard.insertText('Intro text.')
  await page.getByRole('button', { name: 'New file', exact: true }).click()
  await name.fill('chapters/two.tex')
  await name.press('Enter')
  await expect(fileBtn(page, 'chapters/two.tex')).toHaveClass(/active/)
  docs = await g.api('/docs')
  expect(docs.find((d) => d.title === 'two.tex').folder).toBe('book/chapters')
  expect((await g.api(`/docs/${docs.find((d) => d.title === 'intro.tex').id}`)).content).toBe('Intro text.') // saved before switching
  await cm(page).click()
  await page.keyboard.insertText('Two text.')

  await fileBtn(page, 'main.tex').click()
  await page.getByRole('button', { name: /Compile/ }).click()
  await expect(page.locator('.lx-status')).toContainText('OK', { timeout: 120_000 })
  expect(errorsOf(g)).toEqual([])
})

test('Upload files: a .tex becomes an editable doc, a .png stays an asset, and both compile', async ({ grain: g }) => {
  test.skip(!HAVE_LATEXMK, NEEDS_LATEXMK)
  const { page } = g
  await mkDoc(g, { title: 'main.tex', folder: 'up', content: '\\documentclass{article}\n\\usepackage{graphicx}\n\\begin{document}\n\\input{intro}\n\\includegraphics[width=1cm]{fig}\n\\end{document}\n' })
  await openFiles(page)
  await page.locator('.doc-folder-name', { hasText: 'up' }).click()
  await page.locator('.doc-tree .doc-row', { hasText: 'main.tex' }).last().click()
  await expect(cm(page)).toContainText('documentclass')
  const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==', 'base64')
  await page.locator('.lx-side input[type=file]').setInputFiles([
    { name: 'intro.tex', mimeType: 'text/plain', buffer: Buffer.from('Uploaded intro.\n') },
    { name: 'fig.png', mimeType: 'image/png', buffer: png }
  ])
  await expect(fileBtn(page, 'intro.tex')).toBeVisible()
  await expect(page.locator('.lx-asset', { hasText: 'fig.png' })).toBeVisible()
  await expect(page.locator('.doc-tree .doc-row', { hasText: 'intro.tex' }).last()).toBeVisible()
  await fileBtn(page, 'intro.tex').click()
  await expect(cm(page)).toContainText('Uploaded intro.')
  await cm(page).click()
  await page.keyboard.insertText('Edited. ')
  await fileBtn(page, 'main.tex').click()
  await expect(cm(page)).toContainText('documentclass')
  const docs = await g.api('/docs')
  expect((await g.api(`/docs/${docs.find((d) => d.title === 'intro.tex').id}`)).content).toContain('Edited.')
  await page.getByRole('button', { name: /Compile/ }).click()
  await expect(page.locator('.lx-status')).toContainText('OK', { timeout: 120_000 })
  expect(errorsOf(g)).toEqual([])
})

for (const width of [1160, 900]) {
  test(`narrow window ${width}px: compile button and PDF pane stay on screen`, async ({ grain: g }) => {
    const { page } = g
    await g.app.evaluate(({ BrowserWindow }, w) => BrowserWindow.getAllWindows()[0].setSize(w, 700), width)
    await mkDoc(g, { title: 'main.tex', folder: 'thesis', content: MAIN })
    await openFiles(page)
    await page.locator('.doc-folder-name', { hasText: 'thesis' }).click()
    await page.locator('.doc-tree .doc-row', { hasText: 'main.tex' }).last().click()
    await expect(page.locator('.lx-preview')).toBeVisible()
    const vw = await page.evaluate(() => window.innerWidth)
    for (const sel of ['.lx-files', '.lx-editor', '.lx-preview', '.lx-toolbar button', '.lx-toolbar select']) {
      const b = await page.locator(sel).first().boundingBox()
      expect(b.x + b.width, sel).toBeLessThanOrEqual(vw + 1)
      expect(b.width, sel).toBeGreaterThan(20)
    }
  })
}
