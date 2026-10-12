// Screenshot tool, not an assertion test: seeds a realistic sidebar, jobs and memories, then captures the
// sidebar, project memory and Settings > Memory. Every step soft-skips what is missing.
//   SHOTS_DIR (default /home/kyle/.claude/jobs/89becc36/tmp/shots), SHOTS_PREFIX (default "before")
import { test } from './fixtures.mjs'
import { mkdirSync } from 'node:fs'
import { join } from 'node:path'

test.describe.configure({ timeout: 240_000 })

const DIR = process.env.SHOTS_DIR || '/home/kyle/.claude/jobs/89becc36/tmp/shots'
const PREFIX = process.env.SHOTS_PREFIX || 'before'

const LONG = [
  'Prefers concise answers with the conclusion first, then supporting detail only when asked; never pad replies with restated questions or closing summaries.',
  'The quarterly planning doc lives in the shared drive under Finance / FY26 / Planning and is owned by Dana, who wants edits as suggestions rather than direct changes so the history stays readable.',
  'Works Pacific time, blocks 9 to 11 for deep work, and does not want meetings or notifications scheduled in that window unless the sender marks them urgent.'
]
const GLOBAL_MEMS = [
  ['Name is Alex; lives in Seattle.', true], ['Prefers metric units.', false], [LONG[0], true], ['Uses a 2022 MacBook Pro.', false],
  [LONG[1], false], ['Favourite editor is Neovim, but writes docs in plain markdown.', false], [LONG[2], false], ['Allergic to shellfish.', false]
]
const PROJECT_MEMS = [
  'Launch date is the first Tuesday of March; marketing needs final copy two weeks before.',
  'Pricing tiers: Free, Team at 12 per seat per month, and Enterprise on request, billed annually. Discounts above 50 seats need sign-off from finance and a written justification.',
  'Staging deploys go through the release branch only.',
  'Design reviews happen Thursdays; bring the design file link and a one-paragraph summary of what changed since the last review so the group can decide quickly.'
]

test('visual: sidebar, project memory, settings memory, jobs', async ({ grain }) => {
  const { page, api, app } = grain
  mkdirSync(DIR, { recursive: true })
  const shot = async (name, opts = {}) => {
    try { await page.screenshot({ path: join(DIR, `${PREFIX}-${name}.png`), ...opts }) } catch (e) { console.log('shot failed', name, e.message) }
  }
  const soft = async (label, fn) => { try { await fn() } catch (e) { console.log('skipped:', label, String(e.message).split('\n')[0]) } }

  // ---- seed
  const projects = []
  for (const name of ['Website relaunch', 'Q3 budget review', 'Thesis']) projects.push(await api('/projects', { method: 'POST', body: { name } }))
  const projChats = [['Homepage copy pass', 'Navigation audit'], ['Variance by team', 'Vendor renewals and a very long chat title that should truncate'], ['Chapter 3 outline', 'Reading list']]
  for (const [i, p] of projects.entries()) {
    for (const title of projChats[i]) await api('/conversations', { method: 'POST', body: { title, project_id: p.id } })
  }
  for (const title of ['Trip to Lisbon', 'Fix flaky test', 'Draft reply to landlord', 'Recipe ideas', 'Explain CRDTs', 'Weekly review notes']) {
    await api('/conversations', { method: 'POST', body: { title } })
  }
  const now = Date.now() / 1000
  const jobBodies = [
    { name: 'Morning brief', prompt: 'Summarise my calendar and inbox for today.', kind: 'cron', cron: '0 8 * * *', enabled: true },
    { name: 'Renew passport reminder', prompt: 'Remind me to renew my passport.', kind: 'once', run_at: now + 86400 * 3, enabled: true },
    { name: 'Weekly dependency audit', prompt: 'Check for outdated dependencies and propose updates.', kind: 'cron', cron: '0 9 * * 1', enabled: false }
  ]
  const madeJobs = []
  for (const j of jobBodies) await soft('job ' + j.name, async () => { madeJobs.push(await api('/jobs', { method: 'POST', body: j })) })
  await soft('run job', async () => {
    const id = madeJobs[0].id
    await api(`/jobs/${id}/run`, { method: 'POST', body: {} })
    for (let i = 0; i < 60; i++) {
      await new Promise((r) => setTimeout(r, 1000))
      const runs = await api(`/jobs/${id}/runs`)
      const list = Array.isArray(runs) ? runs : (runs.runs ?? [])
      if (list.length && list.every((r) => !['running', 'queued', 'pending'].includes(r.status))) break
    }
  })
  for (const [content, pinned] of GLOBAL_MEMS) await api('/memories', { method: 'POST', body: { content, pinned } })
  for (const content of PROJECT_MEMS) await api('/memories', { method: 'POST', body: { content, project_id: projects[0].id } })

  await app.evaluate(({ BrowserWindow }) => { for (const w of BrowserWindow.getAllWindows()) if (!w.isDestroyed()) { w.setSize(1280, 800); w.center() } })
  await page.setViewportSize({ width: 1280, height: 800 }).catch(() => {})
  await page.reload()
  await page.waitForSelector('.sidebar, [class*="sidebar"]')
  await page.waitForTimeout(1500)

  // ---- a. sidebar
  await shot('sidebar')

  // ---- b. hovers
  await soft('hover chats header', async () => {
    await page.getByRole('button', { name: 'Chats', exact: true }).first().hover()
    await page.waitForTimeout(400)
    await shot('sidebar-hover-chats')
  })
  await soft('hover project', async () => {
    await page.locator('.project-item', { hasText: 'Website relaunch' }).first().hover()
    await page.waitForTimeout(400)
    await shot('sidebar-hover-project')
  })

  // ---- c. project memory (a section at the foot of the project page in this build; a tab if one exists)
  await soft('project memory', async () => {
    await page.locator('.project-item', { hasText: 'Website relaunch' }).first().click()
    await page.waitForTimeout(1200)
    const tab = page.getByRole('tab', { name: /^Memory/ })
    if (await tab.count()) await tab.first().click()
    const sec = page.locator('section[aria-label*="emory"]').last()
    if (await sec.count()) await sec.scrollIntoViewIfNeeded()
    else await page.locator('main').evaluate((m) => { m.scrollTop = m.scrollHeight })
    await page.waitForTimeout(800)
    await shot('project-memory')
  })

  // ---- d. settings > memory
  await soft('settings memory', async () => {
    await page.locator('.settings-btn').click()
    await page.getByRole('dialog', { name: 'Settings' }).getByRole('tab', { name: 'Memory' }).click()
    await page.waitForTimeout(1200)
    await shot('settings-memory')
    await page.keyboard.press('Escape')
    await page.waitForTimeout(400)
  })

  // ---- e. jobs (absent in the current build)
  await soft('jobs', async () => {
    const item = page.locator('nav').getByText('Jobs', { exact: true }).first()
    if (!(await item.count())) throw new Error('no Jobs nav item')
    await item.click()
    await page.waitForTimeout(1200)
    await shot('jobs')
    await soft('job detail', async () => {
      const card = page.locator('.job-card', { hasText: 'at 08:00' }).first()
      const target = (await card.count()) ? card : page.locator('main button, main [role="button"]').first()
      await target.click()
      await page.waitForTimeout(1000)
      await shot('jobs-detail')
      await soft('job edit', async () => {
        await page.getByRole('button', { name: 'Edit', exact: true }).click()
        await page.waitForTimeout(600)
        await shot('jobs-edit')
      })
    })
  })
})
