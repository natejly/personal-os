import { test, expect } from './fixtures.mjs'
import { openLibrary } from './helpers/library.mjs'
import { assistants, newChat, say, sayAndWait, anyStop } from './helpers/chat.mjs'
import { enterCanvas, spaces } from './helpers/spaces.mjs'

test.describe.configure({ timeout: 300_000 })

const SHOTS = process.env.E2E_SHOTS || ''
/** The chat-completions call that answered this exact user message (auto-learn's extraction call comes later). */
const callFor = (llm, text) => [...llm.calls].reverse().find((c) => c.messages.at(-1)?.role === 'user' && c.messages.at(-1).content === text)
const shot = (page, name) => (SHOTS ? page.screenshot({ path: `${SHOTS}/${name}.png` }) : Promise.resolve())

// The draft endpoint asks the model for JSON; with the mock, the intent itself carries the answer.
const DRAFT = '!!reply ' + JSON.stringify({
  name: 'trip-planner', description: 'Plans trips: flights, stays, a day-by-day itinerary', hue: 200,
  tools: ['web_search', 'current_time'], skills: [],
  prompt: 'You plan trips for the user. Check dates and budgets before suggesting anything, and report an itinerary.'
})

test('Library > Agents: a drafted agent is edited, saved, approved, and a chat speaks as it', async ({ grain }) => {
  const { page, api, llm } = grain
  await openLibrary(page, 'Agents')
  await expect(page.getByRole('heading', { name: 'Built in' })).toBeVisible()
  await expect(page.getByText('researcher', { exact: true })).toBeVisible()
  await shot(page, 'agents-empty')

  // Draft from a line: the editor opens filled in, nothing is saved yet.
  const intent = page.getByPlaceholder(/Describe an agent/)
  await intent.fill(DRAFT)
  await page.getByRole('button', { name: 'Draft' }).click()
  const editor = page.locator('.agent-editor')
  await expect(editor).toBeVisible({ timeout: 30_000 })
  await expect(editor.getByLabel('Name')).toHaveValue('trip-planner')
  await expect(editor.getByLabel('When to hand work to it')).toHaveValue(/Plans trips/)
  await expect(editor.locator('summary').first()).toContainText('2')
  expect((await api('/agents/defs')).custom).toEqual([])
  await shot(page, 'agents-editor')

  // Edit and save: the row appears, unapproved and so not spawnable; approve it.
  await editor.getByLabel('Name').fill('trip planner')
  await editor.getByRole('button', { name: 'Save' }).click()
  const row = page.locator('.skill-row', { hasText: 'trip-planner' })
  await expect(row).toBeVisible()
  await expect(row).toContainText('unapproved')
  await expect(row.getByRole('button', { name: 'Chat' })).toBeDisabled()
  let defs = await api('/agents/defs')
  expect(defs.custom[0]).toMatchObject({ name: 'trip-planner', hue: 200, tools: ['web_search', 'current_time'], approved: false })
  await row.getByRole('button', { name: 'Approve' }).click()
  await expect(row.getByRole('button', { name: 'Unapprove' })).toBeVisible()
  await shot(page, 'agents-approved')

  // Chat as the agent: the first send creates the row with `agent` set, and the model is told who it is.
  await row.getByRole('button', { name: 'Chat' }).click()
  await sayAndWait(page, '!!reply Pack light.', 'Pack light.')
  const convs = await api('/conversations')
  expect(convs[0].settings.agent).toBe('trip-planner')
  const asAgent = callFor(llm, '!!reply Pack light.')
  const sys = asAgent.messages[0].content
  expect(sys).toContain("You are the agent 'trip-planner'")
  expect(sys).toContain('Check dates and budgets')
  // Its tool list bounds the chat's: only the two it names are offered.
  const offered = (asAgent.tools || []).map((t) => t.function.name)
  expect(offered.sort()).toEqual(['current_time', 'web_search'])
  await shot(page, 'agents-chat')

  // A chat that can spawn subagents sees the roster, so it can hand work over by description. (A reply that delegates to
  // workers is not shown it: the roster names agent_spawn, which that reply does not have.)
  await api('/settings', { method: 'PUT', body: { tools: { delegate: 'off' } } })
  await page.reload()
  await newChat(page)
  await sayAndWait(page, '!!reply ok', 'ok')
  expect(callFor(llm, '!!reply ok').messages[0].content).toContain('trip-planner: Plans trips')
  expect(grain.consoleErrors).toEqual([])
})

test('a subagent opens from its card, takes a message while it runs, and shows in the crew ring', async ({ grain }) => {
  const { page, api } = grain
  // Every tool offered up front, no tool_search round. A plain chat reply hands work to workers with delegate and is not
  // offered agent_spawn; with delegate off the chat spawns subagents itself, which is the path this test covers.
  await api('/settings', { method: 'PUT', body: { toolDeferAbove: 0, tools: { delegate: 'off' } } })
  const s = (await spaces(grain))[0]
  const c = await api('/conversations', { method: 'POST', body: { title: 'Crew' } })
  const w = await api(`/canvases/${s.id}/windows`, { method: 'POST', body: { kind: 'chat', ref_id: c.id, x: 64, y: 48, w: 560, h: 600 } })
  await page.reload()
  await enterCanvas(grain)
  const win = page.locator(`[data-window-id="${w.id}"]`)
  await expect(win).toBeVisible()
  // A background child that thinks for a while, so there is a running subagent to talk to.
  const box = win.getByRole('textbox', { name: 'Message' })
  // (The mock reads directives off the whole message, so the parent waits the same 8 s before each of its turns.)
  await box.fill('!!tool agent_spawn {"task": "!!slow 8000 !!reply first draft", "role": "researcher", "background": true}')
  await box.press('Enter')
  // The child sits indented under the reply while it runs, with its live status.
  const row = win.locator('.subagent-thread .subagent-row')
  await expect(row).toHaveCount(1, { timeout: 60_000 })
  await expect(row).toContainText('researcher')
  await shot(page, 'chat-subagent-thread')
  await row.click() // a row opens the child's transcript
  await expect(page.locator('.subagent-panel')).toBeVisible()
  await page.locator('.subagent-panel').getByRole('button', { name: 'Close' }).click()

  // The ring: fold the window to its face; the orchestrator sits in the middle with one child on the ring.
  await win.getByTitle('Shrink to a face').click()
  await expect(win.locator('.crew-sat')).toHaveCount(1)
  await expect(win.locator('.crew-lines line')).toHaveCount(1)
  await shot(page, 'crew-ring')

  // Clicking the child opens its panel, not the chat; a message reaches it before its next turn.
  await win.locator('.crew-sat').click()
  const panel = page.locator('.subagent-panel')
  await expect(panel).toBeVisible()
  await expect(panel).toContainText('researcher')
  await expect(win.locator('.crew-sat')).toHaveCount(1) // the blob did not unfold
  await panel.getByRole('textbox').fill('and also check the weather')
  await panel.getByRole('button', { name: 'Send' }).click()
  await expect(panel.locator('.sa-msg.user').last()).toContainText('and also check the weather', { timeout: 30_000 })
  await expect(panel.locator('.sa-msg.assistant').last()).toContainText('MOCK: and also check the weather', { timeout: 40_000 })
  await shot(page, 'subagent-panel')
  const parent = (await api(`/runs?status=all&conversation_id=${c.id}`)).find((r) => r.kind === 'chat')
  const child = (await api(`/runs/${parent.run_id}/children`))[0]
  const view = await api(`/subagents/${child.run_id}`)
  expect(view.run.status).toBe('done')
  expect(view.messages.filter((m) => m.role === 'user').map((m) => m.content)).toEqual(['!!slow 8000 !!reply first draft', 'and also check the weather'])

  // Finished: the box now routes to the chat that owns the child.
  await expect(panel.getByRole('textbox')).toHaveAttribute('placeholder', /Finished/)
  await panel.getByRole('textbox').fill('!!reply resumed')
  await panel.getByRole('button', { name: 'Send' }).click()
  await expect(panel).toHaveCount(0)
  await expect(assistants(page).last()).toContainText('resumed', { timeout: 40_000 })
  await expect(anyStop(page)).toHaveCount(0, { timeout: 30_000 })
  expect(grain.consoleErrors).toEqual([])
})
