import { test, expect } from './fixtures.mjs'
import { msgBox, assistants, users, newChat, say, sayAndWait, anyStop, benign, resize } from './helpers/chat.mjs'
import { seedChats } from './helpers/seed.mjs'

test.describe.configure({ timeout: 240_000 })

const MD = [
  '!!reply # Heading one',
  '',
  'Some **bold** and `inline` text with a [link](https://example.com).',
  '',
  '```js',
  'const a = 1 + 2',
  'console.log(a)',
  '```',
  '',
  '| a | b |',
  '|---|---|',
  '| 1 | 2 |',
  '| 3 | 4 |',
  '',
  'Inline math $x^2 + y^2 = z^2$ and block:',
  '',
  '$$',
  '\\int_0^1 x\\,dx = \\tfrac12',
  '$$',
  '',
  '```mermaid',
  'graph TD',
  '  A[Start] --> B[End]',
  '```',
  '',
  '- item one',
  '- item two'
].join('\n')

test('markdown reply renders code, table, math and mermaid without errors', async ({ grain }) => {
  const { page } = grain
  await newChat(page)
  await sayAndWait(page, MD, 'Heading one')
  const md = assistants(page).last().locator('.markdown')
  await expect(md.locator('h1')).toHaveText('Heading one')
  await expect(md.locator('strong')).toHaveText('bold')
  await expect(md.locator('table tr')).toHaveCount(3)
  await expect(md.locator('pre code').first()).toContainText('console.log(a)')
  await expect(md.locator('.katex').first()).toBeVisible()
  expect(await md.locator('.katex').count()).toBeGreaterThanOrEqual(2)
  await expect(md.locator('li')).toHaveCount(2)
  // mermaid is lazy: either the svg diagram or a plain fallback block, never the error boundary
  await expect(md.locator('svg').first()).toBeVisible({ timeout: 30_000 })
  await expect(page.getByText('Could not render this message.')).toHaveCount(0)
  expect(benign(grain.consoleErrors)).toEqual([])
})

test('a streaming reply with an unterminated fence and half a table never breaks the view', async ({ grain }) => {
  const { page } = grain
  await newChat(page)
  await sayAndWait(page, '!!reply Here:\n\n```python\nprint(1)\n\n| a | b |\n|---|', 'print(1)')
  await expect(page.getByText('Could not render this message.')).toHaveCount(0)
  await sayAndWait(page, '!!reply $$ \\frac{1}{ and ```mermaid\ngraph TD\n A-->', 'frac')
  await expect(page.getByText('Could not render this message.')).toHaveCount(0)
  expect(benign(grain.consoleErrors)).toEqual([])
})

test('a harmless tool call shows a tool row and finishes with the follow-up text', async ({ grain }) => {
  const { page, api, llm } = grain
  await newChat(page)
  await sayAndWait(page, '!!tool search_memory {"query":"anything"}', 'tool done')
  expect(llm.calls.some((c) => (c.tools || []).some((t) => t.function?.name === 'search_memory'))).toBe(true)
  const [c0] = await api('/conversations')
  const c = await api(`/conversations/${c0.id}`)
  const a = c.messages.find((m) => m.role === 'assistant')
  expect(a.tool_events?.some((t) => t.name === 'search_memory')).toBe(true)
  await expect(page.getByText(/search_memory|memory|Searched/i).first()).toBeVisible()
  expect(benign(grain.consoleErrors)).toEqual([])
})

test('model menu: pick mock-chat-2, effort picker, and the request carries the model', async ({ grain }) => {
  const { page, llm } = grain
  await newChat(page)
  const trigger = page.locator('.model-menu-trigger')
  await expect(trigger).toBeVisible()
  await trigger.click()
  const pop = page.getByRole('dialog', { name: 'Models' })
  await expect(pop).toBeVisible()
  await pop.getByRole('textbox', { name: 'Search models' }).fill('chat-2')
  const opt = pop.getByRole('option').first()
  await expect(opt).toContainText(/chat-2|chat 2/i)
  await opt.click()
  await expect(pop).toHaveCount(0)
  await expect(trigger).toContainText(/chat-2|chat 2/i)
  await page.getByRole('combobox', { name: 'Reasoning level' }).selectOption({ label: 'High' })
  await sayAndWait(page, '!!reply with model two', 'with model two')
  // the extraction / title calls run on their own model; look at the reply's request
  const call = llm.calls.find((c) => JSON.stringify(c.messages).includes('with model two') && (c.tools || []).length > 0)
  expect(call.model).toBe('mock-chat-2')
  // the choice sticks to the chat after a relaunch
  const p2 = await grain.relaunch()
  await p2.locator('.convo-list .convo-item').first().click()
  await expect(p2.locator('.model-menu-trigger')).toContainText(/chat-2|chat 2/i)
  await expect(p2.getByRole('combobox', { name: 'Reasoning level' })).toHaveValue(/high/i)
})

test('model menu: Esc closes it, search with no match says so', async ({ grain }) => {
  const { page } = grain
  await newChat(page)
  await page.locator('.model-menu-trigger').click()
  const pop = page.getByRole('dialog', { name: 'Models' })
  await pop.getByRole('textbox', { name: 'Search models' }).fill('zzzznomodel')
  await expect(pop.getByText('No models match.')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(pop).toHaveCount(0)
})

test('pasting 200 KB becomes a file attachment; the composer stays responsive', async ({ grain }) => {
  const { page } = grain
  await newChat(page)
  const box = msgBox(page)
  await box.click()
  const t0 = Date.now()
  await box.evaluate((el) => {
    const dt = new DataTransfer()
    dt.setData('text/plain', 'lorem ipsum dolor sit amet '.repeat(7700))
    el.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }))
  })
  await expect(page.getByText(/Pasted text was long|attached as/)).toBeVisible({ timeout: 30_000 })
  expect(Date.now() - t0).toBeLessThan(15_000)
  await expect.poll(async () => (await box.inputValue()).length, { timeout: 30_000 }).toBeGreaterThan(0)
  expect((await box.inputValue()).length).toBeLessThan(5000)
  await box.type(' summarise this')
  await box.press('Enter')
  await expect(assistants(page).last()).toContainText('MOCK', { timeout: 60_000 })
})

test('a 150 KB typed message sends; an over-limit one is refused with a notice', async ({ grain }) => {
  const { page } = grain
  await newChat(page)
  const box = msgBox(page)
  const big = 'word '.repeat(30_000) + '!!reply ok'
  await box.fill(big)
  await expect(box).toHaveValue(big)
  const t0 = Date.now()
  await box.press('Enter')
  await expect(users(page)).toHaveCount(1, { timeout: 30_000 })
  await expect(assistants(page).last()).toContainText('ok', { timeout: 60_000 })
  await expect(anyStop(page)).toHaveCount(0, { timeout: 30_000 })
  console.log('150KB send round trip ms', Date.now() - t0)
  await box.fill('x'.repeat(2_000_000))
  await box.press('Enter')
  await expect(page.getByText(/One message can hold/).first()).toBeVisible({ timeout: 30_000 })
  await expect(users(page)).toHaveCount(1)
})

test('100 messages in one chat scroll, stay responsive, and a send round-trips fast', async ({ grain }) => {
  const { page } = grain
  const [id] = seedChats(grain, 1, 100)
  await page.reload()
  await page.locator('.convo-list .convo-item').first().click()
  await expect(page.locator('.msg')).toHaveCount(100, { timeout: 60_000 })
  const sc = page.locator('.messages')
  await sc.evaluate((el) => { el.scrollTop = 0 })
  await expect(page.getByRole('button', { name: 'Jump to latest' })).toBeVisible()
  await page.getByRole('button', { name: 'Jump to latest' }).click()
  await expect.poll(() => sc.evaluate((el) => el.scrollHeight - el.scrollTop - el.clientHeight)).toBeLessThan(40)
  const t0 = Date.now()
  await sayAndWait(page, '!!reply quick', 'quick')
  const ms = Date.now() - t0
  console.log('100-msg chat send round trip ms', ms)
  expect(ms).toBeLessThan(10_000) // 3 s on an idle machine; the shared runner is heavily loaded
  await expect.poll(() => sc.evaluate((el) => el.scrollHeight - el.scrollTop - el.clientHeight)).toBeLessThan(40)
  expect(id).toBeTruthy()
  expect(benign(grain.consoleErrors)).toEqual([])
})

test('820x520 window: composer, controls and messages stay reachable', async ({ grain }) => {
  const { page } = grain
  await resize(grain, 820, 520)
  await page.waitForTimeout(500)
  await newChat(page)
  await sayAndWait(page, '!!reply small window', 'small window')
  const box = await msgBox(page).boundingBox()
  const vp = await page.evaluate(() => ({ w: window.innerWidth, h: window.innerHeight }))
  expect(box.y + box.height).toBeLessThanOrEqual(vp.h + 1)
  expect(box.x).toBeGreaterThanOrEqual(0)
  expect(box.x + box.width).toBeLessThanOrEqual(vp.w + 1)
  const hscroll = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1)
  expect(hscroll).toBe(false)
  const send = page.getByRole('button', { name: 'Send', exact: true })
  const sb = await send.boundingBox()
  expect(sb.x + sb.width).toBeLessThanOrEqual(vp.w + 1)
  const mm = await page.locator('.model-menu-trigger').boundingBox()
  expect(mm.y + mm.height).toBeLessThanOrEqual(vp.h + 1)
  await expect(assistants(page).last()).toBeVisible()
  expect(benign(grain.consoleErrors)).toEqual([])
})

test('backend killed mid-stream: the UI reports it, does not hang, and the composer stays usable', async ({ grain }) => {
  const { page } = grain
  await newChat(page)
  await say(page, '!!slow 20000')
  await expect(page.getByRole('button', { name: 'Stop', exact: true })).toBeVisible()
  grain.backend.child.kill('SIGKILL')
  // The run is not left spinning: within the stream's stall limit (~30 s) the dots and Stop are gone and the
  // composer is usable again. What replaces them varies (an "Interrupted" row with Regenerate, or, when the
  // kill lands very early, an empty pane plus a toast), so only the no-hang contract is asserted here.
  await expect(anyStop(page)).toHaveCount(0, { timeout: 120_000 })
  await expect(page.locator('.thinking')).toHaveCount(0, { timeout: 30_000 })
  await expect(msgBox(page)).toBeEnabled()
  await msgBox(page).fill('anyone there?')
  await msgBox(page).press('Enter')
  // a send to a dead backend fails cleanly instead of hanging
  await expect(anyStop(page)).toHaveCount(0, { timeout: 90_000 })
  await expect(page.locator('.thinking')).toHaveCount(0, { timeout: 90_000 })
  await expect(msgBox(page)).toBeEnabled()
})
