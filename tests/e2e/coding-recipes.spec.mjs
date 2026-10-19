import { mkdtempSync, rmSync, realpathSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { execFileSync } from 'node:child_process'
import { test } from './fixtures.mjs'
import { expect, realErrors } from './helpers/cowork.mjs'
import { menu } from './helpers/shell.mjs'
test.describe.configure({ timeout: 120_000 })

test('palette "Run coding recipe...": opens the recipes section, a saved recipe shows its settings before a run can start', async ({ grain }) => {
  const repo = realpathSync(mkdtempSync(join(tmpdir(), 'grain-e2e-recipe-')))
  try {
    execFileSync('git', ['init', '-q'], { cwd: repo })
    execFileSync('git', ['-c', 'user.email=e2e@example.com', '-c', 'user.name=e2e', 'commit', '-q', '--allow-empty', '-m', 'init'], { cwd: repo })
    const { page } = grain
    const pal = page.getByRole('dialog', { name: 'Command palette' })
    await menu(grain, 'Command Palette…')
    await pal.getByPlaceholder('Go to, create, open…').fill('Run coding recipe')
    await expect(pal.getByRole('option')).toHaveCount(1)
    await page.keyboard.press('Enter')
    const panel = page.getByRole('dialog').getByRole('tabpanel')
    await expect(panel).toContainText('New recipe')
    await expect(panel).toContainText('No recipes yet')

    const body = { name: 'fix-test', description: 'Fix a test', prompt: 'Make this pass: {{test}}', inputs: [{ name: 'test', type: 'string' }],
      repo_path: repo, driver: 'claude', verify: { command: 'true' }, max_retries: 0 }
    await panel.locator('textarea').fill(JSON.stringify(body))
    await panel.getByRole('button', { name: 'Save as a new version' }).click()
    const row = panel.getByTestId('recipe-fix-test')
    await expect(row).toBeVisible()
    await row.getByRole('button', { name: 'Run' }).click()
    const form = panel.getByTestId('recipe-run')
    await form.getByLabel('test').fill('the parser test')
    const start = form.getByRole('button', { name: 'Start run' })
    await expect(start).toBeDisabled()
    await form.getByRole('button', { name: 'Show settings' }).click()
    await expect(panel.getByTestId('recipe-preview')).toBeVisible()
    await expect(start).toBeEnabled()
    expect(realErrors(grain)).toEqual([])
  } finally {
    try { rmSync(repo, { recursive: true, force: true }) } catch {}
  }
})
