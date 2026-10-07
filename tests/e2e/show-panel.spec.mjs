// The chat's side panel: the `show` tool opens it on model content or a file from this Mac, a fenced block
// in a reply can be promoted into it, and a closed panel reopens from the tool row.
import { test, expect } from '@playwright/test'
import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { launchApp } from './harness.mjs'
import { newChat, say, reply, realErrors } from './helpers/blocks.mjs'

const PDF = `%PDF-1.4
1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj
2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj
3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 200]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj
4 0 obj<</Length 60>>stream
BT /F1 36 Tf 30 100 Td (HELLO PDF) Tj ET
endstream
endobj
5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj
trailer<</Root 1 0 R>>`

const panel = (page) => page.getByRole('complementary', { name: 'Side panel' })
const showTool = (page, args) => say(page, '!!tool show ' + JSON.stringify(args))
/** A `show` call needs nothing from the user, so its row is folded under the reply's "1 tool call" line. */
async function openFold(page) {
  const last = page.locator('.msg.assistant').last()
  await last.getByRole('button', { name: /tool call/ }).click()
  return last.locator('.tool-event').last()
}

test('show opens the panel on html, then a PDF from this Mac in the built-in viewer; it reopens from the tool row', async () => {
  // The backend's home folder is a scratch one, so the file guard admits the test's PDF and nothing of the user's.
  const home = mkdtempSync(join(tmpdir(), 'grain-show-home-'))
  mkdirSync(join(home, 'Documents'))
  writeFileSync(join(home, 'Documents', 'lease.pdf'), PDF)
  const grain = await launchApp({ name: 'show-panel', backendEnv: { HOME: home } })
  try {
    const { page } = grain
    await newChat(page)
    await showTool(page, { kind: 'html', content: '<h1 id="hi">Hello panel</h1>', title: 'Mock-up' })
    await expect(panel(page)).toBeVisible()
    await expect(panel(page).getByRole('heading', { name: 'Mock-up' })).toBeVisible()
    // Model HTML renders in the same sandboxed frame as an inline ```html block.
    const frame = panel(page).frameLocator('iframe[title="HTML preview"]')
    await expect(frame.locator('#hi')).toHaveText('Hello panel')
    await expect(await openFold(page)).toContainText('Show in side panel')

    await showTool(page, { kind: 'file', path: '~/Documents/lease.pdf', title: 'Lease' })
    await expect(panel(page).getByRole('heading', { name: 'Lease' })).toBeVisible()
    const pdf = panel(page).locator('iframe.show-pdf')
    await expect(pdf).toHaveAttribute('src', /^blob:/)
    // The navigation guard let the renderer's own blob through: the frame committed and holds the PDF.
    await expect.poll(() => grain.app.evaluate(({ BrowserWindow }) =>
      BrowserWindow.getAllWindows()[0].webContents.mainFrame.frames.map((f) => f.url).filter((u) => u.startsWith('blob:')).length
    )).toBeGreaterThan(0)
    const type = await grain.app.evaluate(({ BrowserWindow }) => {
      const f = BrowserWindow.getAllWindows()[0].webContents.mainFrame.frames.find((x) => x.url.startsWith('blob:'))
      return f ? f.executeJavaScript('document.querySelector("embed")?.type || document.contentType') : null
    })
    expect(type).toBe('application/pdf')

    await panel(page).getByRole('button', { name: 'Close side panel' }).click()
    await expect(panel(page)).toHaveCount(0)
    const row = await openFold(page)
    await row.getByRole('button', { name: 'Open in side panel' }).click()
    await expect(panel(page).getByRole('heading', { name: 'Lease' })).toBeVisible()
    expect(realErrors(grain)).toEqual([])
  } finally {
    await grain.close()
  }
})

test('a credential store is refused, and the panel stays closed; a plain file outside the home folder opens', async () => {
  // Whole-Mac scope is the default, so the guard is about what a file is: keys and credential files never reach the panel.
  const home = mkdtempSync(join(tmpdir(), 'grain-show-guard-'))
  mkdirSync(join(home, '.aws'))
  writeFileSync(join(home, '.aws', 'credentials'), '[default]\nplaceholder = not a real credential\n')
  const grain = await launchApp({ name: 'show-panel-guard', backendEnv: { HOME: home } })
  try {
    const { page } = grain
    await newChat(page)
    await showTool(page, { kind: 'file', path: '~/.aws/credentials' })
    await expect(await openFold(page)).toContainText(/credential/)
    await expect(panel(page)).toHaveCount(0)
    await showTool(page, { kind: 'file', path: '/etc/hosts' })
    await expect(panel(page)).toBeVisible()
  } finally {
    await grain.close()
  }
})

test('a mermaid block in a reply is promoted to the panel from its header', async () => {
  const grain = await launchApp({ name: 'show-panel-fence' })
  try {
    const { page } = grain
    await newChat(page)
    await reply(page, '```mermaid\ngraph TD\n  A[Start] --> B[Done]\n```')
    const block = page.locator('.msg.assistant').last().locator('.chart-block.mermaid')
    await expect(block.locator('.mermaid-svg svg')).toBeVisible({ timeout: 60_000 })
    await block.getByRole('button', { name: 'Open in side panel' }).click()
    await expect(panel(page).getByRole('heading', { name: 'Diagram' })).toBeVisible()
    await expect(panel(page).locator('.mermaid-svg svg')).toBeVisible({ timeout: 60_000 })
    expect(realErrors(grain)).toEqual([])
  } finally {
    await grain.close()
  }
})

test('a second show with pane right splits the panel; each pane has its own picker', async () => {
  const grain = await launchApp({ name: 'show-panel-split' })
  try {
    const { page } = grain
    await newChat(page)
    await showTool(page, { kind: 'markdown', content: '# Left note', title: 'Left' })
    await expect(panel(page).getByRole('heading', { name: 'Left', exact: true })).toBeVisible()
    await showTool(page, { kind: 'markdown', content: '# Right note', title: 'Right', pane: 'right' })
    await expect(panel(page).getByLabel('left pane', { exact: true })).toContainText('Left note')
    await expect(panel(page).getByLabel('right pane', { exact: true })).toContainText('Right note')
    // The right pane's picker swaps what it shows without touching the left one.
    await panel(page).getByLabel('What the right pane shows').selectOption({ label: 'Left' })
    await expect(panel(page).getByLabel('right pane', { exact: true })).toContainText('Left note')
    await panel(page).getByRole('button', { name: 'Close right pane' }).click()
    await expect(panel(page).getByLabel('left pane', { exact: true })).toHaveCount(0)
    await expect(panel(page).getByRole('button', { name: 'Split panel' })).toBeVisible()
  } finally {
    await grain.close()
  }
})
