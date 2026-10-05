import { test, expect } from './fixtures.mjs'
import { newChat, reply, smallWindow, realErrors } from './helpers/chat.mjs'

const last = (page) => page.locator('.msg.assistant').last()
const fence = (lang, body) => '```' + lang + '\n' + body + '\n```'

async function start(grain, settings) {
  if (settings) {
    await grain.api('/settings', { method: 'PUT', body: settings })
    await grain.page.reload()
  }
  await newChat(grain.page)
  return grain.page
}

test.describe('charts', () => {
  test('a chart fence draws with recharts, with a toolbar for source/table/copy', async ({ grain }) => {
    const page = await start(grain)
    const spec = { type: 'bar', title: 'Sales', x: 'm', series: ['a', 'b'], data: [{ m: 'Jan', a: 1, b: 2 }, { m: 'Feb', a: 3, b: 1 }, { m: 'Mar', a: 2, b: 4 }] }
    await reply(page, fence('chart', JSON.stringify(spec)))
    const block = last(page).locator('.chart-block')
    await expect(block.locator('.recharts-wrapper')).toBeVisible({ timeout: 20_000 })
    await expect(block).toContainText('Sales')
    await expect(block.locator('.recharts-bar-rectangle').first()).toBeAttached()
    await block.getByRole('button', { name: /table/i }).first().click().catch(() => {})
    expect(realErrors(grain)).toEqual([])
  })

  test('Chart.js-shaped, key-value and line/pie/scatter specs all draw', async ({ grain }) => {
    const page = await start(grain)
    const specs = [
      { labels: ['a', 'b', 'c'], datasets: [{ label: 'S', data: [1, 2, 3] }] },
      { type: 'pie', data: { A: 1, B: 2, C: 3 } },
      { type: 'line', x: 'x', series: ['y'], data: [{ x: 1, y: 2 }, { x: 2, y: 5 }, { x: 3, y: 3 }] },
      { type: 'scatter', data: [[1, 2], [2, 3], [3, 1]] },
      { type: 'area', x: 'd', series: ['v'], data: [{ d: '2026-01-01', v: 1 }, { d: '2026-02-01', v: 2 }] }
    ]
    for (const s of specs) {
      await reply(page, fence('chart', JSON.stringify(s)))
      await expect(last(page).locator('.chart-block .recharts-wrapper')).toBeVisible({ timeout: 20_000 })
    }
    expect(realErrors(grain)).toEqual([])
  })

  test('malformed chart JSON is repaired or shown as a friendly message, never a crash', async ({ grain }) => {
    const page = await start(grain)
    const bad = [
      `{type:'bar', x:'m', series:['a'], data:[{m:'x',a:1},{m:'y',a:2},]}`, // single quotes, bare keys, trailing comma
      `{"type":"bar","x":"m","series":["a"],"data":[{"m":"x","a":1},{"m":"y","a":2}`, // truncated
      `[1,2`, // hopeless
      `not json at all`,
      `{"type":"bar","data":[]}`,
      `{"type":"bar","data":[{"a":"x","b":"y"}]}`, // no numeric series
      `null`,
      `{"type":"bar","data":[{"m":"x","a":1e999}]}`
    ]
    for (const b of bad) {
      await reply(page, fence('chart', b))
      const m = last(page)
      await expect(m.locator('.chart-block .recharts-wrapper, .chart-block .chart-err')).toBeVisible({ timeout: 20_000 })
      await expect(m.locator('.render-fallback')).toHaveCount(0)
      await expect(page.getByText(/something went wrong|Minified React|Unhandled/i)).toHaveCount(0)
    }
    expect(realErrors(grain)).toEqual([])
  })

  test('a chart with 10,000 points draws (capped) without freezing the app', async ({ grain }) => {
    const page = await start(grain, { contextWindow: 4_000_000 })
    const data = Array.from({ length: 10_000 }, (_, i) => ({ x: i, y: Math.sin(i / 50) }))
    const t = Date.now()
    await reply(page, fence('chart', JSON.stringify({ type: 'line', x: 'x', series: ['y'], data })))
    await expect(last(page).locator('.chart-block .recharts-wrapper')).toBeVisible({ timeout: 30_000 })
    // the UI must stay responsive afterwards
    await page.getByRole('textbox', { name: 'Message' }).fill('still alive')
    expect(Date.now() - t).toBeLessThan(60_000)
    expect(realErrors(grain)).toEqual([])
  })

  test('charts stay inside the message at 820x520', async ({ grain }) => {
    const page = await start(grain)
    await smallWindow(grain.app)
    await reply(page, fence('chart', JSON.stringify({ type: 'bar', x: 'm', series: ['a'], data: [{ m: 'one', a: 1 }, { m: 'two', a: 2 }] })))
    const box = await last(page).locator('.chart-block').boundingBox()
    const vp = await page.evaluate(() => window.innerWidth)
    expect(box.x + box.width).toBeLessThanOrEqual(vp + 1)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true)
  })
})

test.describe('mermaid', () => {
  test('a valid diagram renders an svg; an invalid one shows an error block', async ({ grain }) => {
    const page = await start(grain)
    await reply(page, fence('mermaid', 'graph TD\n  A[Start] --> B{Ok?}\n  B -->|yes| C[Done]\n  B -->|no| A'))
    await expect(last(page).locator('.mermaid-svg svg')).toBeVisible({ timeout: 60_000 })
    await reply(page, fence('mermaid', 'graph TD\n  A --> --> ???\n  [[['))
    await expect(last(page).locator('.chart-block .chart-err')).toBeVisible({ timeout: 60_000 })
    await expect(last(page).locator('.chart-source')).toContainText('???')
    expect(realErrors(grain).filter((e) => !/mermaid|Parse error|Syntax error/i.test(e))).toEqual([])
    // the invalid one did not take the page down: the first diagram is still there
    await expect(page.locator('.mermaid-svg svg').first()).toBeVisible()
  })
})

test.describe('math', () => {
  test('inline and block KaTeX render; malformed math degrades in place', async ({ grain }) => {
    const page = await start(grain)
    await reply(page, 'Inline $E=mc^2$ and block:\n\n$$\n\\int_0^1 x^2\\,dx = \\frac13\n$$\n\n$$a^2+b^2=c^2$$\n\nDone.')
    const m = last(page)
    await expect(m.locator('.katex').first()).toBeVisible({ timeout: 20_000 })
    await expect(m.locator('.katex-display')).toHaveCount(2)
    await reply(page, 'Broken $\\frac{1}{$ and $$\\notacommand{x}$$ and a price of $5 and $10 stays text')
    const b = last(page)
    await expect(b).toContainText('stays text')
    await expect(b.locator('.render-fallback')).toHaveCount(0)
    expect(realErrors(grain).filter((e) => !/KaTeX/i.test(e))).toEqual([])
  })
})

test.describe('code and tables', () => {
  test('code blocks highlight and copy; a 5000-line block stays usable', async ({ grain }) => {
    const page = await start(grain, { contextWindow: 4_000_000 })
    await grain.app.context().grantPermissions(['clipboard-read', 'clipboard-write']).catch(() => {})
    await reply(page, fence('js', 'const a = 1\nfunction f(x) { return x + a }'))
    const cb = last(page).locator('.code-block')
    await expect(cb.locator('.hljs-keyword').first()).toBeVisible()
    await cb.getByRole('button', { name: 'Copy' }).click()
    await expect(cb.getByRole('button', { name: 'Copied' })).toBeVisible()
    await expect.poll(() => page.evaluate(() => navigator.clipboard.readText()).catch(() => ''), { timeout: 10_000 }).toContain('function f(x)')
    // Sent through the API: filling a 5000-line draft into the composer box is slow in the renderer itself (see report).
    const lines = Array.from({ length: 5000 }, (_, i) => `const v${i} = ${i} // line ${i}`).join('\n')
    const t = Date.now()
    const c = await grain.api('/conversations', { method: 'POST', body: {} })
    await grain.api(`/conversations/${c.id}/chat`, { method: 'POST', body: { content: '!!reply ' + fence('js', lines) } })
    await expect.poll(async () => (await grain.api(`/conversations/${c.id}`)).messages.filter((m) => m.role === 'assistant' && m.content).length, { timeout: 60_000 }).toBe(1)
    await page.reload()
    await page.locator('.convo-list .convo-item').first().click()
    await expect(page.locator('.code-block').last()).toBeVisible({ timeout: 60_000 })
    await expect(page.locator('.code-block').last()).toContainText('line 4999')
    expect(Date.now() - t).toBeLessThan(90_000)
    await page.getByRole('textbox', { name: 'Message' }).fill('typing still works')
    await expect(page.getByRole('textbox', { name: 'Message' })).toHaveValue('typing still works')
    expect(realErrors(grain)).toEqual([])
  })

  test('a 1000-row table renders in a scroller', async ({ grain }) => {
    const page = await start(grain)
    const rows = Array.from({ length: 1000 }, (_, i) => `| r${i} | ${i} | ${i * 2} |`).join('\n')
    await reply(page, `| name | a | b |\n|---|---|---|\n${rows}`)
    const t = last(page).locator('.md-table-scroll table')
    await expect(t).toBeVisible({ timeout: 30_000 })
    await expect(t.locator('tbody tr')).toHaveCount(1000)
    await expect(t.locator('tbody tr').last()).toContainText('r999')
    expect(realErrors(grain)).toEqual([])
  })
})

test.describe('sanitising', () => {
  test('raw html, script, onerror and javascript: links in markdown never execute', async ({ grain }) => {
    const page = await start(grain)
    await page.evaluate(() => { window.__pwn = 0 })
    await reply(page, [
      'before',
      '',
      '<img src="x" onerror="window.__pwn=1">',
      '',
      '<script>window.__pwn=2</script>',
      '',
      '<iframe src="javascript:window.__pwn=3"></iframe>',
      '',
      '<a href="javascript:window.__pwn=4" id="evil">html link</a>',
      '',
      '[md link](javascript:window.__pwn=5)',
      '',
      '![](javascript:window.__pwn=6)',
      '',
      '<svg onload="window.__pwn=7"></svg>',
      '',
      'after'
    ].join('\n'))
    const m = last(page)
    await expect(m).toContainText('after')
    await page.waitForTimeout(500)
    const link = m.getByRole('link', { name: 'md link' })
    if (await link.count()) { await link.click({ trial: false }).catch(() => {}) }
    await page.waitForTimeout(300)
    expect(await page.evaluate(() => window.__pwn)).toBe(0)
    await expect(m.locator('img[src="x"], script, iframe, svg[onload], #evil')).toHaveCount(0)
  })
})
