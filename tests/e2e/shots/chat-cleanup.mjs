// Before/after screenshots for the chat cleanup. One isolated Grain per group of scenes (harness: own data dir, own port, mock LLM, hidden window).
//   GRAIN_ROOT=<checkout> SHOTS_OUT=<dir> node tests/e2e/shots/chat-cleanup.mjs     (ONLY=a,b,... limits groups: checklist,delegate,workers,desk,ask,mode,main)
import { appendFileSync, mkdirSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const ROOT = resolve(process.env.GRAIN_ROOT || join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..'))
const OUT = resolve(process.env.SHOTS_OUT || join(ROOT, 'docs', 'ux-shots', 'chat-cleanup'))
const imp = (p) => import(pathToFileURL(join(ROOT, 'tests/e2e', p)).href)
const { launchApp } = await imp('harness.mjs')
const { scriptLLM } = await imp('helpers/scriptllm.mjs')
const { deskChat, openChat, WRITE, DELIVER, DONE } = await imp('helpers/cowork.mjs')
const { msgBox, newChat, say, anyStop } = await imp('helpers/chat.mjs')

mkdirSync(OUT, { recursive: true })
const INV = join(OUT, 'inventory.md')
const NOTES = join(OUT, 'notes.md')
const only = process.env.ONLY ? process.env.ONLY.split(',') : null
const want = (k) => !only || only.includes(k)
if (!only) { writeFileSync(INV, '# Chat cleanup inventory (1280x800)\n'); writeFileSync(NOTES, `# Notes\nGRAIN_ROOT=${ROOT}\n`) }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const note = (s) => appendFileSync(NOTES, s + '\n')

async function session(name, fn, opts = {}) {
  const g = await launchApp({ name: 'cleanup', ...opts, settings: { hiddenViews: [], ...(opts.settings || {}) } })
  try {
    await g.app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].setSize(1280, 800))
    await g.page.waitForSelector('.sidebar'); await sleep(600)
    await fn(g)
  } catch (e) { console.log('SESSION FAILED', name, e); note(`## session ${name} FAILED\n${e.stack || e}`) }
  finally {
    note(`## session ${name}\nconsole errors:\n${g.consoleErrors.map((e) => '- ' + e.slice(0, 300)).join('\n') || '(none)'}\nbackend log tail:\n\`\`\`\n${g.backend.log().slice(-4000)}\n\`\`\``)
    await g.close().catch(() => {})
  }
}
async function shot(g, name, page = g.page) {
  await sleep(500)
  await page.screenshot({ path: join(OUT, name + '.png') })
  console.log('shot', name)
}
async function inventory(page, name, extra = '') {
  const d = await page.evaluate(() => {
    const vis = (e) => e.offsetParent !== null || getComputedStyle(e).position === 'fixed'
    const btns = [...document.querySelectorAll('button, [role=tab], [role=menuitem], a')].filter(vis)
      .map((e) => (e.getAttribute('aria-label') || e.innerText || e.title || '').trim().replace(/\s+/g, ' ')).filter(Boolean)
    const texts = []
    for (const e of document.querySelectorAll('p, div, span, li, label, h1, h2, h3, h4, small')) {
      if (!vis(e) || [...e.children].some((c) => (c.innerText || '').trim().length > 40)) continue
      const t = (e.innerText || '').trim().replace(/\s+/g, ' ')
      if (t.length > 40) texts.push(t.slice(0, 300))
    }
    return { btns, texts: [...new Set(texts)] }
  })
  appendFileSync(INV, `\n## ${name}\n${extra ? extra + '\n' : ''}Buttons/tabs/links: ${d.btns.join(' | ')}\nText blocks >40 chars:\n${d.texts.map((t) => '- ' + t).join('\n')}\n`)
}
async function snap(g, name, page = g.page, extra = '') { await shot(g, name, page); await inventory(page, name, extra) }
async function attempt(label, fn) {
  for (let i = 1; i <= 2; i++) {
    try { await fn(); return } catch (e) {
      const m = String(e).split('\n')[0]
      console.log('FAILED', label, 'try', i, m)
      if (i === 2) { appendFileSync(INV, `\n## ${label}\nNOT CAPTURED: ${m}\n`); note(`FAILED scene ${label}: ${m}`) }
      else { await g_page_reset?.(); await sleep(800) }
    }
  }
}
let g_page_reset = null
const settle = async (page, ms = 60000) => {
  const t = Date.now()
  while (Date.now() - t < ms) { if (!(await anyStop(page).count())) { await sleep(400); if (!(await anyStop(page).count())) return true } await sleep(300) }
  return false
}
const menuClick = (g, label) => g.app.evaluate(({ Menu }, l) => {
  const walk = (items) => { for (const it of items) { if (it.label === l) { it.click(); return true } if (it.submenu && walk(it.submenu.items)) return true } return false }
  return walk(Menu.getApplicationMenu().items)
}, label)
const escape = (g) => g.page.keyboard.press('Escape')
const MD = `!!reply Here is a quick plan for the week. I grouped the work so the riskiest item lands first.\n\n- Fix the flaky export test\n- Review the open pull requests\n- Draft the release notes\n\nA minimal helper looks like this:\n\n\`\`\`ts\nexport function total(xs: number[]) {\n  return xs.reduce((a, b) => a + b, 0)\n}\n\`\`\`\n\nLet me know if you want me to start on any of these.`

// ---- 0. checklist (the assistant's plan written with todo_write) ----
if (want('checklist')) await session('checklist', async (g) => {
  const { page } = g
  await g.api('/settings', { method: 'PUT', body: { toolDeferAbove: 0 } })
  await newChat(page); await sleep(500)
  await attempt('chat-checklist', async () => {
    await say(page, '!!tool todo_write {"steps":[{"text":"Read the export test","status":"done"},{"text":"Find the flaky assertion","status":"in_progress"},{"text":"Patch and rerun","status":"pending"},{"text":"Write the release note","status":"pending"}]}')
    await settle(page, 30000); await page.locator('section.plan-panel').waitFor({ timeout: 30000 }); await sleep(600)
    await snap(g, 'chat-checklist', page, 'checklist text: ' + (await page.locator('section.plan-panel').innerText()).replace(/\s+/g, ' '))
  })
  await attempt('chat-checklist-open', async () => {
    const head = page.locator('section.plan-panel button').first(); await head.click(); await sleep(500)
    await snap(g, 'chat-checklist-toggled', page, 'after clicking the first checklist button')
  })
  await attempt('chat-checklist-done', async () => {
    await say(page, '!!tool todo_write {"steps":[{"text":"Read the export test","status":"done"},{"text":"Find the flaky assertion","status":"done"},{"text":"Patch and rerun","status":"done"},{"text":"Write the release note","status":"done"}]}')
    await settle(page, 30000); await sleep(800)
    await snap(g, 'chat-checklist-done', page, 'all steps done: ' + (await page.locator('section.plan-panel').innerText().catch(() => '(no checklist rendered)')).replace(/\s+/g, ' '))
  })
})

// ---- 1. delegate transcript ----
if (want('delegate')) await session('delegate', async (g) => {
  const { page } = g
  await attempt('chat-delegate-transcript', async () => {
    await newChat(page); await sleep(500)
    await say(page, '!!tool delegate {"goal":"look something up","title":"Alpha job"}')
    await page.locator('section.worker-panel').waitFor({ timeout: 60000 }); await sleep(4000)
    const text = async (sel) => page.locator(sel).first().innerText().catch(() => '(none)')
    await snap(g, 'chat-delegate-transcript', page, `.messages innerText:\n${await text('.messages')}\n\nworker-panel innerText:\n${await text('section.worker-panel')}`)
    const done = await settle(page, 60000)
    await sleep(1000)
    await snap(g, 'chat-delegate-transcript-later', page, `settled=${done}\n.messages innerText:\n${await text('.messages')}\n\nworker-panel innerText:\n${await text('section.worker-panel')}`)
    await page.locator('section.worker-panel .worker-open').first().click(); await sleep(700)
    await snap(g, 'chat-delegate-worker-open', page, `worker-panel innerText:\n${await text('section.worker-panel')}`)
  })
}, { settings: { autonomousByDefault: false, telegramPushWorkerResults: false, toolDeferAbove: 0 } })

// ---- 2. desk workers strip ----
if (want('workers')) await session('workers', async (g) => {
  const { page } = g
  const llm = await scriptLLM(g)
  llm.push({ calls: [{ name: 'delegate', args: { goal: 'look something up', title: 'Alpha job' } }, { name: 'delegate', args: { goal: 'check the calendar notes', title: 'Beta job' } }] },
    { text: 'still working', delay: 120_000 }, { text: 'still working', delay: 120_000 })
  const { desk, chat } = await deskChat(g, { brief: 'look things up', title: 'Delegator', autonomy: 'ask' })
  await attempt('desk-workers', async () => {
    await openChat(page, 'Delegator')
    await page.locator('section.worker-panel').waitFor({ timeout: 60000 }); await sleep(2500)
    await snap(g, 'desk-workers-strip', page, 'Strip text: ' + (await page.locator('.desk-strip').innerText().catch(() => 'none')))
    await page.locator('.desk-strip').getByRole('button', { name: 'Files, changes and review' }).click(); await sleep(800)
    await snap(g, 'desk-workers-panel')
  })
  const { workers } = await g.api(`/conversations/${chat.id}/workers`).catch(() => ({ workers: [] }))
  for (const w of workers || []) await g.api(`/workers/${w.id}/stop`, { method: 'POST' }).catch(() => {})
  await g.api(`/cowork/desks/${desk.id}/stop`, { method: 'POST' }).catch(() => {})
}, { settings: { toolDeferAbove: 0 } })

// ---- 3. desk review ----
if (want('desk')) await session('desk', async (g) => {
  const { page } = g
  const llm = await scriptLLM(g)
  llm.push({ calls: [WRITE] }, { calls: [DELIVER] }, { calls: [DONE] }, { text: 'Finished. The report is in the Review tab.' })
  const { desk } = await deskChat(g, { brief: 'Write a short report', title: 'Report chat', autonomy: 'propose' })
  await attempt('desk-review', async () => {
    await openChat(page, 'Report chat')
    for (let i = 0; i < 90; i++) { if ((await g.api(`/cowork/desks/${desk.id}`)).status === 'review') break; await sleep(1000) }
    await sleep(1500)
    if (!(await page.locator('.desk-panel').count())) await page.locator('.desk-strip').getByRole('button', { name: 'Files, changes and review' }).click()
    await sleep(600)
    const tabs = page.locator('.desk-panel .desk-tabs button:not(.icon-btn)')
    const n = await tabs.count()
    for (let i = 0; i < n; i++) {
      const label = (await tabs.nth(i).innerText()).trim().split(/\s+/)[0].toLowerCase()
      await tabs.nth(i).click(); await sleep(700)
      await snap(g, `desk-review-${label}`, page, i === 0 ? 'Strip text: ' + (await page.locator('.desk-strip').innerText().catch(() => 'none')) : '')
    }
  })
}, { settings: { toolDeferAbove: 0 } })

// ---- ask_user card (text-based locators: the before checkout has the old markup) ----
const Q = 'How would you like your Downloads folder sorted?'
const OPTS = ['By file type (PDFs, Images, Zips, etc.)', 'By date (monthly folders)', 'Clean up junk (old installers, zips), keep rest', "Just show me what's in there first"]
if (want('ask')) await session('ask', async (g) => {
  const { page } = g
  await newChat(page); await sleep(500)
  await attempt('chat-ask-card', async () => {
    await say(page, `!!tool ask_user ${JSON.stringify({ question: Q, options: OPTS })}`)
    await page.getByText(Q).first().waitFor({ timeout: 60000 }); await sleep(600)
    await snap(g, 'chat-ask-card', page, '.messages innerText:\n' + await page.locator('.messages').first().innerText())
    const box = page.getByRole('textbox', { name: /answer/i }).first()
    await box.fill('Sort by project'); await sleep(300)
    await snap(g, 'chat-ask-card-typing', page)
    await box.fill(''); await sleep(200)
    await page.getByRole('button', { name: OPTS[0] }).click()
    await settle(page, 60000); await sleep(800)
    await snap(g, 'chat-ask-card-answered', page, '.messages innerText:\n' + await page.locator('.messages').first().innerText())
  })
}, { settings: { toolDeferAbove: 0 } })

// ---- 4-10. everything else ----
// ---- mode menu (composer Mode button: the three styles and their one-line descriptions) ----
if (want('mode')) await session('mode', async (g) => {
  const { page } = g
  g_page_reset = async () => { await escape(g).catch(() => {}); await escape(g).catch(() => {}) }
  await attempt('chat-mode-menu', async () => {
    await newChat(page); await sleep(500)
    await page.getByRole('button', { name: /^Mode/ }).click(); await sleep(500)
    await snap(g, 'chat-mode-menu', page, 'menu text: ' + (await page.getByRole('dialog', { name: 'Mode' }).innerText()).replace(/\s+/g, ' '))
    await escape(g)
  })
})

if (want('main')) await session('main', async (g) => {
  const { page } = g
  g_page_reset = async () => { await escape(g).catch(() => {}); await escape(g).catch(() => {}) }
  // 5. composer at rest (before any message) + mic
  await attempt('chat-composer', async () => {
    await newChat(page); await sleep(700)
    const info = await page.evaluate(() => {
      const box = document.querySelector('textarea[aria-label="Message"], [aria-label="Message"]')
      let n = box; while (n && n.querySelectorAll('button').length < 3) n = n.parentElement
      const lab = (e) => `${e.getAttribute('aria-label') || ''}${e.title ? ' {title:' + e.title + '}' : ''}${!e.getAttribute('aria-label') && e.innerText ? ' "' + e.innerText.trim() + '"' : ''}`
      const comp = n ? [...n.querySelectorAll('button')].map(lab) : []
      const mic = [...document.querySelectorAll('button')].filter((b) => /mic|dictat|voice|speak|listen|talk/i.test((b.getAttribute('aria-label') || '') + (b.title || ''))).map(lab)
      return { comp, mic }
    })
    await snap(g, 'chat-composer', page, `COMPOSER BUTTONS: ${info.comp.join(' | ')}\nMIC/VOICE BUTTONS (whole page): ${info.mic.join(' | ') || '(none)'}`)
    note(`composer buttons: ${info.comp.join(' | ')}\nmic-like: ${info.mic.join(' | ') || '(none)'}`)
    const mic = page.locator('button').filter({ has: page.locator('xpath=.') }).and(page.locator('[aria-label*="icrophone" i], [aria-label*="ictat" i], [aria-label*="oice" i], [title*="ictat" i]')).first()
    if (await mic.count()) { await mic.hover(); await sleep(900); await snap(g, 'chat-composer-mic', page, 'hovering first mic/dictate button') }
    else note('no mic/dictation button found in chat composer; chat-composer-mic skipped')
  })
  // 4. reply hover + message menu
  await attempt('chat-reply-hover', async () => {
    await say(page, MD); await settle(page, 30000)
    const last = page.locator('.msg.assistant').last()
    await last.hover(); await sleep(400)
    const btns = await last.locator('button').evaluateAll((els) => els.map((e) => (e.getAttribute('aria-label') || e.title || e.innerText || '').trim()))
    await snap(g, 'chat-reply-hover', page, `last assistant message buttons: ${btns.join(' | ')}`)
    const more = last.getByRole('button', { name: /more|⋯|…|actions|menu/i }).first()
    if (await more.count()) { await more.click(); await sleep(500); await snap(g, 'chat-message-menu'); await escape(g) }
    else note('no More/menu button on assistant message; chat-message-menu skipped. buttons: ' + btns.join(' | '))
  })
  // 7. files side panel (was a popover)
  await attempt('chat-files-popover', async () => {
    await say(page, '!!tool current_time {}'); await settle(page, 30000); await sleep(500)
    await page.getByRole('button', { name: 'Documents in this chat' }).click(); await page.locator('aside.desk-panel').waitFor({ timeout: 10000 }); await sleep(700)
    const tabs = (await page.locator('aside.desk-panel [role=tab], aside.desk-panel .desk-tabs button').allInnerTexts()).map((t) => t.trim()).filter(Boolean)
    await snap(g, 'chat-files-popover', page, 'side panel tabs: ' + tabs.join(' | '))
    await snap(g, 'chat-side-panel-files', page)
    await page.locator('aside.desk-panel').getByRole('tab', { name: /Changes/ }).or(page.locator('aside.desk-panel .desk-tabs button', { hasText: 'Changes' })).first().click(); await sleep(700)
    await snap(g, 'chat-side-panel-changes', page)
    await page.getByRole('button', { name: 'Documents in this chat' }).click(); await sleep(500)
  })
  // 8. sidebar chats
  await attempt('sidebar-chats', async () => {
    for (const t of ['Trip planning', 'Quarterly numbers', 'Recipe ideas']) await g.api('/conversations', { method: 'POST', body: { title: t } })
    await page.reload(); await page.waitForSelector('.sidebar'); await sleep(1200)
    await shot(g, 'sidebar-chats', page)
    await page.locator('.sidebar').screenshot({ path: join(OUT, 'sidebar-chats-crop.png') })
    await inventory(page, 'sidebar-chats')
  })
  // unread dot
  await attempt('sidebar-unread-dot', async () => {
    await page.getByRole('button', { name: /New chat/ }).first().click(); await sleep(500)
    const b = await g.api('/conversations', { method: 'POST', body: { title: 'Chat B unread' } })
    await page.reload(); await page.waitForSelector('.sidebar'); await sleep(3500)
    await g.api(`/conversations/${b.id}/chat`, { method: 'POST', body: { content: '!!reply hello from B' } })
    const item = page.locator('.convo-item', { hasText: 'Chat B unread' }).first()
    await item.locator('.pulse.unread').waitFor({ timeout: 30000 }); await sleep(500)
    await snap(g, 'sidebar-unread-dot', page, 'unread dot present on Chat B')
    await page.locator('.sidebar').screenshot({ path: join(OUT, 'sidebar-unread-dot-crop.png') })
    await item.click(); await sleep(1200)
    await snap(g, 'sidebar-unread-cleared', page, 'unread dots now: ' + await page.locator('.pulse.unread').count())
  })
  // 6. files note editor
  await attempt('files-note-editor', async () => {
    await page.locator('.nav-item', { hasText: 'Files' }).first().click()
    await page.getByRole('tablist', { name: 'Files section' }).waitFor(); await sleep(500)
    await menuClick(g, 'New File'); await sleep(1500)
    const t = page.locator('.doc-edit-toggle')
    if (await t.count() && (await t.getAttribute('aria-pressed')) !== 'true') await t.click()
    await sleep(600)
    const bar = await page.evaluate(() => [...document.querySelectorAll('.doc-view button, .doc-editor button, .md-toolbar button, [role=toolbar] button, .page-header button')].filter((e) => e.offsetParent).map((e) => (e.getAttribute('aria-label') || e.title || e.innerText || '').trim()).filter(Boolean))
    await snap(g, 'files-note-editor', page, `TOOLBAR/HEADER BUTTONS: ${bar.join(' | ')}`)
  })
  // 8. library agents
  await attempt('library-agents', async () => {
    await page.getByRole('button', { name: /^Library/ }).first().click()
    await page.getByRole('heading', { name: /Library/ }).waitFor()
    await page.getByRole('tab', { name: 'Agents' }).click(); await sleep(1000)
    await snap(g, 'library-agents')
  })
  // 9. settings tabs
  await attempt('settings', async () => {
    await menuClick(g, 'Settings…') || await page.locator('.settings-btn').click()
    const d = page.getByRole('dialog', { name: 'Settings' }); await d.waitFor(); await sleep(500)
    const tabs = (await d.getByRole('tab').allInnerTexts()).map((s) => s.trim())
    note('settings tabs: ' + tabs.join(' | '))
    for (const t of tabs) {
      await d.getByRole('tab', { name: t, exact: true }).click(); await sleep(800)
      await snap(g, 'settings-' + t.toLowerCase().replace(/[^a-z]+/g, '-').replace(/^-|-$/g, ''))
    }
    await escape(g); await sleep(400)
  })
  // 10. spaces crew
  await attempt('spaces-crew', async () => {
    await menuClick(g, 'Toggle Spaces'); await sleep(2500)
    await snap(g, 'spaces-crew')
  })
}, { settings: { toolDeferAbove: 0 } })
console.log('done')
