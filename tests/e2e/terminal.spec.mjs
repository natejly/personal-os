import { mkdtempSync, rmSync, realpathSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { execFileSync } from 'node:child_process'
import { test } from './fixtures.mjs'
import { expect, realErrors, newChat, say } from './helpers/cowork.mjs'
test.describe.configure({ timeout: 120_000 })
// Test-only override honoured by the unpackaged app: every terminal runs this command instead of a login shell.
process.env.GRAIN_TERM_FAKE_CMD = '/bin/cat'

const SID = '0123456789abcdef'
// A coding session row straight in the isolated database; no agent process is ever started.
const seedSession = (grain, worktree, convo = null) => {
  const now = Date.now() / 1000
  const q = (s) => `'${String(s).replace(/'/g, "''")}'`
  execFileSync('sqlite3', [join(grain.dataDir, 'personal-os.db'), `INSERT INTO coding_sessions(id, agent, external_id, session_id, repo_path, worktree, branch, conversation_id, desk_id, run_id, name, prompt, model, permission_mode, status, detail, log_tail, created_at, updated_at, ended_at, base_sha) VALUES(${q(SID)},'claude','ab12cd34',NULL,${q(worktree)},${q(worktree)},NULL,${convo ? q(convo) : 'NULL'},NULL,NULL,'Terminal test','do it',NULL,NULL,'stopped',NULL,'',${now},${now},${now},NULL);`])
}
// The Workspace panel needs a saved chat, so send one message first.
const openPanel = async (page) => {
  await newChat(page)
  await say(page, '!!reply ok')
  await expect(page.locator('.msg.assistant').last()).toContainText('ok')
  const btn = page.getByRole('button', { name: 'Workspace panel' })
  await expect(btn).toBeEnabled()
  if ((await btn.getAttribute('aria-pressed')) !== 'true') await btn.click()
  await expect(page.locator('.desk-panel')).toBeVisible()
}
// Seeds the session into the chat now open (the Terminal tab lists a chat's own sessions) and reopens the panel so it loads it.
const seedInChat = async (grain, page, worktree) => {
  const convo = execFileSync('sqlite3', [join(grain.dataDir, 'personal-os.db'), 'SELECT id FROM conversations ORDER BY updated_at DESC LIMIT 1;']).toString().trim()
  seedSession(grain, worktree, convo)
  const btn = page.getByRole('button', { name: 'Workspace panel' })
  await btn.click()
  await expect(page.locator('.desk-panel')).toHaveCount(0)
  await btn.click()
  await expect(page.locator('.desk-panel')).toBeVisible()
}
const rowsText = (page) => page.locator('.xterm-rows').first().innerText()

test('the Terminal tab is hidden while there are no coding sessions', async ({ grain }) => {
  const { page } = grain
  await openPanel(page)
  await expect(page.getByTestId('desk-tab-files')).toBeVisible()
  await expect(page.getByTestId('desk-tab-terminal')).toHaveCount(0)
  expect(realErrors(grain)).toEqual([])
})

test('Terminal: the attached view echoes typed text, survives a tab switch and shows the exit line', async ({ grain }) => {
  const wt = realpathSync(mkdtempSync(join(tmpdir(), 'grain-e2e-term-')))
  try {
    const p = grain.page
    await openPanel(p)
    await seedInChat(grain, p, wt)
    const tab = p.getByTestId('desk-tab-terminal')
    await expect(tab).toBeVisible()
    await tab.click()
    // the chat has one session: no chip row, and opening the tab attaches with no click; only the shown view's DOM is mounted
    await expect(p.getByTestId(`term-chip-${SID}`)).toHaveCount(0)
    await expect(p.getByTestId('term-attach')).toHaveCount(0)
    await expect(p.getByTestId('term-shell')).toHaveCount(0)
    await expect(p.locator('.xterm-rows')).toHaveCount(1)
    await expect(p.locator('.xterm-rows').first()).toBeVisible()
    await p.locator('.xterm-helper-textarea').first().focus()
    await p.keyboard.type('hello-from-e2e')
    await p.keyboard.press('Enter')
    await expect.poll(() => rowsText(p)).toContain('hello-from-e2e')

    // another tab and back: the PTY and its screen are still there
    await p.getByTestId('desk-tab-context').click()
    await expect(p.locator('.xterm-rows')).toHaveCount(0)
    await tab.click()
    await expect.poll(() => rowsText(p)).toContain('hello-from-e2e')
    await p.locator('.xterm-helper-textarea').first().focus()
    await p.keyboard.type('second-line')
    await p.keyboard.press('Enter')
    await expect.poll(() => rowsText(p)).toContain('second-line')

    // end of input makes cat exit on its own: the pane says so
    await p.keyboard.press('Control+d')
    await expect.poll(() => rowsText(p)).toContain('[session detached')
    expect(realErrors(grain)).toEqual([])
  } finally {
    try { rmSync(wt, { recursive: true, force: true }) } catch {}
  }
})

test('Terminal: a chat's attachable session shows the attached view', async ({ grain }) => {
  const wt = realpathSync(mkdtempSync(join(tmpdir(), 'grain-e2e-term-')))
  try {
    const p = grain.page
    await openPanel(p)
    await seedInChat(grain, p, wt)
    await p.getByTestId('desk-tab-terminal').click()
    await expect(p.locator('.xterm-rows').first()).toBeVisible()
    expect(realErrors(grain)).toEqual([])
  } finally {
    try { rmSync(wt, { recursive: true, force: true }) } catch {}
  }
})

test('Terminal: opening the tab attaches to the newest attachable session with no click', async ({ grain }) => {
  const wt = realpathSync(mkdtempSync(join(tmpdir(), 'grain-e2e-term-')))
  try {
    const p = grain.page
    await openPanel(p)
    await seedInChat(grain, p, wt)
    await p.getByTestId('desk-tab-terminal').click()
    await expect(p.locator('.xterm-rows').first()).toBeVisible()
    expect(realErrors(grain)).toEqual([])
  } finally {
    try { rmSync(wt, { recursive: true, force: true }) } catch {}
  }
})
