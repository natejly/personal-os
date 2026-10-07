// Files, Projects, Settings screenshots + text inventory. Run: zsh ~/.claude/jobs/66435b7a/tmp/node20.sh tests/e2e/shots/files-settings.mjs
import { appendFileSync, writeFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'
import { session, shot, sleep } from './lib.mjs'
import { openFiles, mkDoc, editDoc } from '../helpers/files.mjs'
import { openSettings, openAdvanced, openMemory, dialog, TABS } from '../helpers/home.mjs'
import { upload } from '../helpers/kb.mjs'

const INV = join(homedir(), '.claude/jobs/66435b7a/tmp/inventory-files-settings.md')
writeFileSync(INV, '# Inventory: files-settings\n')
const ONLY = process.env.ONLY // comma list of sections: files,projects,settings
const want = (s) => !ONLY || ONLY.split(',').includes(s)

async function inventory(page, name) {
  const d = await page.evaluate(() => {
    const vis = (e) => e.offsetParent !== null || getComputedStyle(e).position === 'fixed'
    const header = [...document.querySelectorAll('.page-header, [role=dialog] header, .project-page h1, .project-page h2')].filter(vis).map((e) => e.innerText.replace(/\s+/g, ' ').trim())
    const ctrl = [...document.querySelectorAll('button, [role=tab], [role=menuitem], [role=menuitemcheckbox], a, summary, select, input:not([type=hidden]), textarea')].filter(vis)
      .map((e) => {
        const l = (e.getAttribute('aria-label') || e.innerText || e.getAttribute('placeholder') || e.title || '').replace(/\s+/g, ' ').trim()
        return l && `${['INPUT', 'TEXTAREA', 'SELECT'].includes(e.tagName) ? '(' + e.tagName.toLowerCase() + ') ' : ''}${l}`
      }).filter(Boolean)
    const texts = [...document.querySelectorAll('p, span, div, label, li, small, h1, h2, h3, h4, td')].filter(vis)
      .filter((e) => ![...e.children].some((c) => ['P', 'DIV', 'LI', 'LABEL', 'H1', 'H2', 'H3', 'UL', 'TABLE'].includes(c.tagName)))
      .map((e) => e.innerText.replace(/\s+/g, ' ').trim()).filter((t) => t.length > 40)
    return { header, ctrl: [...new Set(ctrl)], texts: [...new Set(texts)] }
  })
  appendFileSync(INV, `\n## ${name}\nHeader: ${d.header.join(' | ')}\nControls: ${d.ctrl.map((c) => '[' + c + ']').join(' ')}\nText:\n${d.texts.map((t) => '- ' + t).join('\n')}\n`)
}
async function snap(g, name, fn) {
  try {
    if (fn) await fn()
    await shot(g, name)
    await inventory(g.page, name)
  } catch (e) {
    console.log('FAILED', name, String(e.message).split('\n')[0])
    appendFileSync(INV, `\n## ${name}\nFAILED: ${String(e.message).split('\n')[0]}\n`)
  }
}
const openDocRow = (page, title) => page.locator('.doc-row', { hasText: title }).last().click()
const size = (g, w, h) => g.app.evaluate(({ BrowserWindow }, [w, h]) => BrowserWindow.getAllWindows()[0].setSize(w, h), [w, h])
const slug = (s) => s.toLowerCase().replace(/[^a-z]+/g, '-').replace(/^-|-$/g, '')

// ---- Files ----
if (want('files')) await session(async (g) => {
  const { page } = g
  await snap(g, 'files-empty', () => openFiles(page))
  await mkDoc(g, { title: 'Weekly review', content: '# Weekly review\n\nWhat shipped this week, what slipped, and what to carry over.\n\n- Shipped the sidebar rework\n- Slipped: export polish\n\n## Next\n\nPlan Monday.', folder: 'Work' })
  await mkDoc(g, { title: 'Project ideas', content: '# Project ideas\n\n1. A small habit tracker\n2. Recipe box', folder: 'Work' })
  await mkDoc(g, { title: 'Groceries', content: 'milk\neggs\nbread', folder: 'Home' })
  await page.reload(); await page.waitForSelector('.sidebar')
  await openFiles(page)
  await sleep(800)
  await snap(g, 'files-notes-list')
  await openDocRow(page, 'Weekly review')
  await sleep(500)
  await snap(g, 'files-note-read')
  await snap(g, 'files-note-editor', () => editDoc(page))
  await snap(g, 'files-doc-comments', async () => {
    const ds = await g.api('/docs')
    const d = ds.find((x) => x.title === 'Weekly review')
    await g.api(`/docs/${d.id}/comments`, { method: 'POST', body: { body: 'Should this mention the export bug?', quote: 'export polish', prefix: 'Slipped: ', suffix: '', offset_hint: 80 } })
    await page.reload(); await page.waitForSelector('.sidebar'); await openFiles(page); await openDocRow(page, 'Weekly review')
    await sleep(500)
    const panel = page.getByRole('button', { name: 'Toggle side panel' })
    if ((await panel.getAttribute('aria-pressed')) !== 'true') await panel.click()
    await page.getByRole('tab', { name: 'Comments' }).click()
  })
  await snap(g, 'files-export-menu', async () => { await page.getByTitle('Export').click() })
  await page.keyboard.press('Escape')
  await snap(g, 'files-new-doc-menu', async () => { await page.getByRole('button', { name: 'New from template' }).first().click() })
  await page.keyboard.press('Escape')
  await snap(g, 'files-uploads', async () => {
    await upload(g, 'meeting-notes.txt', 'alpha bravo charlie\n'.repeat(40))
    await page.getByRole('tab', { name: 'Uploads', exact: true }).click()
    await sleep(1200)
  })
  await snap(g, 'files-artifacts', async () => { await page.getByRole('tab', { name: 'Artifacts', exact: true }).click(); await sleep(800) })
  await snap(g, 'files-trash', async () => {
    await page.getByRole('tab', { name: 'Notes', exact: true }).click()
    await page.locator('.doc-row', { hasText: 'Groceries' }).last().getByTitle('Delete').click()
    await sleep(500)
    await page.locator('.doc-row', { hasText: 'Trash' }).click()
    await sleep(600)
    await dialog(page).locator('.trash-row').first().scrollIntoViewIfNeeded().catch(() => {})
  })
  await snap(g, 'files-narrow', async () => {
    await page.getByRole('button', { name: 'Close settings' }).click().catch(() => {})
    await size(g, 820, 520); await sleep(700)
    await page.locator('.doc-row', { hasText: 'Weekly review' }).last().click().catch(() => {})
  })
})

// ---- Projects ----
if (want('projects')) await session(async (g) => {
  const { page, api } = g
  const p = await api('/projects', { method: 'POST', body: { name: 'Apollo', description: 'Moon landing planning', color: '#3b9edb', system_prompt: 'Answer tersely. Prefer metric units.' } })
  for (let i = 1; i <= 3; i++) await api('/conversations', { method: 'POST', body: { project_id: p.id, title: `Trajectory draft ${i}` } })
  await api('/docs', { method: 'POST', body: { title: 'Mission brief', content: '# Mission brief\n\nLand softly.', project_id: p.id } })
  await upload(g, 'budget.txt', 'Fuel: 40%\nCrew: 12%\n', p.id)
  await api('/memories', { method: 'POST', body: { content: 'Apollo launches in March', project_id: p.id } }).catch((e) => console.log('mem seed', e.message))
  await page.reload(); await page.waitForSelector('.sidebar'); await sleep(800)
  await snap(g, 'project-sidebar-group')
  await page.locator('.sidebar').getByText('Apollo', { exact: true }).click()
  await sleep(600)
  await snap(g, 'project-page')
  const sec = (n) => page.locator('.project-page').getByRole('region', { name: n })
  await snap(g, 'project-memory', async () => { await sec('Memory').scrollIntoViewIfNeeded(); await sleep(600) })
  await snap(g, 'project-modal', async () => { await page.getByRole('button', { name: 'Edit', exact: true }).click(); await page.getByRole('dialog').waitFor() })
})

// ---- Settings ----
if (want('settings')) await session(async (g) => {
  const { page } = g
  for (const c of ['Prefers short answers', 'Works on Grain, an Electron app, on a Mac', 'Lives in Berlin, travels in March', 'Dislikes meetings before 10am'])
    await g.api('/memories', { method: 'POST', body: { content: c } }).catch((e) => console.log('mem seed', e.message))
  await page.reload(); await page.waitForSelector('.sidebar'); await sleep(600)
  for (const t of TABS) await snap(g, 'settings-' + slug(t), async () => { await openSettings(page, t); await sleep(700) })
  await snap(g, 'settings-permissions-allow-all', async () => {
    await openSettings(page, 'Permissions')
    const d = dialog(page)
    const r = d.getByRole('radio', { name: /allow everything/i }).first()
    await r.click()
    await sleep(400)
  })
  await snap(g, 'settings-permissions-allow-all-confirmed', async () => {
    await page.getByRole('alertdialog').getByRole('button', { name: 'Allow everything' }).click()
    await sleep(600)
  })
  await openSettings(page, 'Advanced')
  const groups = (await dialog(page).locator('.adv-group > summary').allInnerTexts()).map((s) => s.trim())
  console.log('adv groups', groups)
  for (const gr of groups) await snap(g, 'settings-advanced-' + slug(gr), async () => {
    await openSettings(page, 'Advanced')
    const all = dialog(page).locator('.adv-group')
    for (let i = 0; i < await all.count(); i++) { const el = all.nth(i); const isMe = (await el.locator(':scope > summary').innerText()).trim() === gr; if (!isMe && (await el.evaluate((e) => e.open))) await el.locator(':scope > summary').click() }
    await openAdvanced(page, gr)
    await dialog(page).locator('.adv-group > summary', { hasText: gr }).scrollIntoViewIfNeeded()
    await sleep(300)
  })
  for (const l of ['List', 'Graph', 'Voice']) await snap(g, 'settings-memory-' + slug(l), async () => { await openMemory(page, l); await sleep(900) })
  await snap(g, 'settings-narrow', async () => { await openSettings(page, 'Model'); await size(g, 820, 520); await sleep(700) })
})
console.log('done')
