import { appendFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { homedir } from 'node:os'
import { session, shot as rawShot, nav, sleep } from './lib.mjs'
import { ROOT } from '../harness.mjs'
import { dayStr, seed } from '../helpers/todos.mjs'
import { openLibrary } from '../helpers/library.mjs'
import { enableModules, reload, sql as mahSql } from '../helpers/mah.mjs'
import { seedJobRuns } from '../helpers/home.mjs'

const INV = join(homedir(), '.claude/jobs/66435b7a/tmp/inventory-views.md')
if (!process.env.ONLY) writeFileSync(INV, '# Inventory: views\n')
const only = (process.env.ONLY || '').split(',').filter(Boolean)
const want = (k) => !only.length || only.includes(k)

async function shot(g, name, extra = '') {
  await rawShot(g, name)
  const d = await g.page.evaluate(() => {
    const vis = (e) => e.offsetParent !== null
    const buttons = [...document.querySelectorAll('button, [role=tab], [role=menuitem], a')].filter(vis)
      .map((e) => (e.getAttribute('aria-label') || e.innerText || '').trim().replace(/\s+/g, ' ')).filter(Boolean)
    const heads = [...document.querySelectorAll('h1,h2,h3,h4,.header-title,.page-title,header')].filter(vis).map((e) => e.innerText.trim().replace(/\s+/g, ' ')).filter(Boolean)
    const seen = new Set(); const blocks = []
    for (const e of document.querySelectorAll('p, span, div, li, label, summary, small')) {
      if (!vis(e) || e.children.length > 3) continue
      const t = (e.innerText || '').trim().replace(/\s+/g, ' ')
      if (t.length > 40 && !seen.has(t)) { seen.add(t); blocks.push(t) }
    }
    return { heads, buttons, blocks }
  })
  appendFileSync(INV, `\n## ${name}\n${extra ? extra + '\n' : ''}headers: ${d.heads.join(' | ')}\n\nbuttons/tabs/links (${d.buttons.length}): ${d.buttons.join(' | ')}\n\ntext blocks >40 chars:\n${d.blocks.map((b) => '- ' + b).join('\n')}\n`)
}
const note = (s) => appendFileSync(INV, `\n${s}\n`)
const sideLabel = (page, name) => nav(page, name).innerText().then((t) => t.replace(/\s+/g, ' ').trim())
const attempt = async (label, fn) => { try { await fn() } catch (e) { console.log('FAILED', label, String(e).split('\n')[0]); note(`### ${label} FAILED: ${String(e).split('\n')[0]}`) } }

if (want('todos')) await session(async (g) => {
  const { page, api } = g
  note(`### Sidebar Lists row, 0 todos: "${await sideLabel(page, 'Lists')}"`)
  await nav(page, 'Lists').click()
  await page.getByPlaceholder('Add to Todos…').waitFor()
  await shot(g, 'lists-empty')
  await seed(api, 6, (i) => [
    { title: 'Send the quarterly report', due: dayStr(-3), priority: 1 },
    { title: 'Book dentist appointment', due: dayStr(0) },
    { title: 'Review pull request', due: dayStr(0), tags: ['work'] },
    { title: 'Plan weekend trip', due: dayStr(6) },
    { title: 'Read a paper on retrieval' },
    { title: 'Water the plants', due: dayStr(-1) }
  ][i], 1)
  const t = await api('/todos')
  await api(`/todos/${t.find((x) => x.title === 'Water the plants').id}`, { method: 'PUT', body: { done: true } })
  await reload(page)
  note(`### Sidebar Lists row right after reload, 5 open + 1 done: "${await sideLabel(page, 'Lists')}"`)
  await sleep(4000)
  note(`### Sidebar Lists row 4s after reload (Lists view never opened), 5 open + 1 done: "${await sideLabel(page, 'Lists')}"`)
  await nav(page, 'Lists').click()
  await page.getByPlaceholder('Add to Todos…').waitFor()
  await page.getByText('Show done').click().catch(() => {})
  await shot(g, 'lists-seeded')
  const r = page.locator('.todo', { hasText: 'Review pull request' }).first()
  await r.hover()
  await r.getByRole('button', { name: /Add subtask/ }).click().catch(() => {})
  await shot(g, 'lists-item-open')
  await api(`/todos/${t.find((x) => x.title === 'Plan weekend trip').id}`, { method: 'PUT', body: { done: true } })
  await api(`/todos/${t.find((x) => x.title === 'Read a paper on retrieval').id}`, { method: 'PUT', body: { done: true } })
  await reload(page)
  await sleep(4000)
  note(`### Sidebar Lists row 4s after reload, 3 open todos (+3 done): "${await sideLabel(page, 'Lists')}"`)
  await nav(page, 'Lists').click(); await sleep(1500)
  note(`### Sidebar Lists row after opening Lists, 3 open: "${await sideLabel(page, 'Lists')}"`)
})

if (want('disc')) await session(async (g) => {
  await nav(g.page, 'Calendar').click(); await sleep(1500)
  await shot(g, 'calendar-disconnected')
  await nav(g.page, 'Mail').click(); await sleep(1500)
  await shot(g, 'mail-disconnected')
})

if (want('google')) await session(async (g) => {
  const { page } = g
  await g.api('/__fake/events', { method: 'POST', body: { n: 14, days: 7 } }).catch(() => {})
  await nav(page, 'Calendar').click()
  await page.locator('.cal-event').first().waitFor({ timeout: 40000 })
  await shot(g, 'calendar-week')
  await page.getByRole('button', { name: 'Month', exact: true }).click(); await sleep(1500)
  await shot(g, 'calendar-month')
  await page.getByRole('button', { name: 'Week', exact: true }).click(); await sleep(800)
  await attempt('calendar-event-editor', async () => {
    await page.locator('.cal-event', { hasText: 'Review' }).first().click()
    await page.locator('.event-editor').waitFor({ timeout: 10000 })
    await shot(g, 'calendar-event-editor')
    await page.locator('.event-editor').getByRole('button', { name: 'Cancel' }).first().click()
  })
  await nav(page, 'Mail').click()
  await page.locator('.mail-list .mail-row').first().waitFor({ timeout: 40000 })
  await shot(g, 'mail-inbox')
  await page.locator('.mail-list .mail-row', { hasText: /Subject 3(?!\d)/ }).first().click()
  await page.locator('.mail-reader').waitFor()
  await shot(g, 'mail-thread')
  await attempt('mail-compose-reply', async () => {
    await page.locator('.mail-reader').getByRole('button', { name: 'Reply', exact: true }).click()
    await page.locator('.mail-compose').waitFor({ timeout: 10000 })
    await shot(g, 'mail-compose-reply')
    page.once('dialog', (d) => d.accept()); await page.getByRole('button', { name: 'Discard message' }).click()
    await sleep(500)
    
  })
  await page.getByRole('button', { name: 'Compose' }).click()
  await page.locator('.mail-compose').waitFor()
  await shot(g, 'mail-compose')
}, { backendEntry: [join(ROOT, 'tests', 'e2e', 'backend_fake_google.py')], settings: { toolDeferAbove: 0 } })

if (want('health')) await session(async (g) => {
  const { page, api, dataDir } = g
  await enableModules(api); await reload(page)
  await page.getByRole('button', { name: 'Health', exact: true }).click()
  await page.locator('.hl-tile').first().waitFor()
  await shot(g, 'health-empty')
  const ymd = (n) => { const d = new Date(Date.now() - n * 86400000); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}` }
  const rows = []
  for (let i = 1; i <= 30; i++) {
    rows.push(['sleep', 6 + (i % 5) * 0.5, ymd(i)], ['steps', 4000 + (i * 733) % 6000, ymd(i)], ['water', 4 + (i % 4), ymd(i)], ['mood', 2 + (i % 4), ymd(i)], ['weight', 180 - i * 0.05, ymd(i)])
  }
  mahSql(dataDir, rows.map(([m, v, d], i) => ['INSERT INTO health_entries(id,metric,value,day,note,source,created_at) VALUES(?,?,?,?,?,?,?)', [`seed${m}${i}`, m, v, d, '', 'manual', Date.now() / 1000]]))
  await reload(page)
  await page.getByRole('button', { name: 'Health', exact: true }).click()
  await page.locator('.hl-tile').first().waitFor(); await sleep(800)
  await shot(g, 'health-seeded')
  await attempt('health-sources', async () => {
    await page.getByRole('button', { name: 'Connected services' }).click()
    const panel = page.getByRole('region', { name: 'Connected services' })
    await panel.waitFor()
    await panel.getByRole('button', { name: /COROS/ }).click()
    await shot(g, 'health-sources')
  })
})

if (want('library')) await session(async (g) => {
  const { page, api } = g
  await openLibrary(page, 'Skills')
  await shot(g, 'library-skills')
  await attempt('library-skill-editor', async () => {
    await page.getByRole('button', { name: 'New skill' }).click()
    await page.getByPlaceholder('Weekly review', { exact: true }).fill('Weekly review')
    await page.getByPlaceholder('when I ask for a weekly review', { exact: true }).fill('when I ask for a weekly review')
    await page.getByPlaceholder(/^1\. Pull this week/).fill('1. Pull the done todos.\n2. Check the calendar for what slipped.\n3. Draft the summary as bullets.')
    await shot(g, 'library-skill-editor')
    await page.getByRole('button', { name: 'Add as candidate' }).click()
    await sleep(800)
    await page.locator('.skill-row', { hasText: 'Weekly review' }).locator('.skill-head').click()
    await shot(g, 'library-skill-row-open')
  })

  await page.getByRole('tab', { name: 'Agents' }).click(); await sleep(800)
  await shot(g, 'library-agents')
  await attempt('library-agent-editor', async () => {
    await page.getByPlaceholder(/Describe an agent/).fill('!!reply ' + JSON.stringify({ name: 'trip-planner', description: 'Plans trips: flights, stays, a day-by-day itinerary', hue: 200, tools: ['web_search', 'current_time'], skills: [], prompt: 'You plan trips for the user. Check dates and budgets first, and report an itinerary.' }))
    await page.getByRole('button', { name: 'Draft' }).click()
    await page.locator('.agent-editor').waitFor({ timeout: 30000 })
    await shot(g, 'library-agent-editor')
  })

  await api('/jobs', { method: 'POST', body: { name: 'Morning briefing', prompt: 'Summarise my calendar and unread mail', kind: 'cron', cron: '0 9 * * *', enabled: true } })
  await page.getByRole('tab', { name: 'Automations' }).click(); await sleep(1000)
  await shot(g, 'library-automations')
  await attempt('library-automation-editor', async () => {
    await page.getByRole('button', { name: 'New workflow' }).first().click()
    await sleep(800)
    await shot(g, 'library-automation-editor')
    await page.keyboard.press('Escape'); await sleep(400)
  })
  await attempt('library-command-editor', async () => {
    await page.getByRole('button', { name: 'New command' }).first().click()
    await sleep(800)
    await shot(g, 'library-command-editor')
    await page.keyboard.press('Escape'); await sleep(400)
  })
  await attempt('today-scheduled-task', async () => {
    await page.getByRole('button', { name: /^Agent inbox/ }).click()
    const btn = page.getByRole('button', { name: /^Scheduled \(/ })
    await btn.waitFor({ timeout: 20000 })
    if ((await btn.getAttribute('aria-expanded')) !== 'true') await btn.click()
    await sleep(600)
    await shot(g, 'library-scheduled-jobs')
    await page.getByRole('button', { name: 'Schedule a task' }).click(); await sleep(500)
    await shot(g, 'library-scheduled-job-form')
  })
  await openLibrary(page, 'Connectors')
  await page.getByRole('tab', { name: 'Connectors' }).click(); await sleep(800)
  await shot(g, 'library-connectors')
  await page.getByRole('tab', { name: 'Browse', exact: true }).click(); await sleep(2500)
  await shot(g, 'library-connectors-catalog')
  await attempt('library-connector-detail', async () => {
    await page.locator('.connector-grid > *', { hasText: 'GitHub' }).first().getByRole('button', { name: 'Install' }).click()
    await sleep(600)
    await shot(g, 'library-connector-detail')
  })
  await page.getByRole('tab', { name: 'Import', exact: true }).click(); await sleep(1500)
  await shot(g, 'library-connector-import')
  note('### Import dialog full text (verbatim)\n```\n' + (await page.locator('.mcp').innerText()) + '\n```')
})

if (want('inbox')) await session(async (g) => {
  seedJobRuns(g, 3)
  await g.relaunch()
  await g.page.getByRole('button', { name: /^Agent inbox/ }).click()
  await g.page.locator('.agent-inbox .inbox-item').first().waitFor({ timeout: 30000 })
  await shot(g, 'inbox-agent')
  await g.page.locator('.agent-inbox .inbox-item').first().getByRole('button', { name: /Show the report/ }).click().catch(() => {})
  await shot(g, 'inbox-agent-expanded')
})
