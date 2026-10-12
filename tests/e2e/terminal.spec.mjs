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
const seedSession = (grain, worktree) => {
  const now = Date.now() / 1000
  const q = (s) => `'${String(s).replace(/'/g, "''")}'`
  execFileSync('sqlite3', [join(grain.dataDir, 'personal-os.db'), `INSERT INTO coding_sessions(id, agent, external_id, session_id, repo_path, worktree, branch, conversation_id, desk_id, run_id, name, prompt, model, permission_mode, status, detail, log_tail, created_at, updated_at, ended_at, base_sha) VALUES(${q(SID)},'claude','ab12cd34',NULL,${q(worktree)},${q(worktree)},NULL,NULL,NULL,NULL,'Terminal test','do it',NULL,NULL,'stopped',NULL,'',${now},${now},${now},NULL);`])
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
const rowsText = (page) => page.locator('.xterm-rows').first().innerText()

test('the Terminal tab is hidden while there are no coding sessions', async ({ grain }) => {
  const { page } = grain
  await openPanel(page)
  await expect(page.getByTestId('desk-tab-files')).toBeVisible()
  await expect(page.getByTestId('desk-tab-terminal')).toHaveCount(0)
  expect(realErrors(grain)).toEqual([])
})

test('Terminal: shell in the worktree echoes typed text, survives a tab switch, shows the exit line and End drops the view', async ({ grain }) => {
  const wt = realpathSync(mkdtempSync(join(tmpdir(), 'grain-e2e-term-')))
  try {
    seedSession(grain, wt)
    const p = await grain.relaunch()
    await openPanel(p)
    const tab = p.getByTestId('desk-tab-terminal')
    await expect(tab).toBeVisible()
    await tab.click()
    await expect(p.getByTestId(`term-chip-${SID}`)).toBeVisible()
    await expect(p.getByTestId('term-attach')).toBeVisible()
    await expect(p.locator('.xterm-rows')).toHaveCount(0)
    await p.getByTestId('term-shell').click()
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

    // end of input makes cat exit on its own: the pane says so; End then drops the view
    await p.keyboard.press('Control+d')
    await expect.poll(() => rowsText(p)).toContain('[shell exited]')
    await p.getByTestId('term-end').click()
    await expect(p.locator('.xterm-rows')).toHaveCount(0)
    await expect(p.getByTestId('term-end')).toHaveCount(0)
    expect(realErrors(grain)).toEqual([])
  } finally {
    try { rmSync(wt, { recursive: true, force: true }) } catch {}
  }
})

test('Terminal: picking an attachable session shows the attached view without pressing Attach', async ({ grain }) => {
  const wt = realpathSync(mkdtempSync(join(tmpdir(), 'grain-e2e-term-')))
  try {
    seedSession(grain, wt)
    const p = await grain.relaunch()
    await openPanel(p)
    await p.getByTestId('desk-tab-terminal').click()
    await p.getByTestId(`term-chip-${SID}`).click()
    await expect(p.locator('.xterm-rows').first()).toBeVisible()
    expect(realErrors(grain)).toEqual([])
  } finally {
    try { rmSync(wt, { recursive: true, force: true }) } catch {}
  }
})
