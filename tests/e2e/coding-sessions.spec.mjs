import { test } from './fixtures.mjs'
import { expect, realErrors, newChat } from './helpers/cowork.mjs'
test.describe.configure({ timeout: 120_000 })

// The backend's /coding-sessions routes are stubbed: no `claude` or `opencode` process is ever started.
const session = (over = {}) => ({
  id: 'cs1', agent: 'claude', name: 'Fix the flaky test', status: 'working', attention: 'working', detail: 'Editing tests/flaky.py',
  repo_path: '/tmp/repo', worktree: '/tmp/repo', branch: null, external_id: 'ab12cd34', model: null, permission_mode: null,
  log_tail: 'step one\nstep two', attach_hint: null, created_at: 1, updated_at: 2, ended_at: null, ...over
})
const CORS = { 'access-control-allow-origin': '*', 'access-control-allow-headers': '*', 'access-control-allow-methods': '*' }

test('the Coding sessions list shows a stubbed session; Stop posts to /stop and the row follows', async ({ grain }) => {
  const { page, backend } = grain
  let row = session()
  const posts = []
  await page.route(`${backend.url}/coding-sessions**`, async (route) => {
    const req = route.request()
    if (req.method() === 'OPTIONS') return route.fulfill({ status: 204, headers: CORS })
    const url = new URL(req.url())
    if (req.method() === 'POST' && url.pathname.endsWith('/stop')) {
      posts.push(url.pathname)
      row = session({ status: 'stopped', attention: 'idle', ended_at: 3 })
      return route.fulfill({ status: 200, headers: CORS, contentType: 'application/json', body: JSON.stringify(row) })
    }
    return route.fulfill({ status: 200, headers: CORS, contentType: 'application/json', body: JSON.stringify({ sessions: [row] }) })
  })
  await newChat(page)
  await page.getByRole('button', { name: 'Toggle context panel' }).click()
  const section = page.locator('.ctx-section', { hasText: 'Coding sessions' })
  await expect(section).toContainText('Fix the flaky test')
  await expect(section).toContainText('Claude Code')
  await expect(section).toContainText('Editing tests/flaky.py')
  await section.getByRole('button', { name: /Fix the flaky test/ }).click()
  await expect(section).toContainText('step two')
  await section.getByRole('button', { name: 'Stop', exact: true }).click()
  await expect.poll(() => posts, { timeout: 30_000 }).toEqual(['/coding-sessions/cs1/stop'])
  await expect(section).toContainText('stopped')
  await expect(section.getByRole('button', { name: 'Stop', exact: true })).toHaveCount(0)
  expect(realErrors(grain)).toEqual([])
})
