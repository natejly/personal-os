import { test, expect } from './fixtures.mjs'
import { newChat, say, assistants, anyStop } from './helpers/chat.mjs'
import { scriptLLM } from './helpers/scriptllm.mjs'

const LOOP = 'core/plan-execute-verify'
const PLAN = JSON.stringify({ steps: [{ title: 'Look it up', done_when: 'it is found' }, { title: 'Write it down', done_when: 'it is written' }] })
const VERIFIED = JSON.stringify({ ok: true, gaps: [] })
const FAILED = JSON.stringify({ ok: false, gaps: ['nothing was written'] })

async function selectLoop(grain) {
  await grain.api('/runtime', { method: 'PUT', body: { slot: 'agent.loop', scope: 'global', value: LOOP } })
}

test('a chat on the plan, execute, verify loop plans, works each step, checks, and answers', async ({ grain }) => {
  const { page } = grain
  const llm = await scriptLLM(grain)
  await selectLoop(grain)
  await newChat(page)
  llm.push({ text: PLAN }, { text: 'Found it.' }, { text: 'Wrote it.' }, { text: VERIFIED }, { text: 'All done: found and written.' })
  await say(page, 'look something up and write it down')
  await expect(assistants(page).last()).toContainText('All done: found and written.', { timeout: 60_000 })
  await expect(anyStop(page)).toHaveCount(0, { timeout: 30_000 })
  // Only the final answer is the reply: the plan and the check were model calls of their own.
  await expect(assistants(page).last()).not.toContainText('"steps"')
  // The plan is the one todo_write feeds; both steps ended done.
  const chat = (await grain.api('/conversations')).find((c) => c.title !== 'New chat') ?? (await grain.api('/conversations'))[0]
  const plan = await grain.api(`/conversations/${chat.id}/plan`)
  expect(plan.steps.map((s) => [s.text, s.status])).toEqual([['Look it up', 'done'], ['Write it down', 'done']])
  // The plan and the check are tool-free calls (the tool list stays, so the cached prefix does); the steps may call tools.
  expect(llm.requests.map((r) => r.tool_choice ?? 'auto')).toEqual(['none', 'auto', 'auto', 'none', 'none'])
})

test('a check that fails twice ends with an answer that claims nothing', async ({ grain }) => {
  const { page } = grain
  const llm = await scriptLLM(grain)
  await selectLoop(grain)
  await newChat(page)
  llm.push({ text: PLAN }, { text: 'Found it.' }, { text: 'Wrote nothing.' }, { text: FAILED }, { text: 'Tried again.' }, { text: FAILED },
    { text: 'Not verified: nothing was written. The task is not complete.' })
  await say(page, 'look something up and write it down')
  await expect(assistants(page).last()).toContainText('Not verified: nothing was written', { timeout: 60_000 })
  await expect(anyStop(page)).toHaveCount(0, { timeout: 30_000 })
  expect(llm.queue).toHaveLength(0)
})
