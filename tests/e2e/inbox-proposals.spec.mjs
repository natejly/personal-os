import { test, expect } from './fixtures.mjs'
import { seedJobRuns, sql } from './helpers/home.mjs'

const benign = (e) => /ResizeObserver|favicon/i.test(e)

// Scheduled runs only propose; their proposals are the main content of the Agent inbox.
function seedProposals(grain, rows) {
  const now = Date.now() / 1000
  const q = (s) => `'${String(s).replace(/'/g, "''")}'`
  sql(grain, 'BEGIN;' + rows.map((r, i) =>
    `INSERT INTO proposals(id,run_id,job_id,tool,args,args_digest,status,created_at) VALUES(${q(`prop-${i}`)},${r.run ? q(r.run) : 'NULL'},${r.job ? q(r.job) : 'NULL'},${q(r.tool)},${q(JSON.stringify(r.args))},'d${i}','pending',${now - i});`).join('') + 'COMMIT;')
}

test('Agent inbox renders proposals from scheduled runs and a bare Inbox with none', async ({ grain }) => {
  const errors = []
  grain.page.on('pageerror', (e) => errors.push(String(e)))
  const open = async () => {
    await grain.page.getByRole('button', { name: /^Agent inbox/ }).click()
    await expect(grain.page.locator('.agent-inbox')).toBeVisible()
  }
  // Nothing yet: the card still renders.
  await open()
  await expect(grain.page.locator('.agent-inbox')).toContainText('Inbox empty')
  await grain.page.getByRole('button', { name: 'Close inbox' }).click()

  seedJobRuns(grain, 1)
  seedProposals(grain, [
    { run: 'seed-run-0', job: 'job-0', tool: 'calendar_create', args: { title: 'Fireside chat', start: '2026-10-14T17:00', end: '2026-10-14T18:00' } },
    { run: 'seed-run-0', job: 'job-0', tool: 'gmail_draft', args: { to: 'a@example.com', subject: 'Hi', body: 'Hello' } },
    { run: 'seed-run-0', job: 'job-0', tool: 'todo_add', args: {} },
    { tool: 'calendar_create', args: { title: 'Orphan, no run or job' } }
  ])
  await grain.relaunch()
  const pg = grain.page
  pg.on('pageerror', (e) => errors.push(String(e)))
  await open()
  await expect(pg.locator('.agent-inbox')).toContainText('Needs you')
  await expect(pg.locator('.agent-inbox .inbox-tool', { hasText: 'calendar_create' })).toHaveCount(2)
  await expect(pg.locator('.agent-inbox .inbox-line', { hasText: 'Fireside chat' })).toBeVisible()
  expect(errors).toEqual([])
  expect(grain.consoleErrors.filter((e) => !benign(e))).toEqual([])
})
