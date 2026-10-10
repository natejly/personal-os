import { appendFileSync } from 'node:fs'
import { join } from 'node:path'
import { homedir } from 'node:os'
import { session, shot as rawShot, nav, sleep } from './lib.mjs'
import { seedJobRuns } from '../helpers/home.mjs'
import { dayStr } from '../helpers/todos.mjs'
import { enterCanvas, menuClick, spaces, windowsOf } from '../helpers/spaces.mjs'
import { killBackend } from '../helpers/shell.mjs'

const INV = join(homedir(), '.claude/jobs/66435b7a/tmp/inventory-shell.md')
const only = process.argv[2] // optional: run one part

async function shot(g, name, page = g.page) {
  await rawShot(g, name, page)
  const d = await page.evaluate(() => {
    const vis = (e) => e.offsetParent !== null || getComputedStyle(e).position === 'fixed'
    const buttons = [...document.querySelectorAll('button, [role=tab], [role=menuitem], [role=radio], a')].filter(vis).map((e) => (e.getAttribute('aria-label') || e.innerText || '').trim().replace(/\s+/g, ' ')).filter(Boolean)
    const blocks = []
    document.querySelectorAll('p, li, span, div, h1, h2, h3, h4, label').forEach((e) => {
      if (!vis(e) || [...e.children].some((c) => (c.innerText || '').trim().length > 20)) return
      const t = (e.innerText || '').trim().replace(/\s+/g, ' ')
      if (t.length > 40) blocks.push(t)
    })
    const heads = [...document.querySelectorAll('h1, h2, h3, header')].filter(vis).map((e) => (e.innerText || '').trim().replace(/\s+/g, ' ')).filter(Boolean)
    return { heads: [...new Set(heads)].slice(0, 15), buttons, blocks: [...new Set(blocks)] }
  }).catch(() => ({ heads: [], buttons: [], blocks: [] }))
  appendFileSync(INV, `\n## ${name}\nHeaders: ${d.heads.join(' | ')}\nButtons/tabs/links: ${d.buttons.join(' | ')}\nText blocks:\n${d.blocks.map((b) => '- ' + b).join('\n')}\n`)
}

const menuById = async (g, id) => { await g.app.evaluate(({ BrowserWindow }) => { const b = BrowserWindow.getAllWindows().find((b) => b.webContents.getURL().includes('index.html')); if (b) { b.show(); b.focus() } }); await sleep(300); return menuById0(g, id) }
const menuById0 = (g, id) => g.app.evaluate(({ Menu }, id) => { const it = Menu.getApplicationMenu().getMenuItemById(id); if (!it) return false; it.click(); return true }, id)
const run = (part, fn, opts) => (!only || only === part) ? session(fn, opts) : null

await run('home', async (g) => {
  const { page, api } = g
  await page.getByRole('button', { name: /New chat/ }).first().click()
  // chat with a reply
  const box = page.getByRole('textbox', { name: 'Message' })
  await box.fill('!!reply Here is a short answer about your week. Two meetings, one deadline on Friday.')
  await box.press('Enter')
  await page.locator('.msg.assistant').last().waitFor({ timeout: 30000 })
  await sleep(1500)
  await page.getByRole('button', { name: /New chat/ }).first().click()
  await api('/projects', { method: 'POST', body: { name: 'Website relaunch' } })
  const todos = [['Send invoice to Acme', dayStr(0)], ['Renew passport', dayStr(-3)], ['Book dentist', dayStr(4)], ['Read the Q3 report', null]]
  for (const [title, due] of todos) await api('/todos', { method: 'POST', body: { title, due } })
  seedJobRuns(g, 3)
  await page.reload()
  await page.waitForSelector('.sidebar')
  await page.getByRole('button', { name: /New chat/ }).first().click()
  await sleep(1500)
  await shot(g, 'new-chat-seeded')
})

await run('sidebar', async (g) => {
  const { page, api } = g
  const p = await api('/projects', { method: 'POST', body: { name: 'Website relaunch' } })
  await api('/projects', { method: 'POST', body: { name: 'Tax 2026' } })
  await api('/canvases', { method: 'POST', body: { name: 'Research desk' } })
  const titles = ['Plan the launch email', 'Fix login redirect bug', 'Trip to Lisbon', 'Meeting notes: Acme']
  for (let i = 0; i < titles.length; i++) await api('/conversations', { method: 'POST', body: { title: titles[i], project_id: i < 2 ? p.id : null } })
  await page.reload(); await page.waitForSelector('.sidebar'); await sleep(1200)
  await shot(g, 'sidebar-expanded')
  const row = page.locator('.sidebar .convo-item', { hasText: 'Trip to Lisbon' }).first()
  await row.click({ button: 'right' })
  await sleep(500)
  await shot(g, 'sidebar-chat-context-menu')
  await page.keyboard.press('Escape')
  await page.getByRole('button', { name: 'Hide sidebar' }).click()
  await sleep(700)
  await shot(g, 'sidebar-collapsed')
  await page.getByRole('button', { name: /Show sidebar/ }).click()
  await sleep(500)
})

await run('overlays', async (g) => {
  const { page } = g
  await page.getByRole('button', { name: /New chat/ }).first().click()
  // quick chat (⌘I)
  await page.getByRole('button', { name: 'Quick chat (⌘I)' }).click()
  await page.getByRole('complementary', { name: 'Page agent' }).waitFor()
  await sleep(700)
  await shot(g, 'quick-chat')
  const ta = page.getByRole('complementary', { name: 'Page agent' }).getByRole('textbox').first()
  await ta.fill('!!reply hi')
  await ta.press('Enter')
  await sleep(3000)
  await shot(g, 'quick-chat-reply')
  await page.getByRole('button', { name: 'Quick chat (⌘I)' }).click()
  // palette
  await menuClick(g.app, 'Command Palette…')
  await page.getByRole('dialog').first().waitFor({ timeout: 5000 }).catch(() => {})
  await sleep(500)
  await shot(g, 'command-palette')
  await page.keyboard.press('Escape')
  await sleep(300)
  await menuClick(g.app, 'Keyboard Shortcuts…')
  await page.getByRole('dialog', { name: 'Help' }).waitFor({ timeout: 5000 }).catch(() => {})
  await sleep(500)
  await shot(g, 'help-overlay')
  await page.getByRole('tab', { name: /guide/i }).click().catch(() => {})
  await sleep(400)
  await shot(g, 'help-guide')
})

await run('small', async (g) => {
  const { page } = g
  await page.getByRole('button', { name: /New chat/ }).first().click()
  await g.app.evaluate(({ BrowserWindow }) => { const w = BrowserWindow.getAllWindows()[0]; w.setMinimumSize(1, 1); w.setSize(820, 520) })
  await sleep(1200)
  await shot(g, 'small-window')
})

await run('ob', async (g) => {
  const { page } = g
  const w = page.locator('.onboarding')
  await w.waitFor()
  let i = 1
  await shot(g, `onboarding-${i++}`)
  const cont = (n = 'Continue') => page.getByRole('button', { name: new RegExp('^' + n) })
  await cont('Get started').click(); await sleep(500)
  await shot(g, `onboarding-${i++}`)
  await page.getByRole('radio', { name: /Custom/ }).click()
  await cont().click(); await sleep(500)
  await shot(g, `onboarding-${i++}`)
  await w.getByRole('textbox', { name: /API key/ }).fill('mock-key').catch(() => {})
  await cont().click(); await sleep(2500)
  await shot(g, `onboarding-${i++}`)
  await cont().click(); await sleep(600)
  await shot(g, `onboarding-${i++}`)
  await cont('Skip for now').click(); await sleep(500)
  await shot(g, `onboarding-${i++}`)
  await cont('Skip for now').click(); await sleep(500)
  await shot(g, `onboarding-${i++}`)
  await cont('Skip for now').click(); await sleep(500)
  await shot(g, `onboarding-${i++}`)
}, { settings: { onboardedAt: null, apiKey: null } })

await run('canvas', async (g) => {
  const { page, api } = g
  const s = (await spaces(g))[0]
  const c = await api('/conversations', { method: 'POST', body: { title: 'Launch plan chat' } })
  await api('/canvases/' + s.id + '/windows', { method: 'POST', body: { kind: 'chat', ref_id: c.id, x: 80, y: 60, w: 520, h: 520 } })
  await api('/canvases/' + s.id + '/windows', { method: 'POST', body: { kind: 'face', x: 680, y: 120, w: 240, h: 240 } })
  await page.reload(); await page.waitForSelector('.sidebar')
  await enterCanvas(g)
  await sleep(2000)
  await shot(g, 'space-canvas')
  const wins = await windowsOf(g, s.id)
  const w = wins.find((x) => x.kind === 'face')
  const grip = await page.locator(`[data-window-id="${w.id}"] .win-move`).boundingBox()
  await page.mouse.click(grip.x + 40, grip.y + 8)
  await g.app.evaluate(({ BrowserWindow }) => { const b = BrowserWindow.getAllWindows().find((b) => b.webContents.getURL().includes('index.html')); if (b) { b.show(); b.focus() } })
  await menuClick(g.app, 'Pop Out')
  await sleep(4000)
  const pop = g.app.windows().find((p) => p.url().includes('surface=widget'))
  if (pop) { await pop.waitForLoadState('domcontentloaded'); await sleep(1000); await shot(g, 'space-popout', pop) } else console.log('no popout window')
  await shot(g, 'space-canvas-popped')
})

await run('failed', async (g) => {
  await g.page.getByRole('button', { name: /New chat/ }).first().click()
  await killBackend(g)
  await sleep(500)
  await g.page.reload().catch(() => {})
  await sleep(6000)
  await shot(g, 'backend-failed')
})
