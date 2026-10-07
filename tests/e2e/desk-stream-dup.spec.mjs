// Regression: ISSUE-008 — streamed text is duplicated in a default (autonomous) chat
// Found by /qa on 2026-10-06
// Report: .gstack/qa-reports/run-20261006T212932Z/
import { createServer } from 'node:http'
import { test, expect } from './fixtures.mjs'
import { newChat, say } from './helpers/chat.mjs'

test.describe.configure({ timeout: 180_000 })

/**
 * A provider in front of the harness mock that streams a `!!reply` a word at a time with a pause between words, so the
 * window attaches to the run while it is still streaming (the mock alone answers in one burst, before the attach).
 * Everything else goes to the mock untouched.
 */
async function slowStreamingProvider(upstream) {
  const server = createServer((req, res) => {
    let raw = ''
    req.on('data', (c) => (raw += c))
    req.on('end', async () => {
      let body = {}
      try { body = JSON.parse(raw || '{}') } catch {}
      const msgs = body.messages || []
      const lastUser = [...msgs].reverse().find((m) => m.role === 'user')
      const text = typeof lastUser?.content === 'string' ? lastUser.content : ''
      const reply = text.match(/!!reply ([\s\S]*)/)
      if (!req.url.split('?')[0].endsWith('/chat/completions') || !body.stream || !reply) {
        const r = await fetch(upstream + req.url, { method: req.method, headers: { 'Content-Type': 'application/json' }, body: req.method === 'GET' ? undefined : raw })
        res.statusCode = r.status
        res.setHeader('Content-Type', r.headers.get('content-type') || 'application/json')
        return res.end(Buffer.from(await r.arrayBuffer()))
      }
      res.setHeader('Content-Type', 'text/event-stream')
      const send = (delta, finish = null) => res.write(`data: ${JSON.stringify({ id: 'slow', object: 'chat.completion.chunk', model: body.model, choices: [{ index: 0, delta, finish_reason: finish }] })}\n\n`)
      send({ role: 'assistant' })
      for (const w of reply[1].trim().split(/(?<= |\n)/)) {
        send({ content: w })
        await new Promise((r) => setTimeout(r, 8))
      }
      send({}, 'stop')
      res.write(`data: ${JSON.stringify({ id: 'slow', object: 'chat.completion.chunk', choices: [], usage: { prompt_tokens: 20, completion_tokens: 10, total_tokens: 30 } })}\n\n`)
      res.write('data: [DONE]\n\n')
      res.end()
    })
  })
  await new Promise((r) => server.listen(0, '127.0.0.1', r))
  return { url: `http://127.0.0.1:${server.address().port}`, close: () => { server.close(); server.closeAllConnections?.() } }
}

async function deskChat(grain, content) {
  const { page, api } = grain
  const slow = await slowStreamingProvider(grain.llm.url.replace(/\/v1\/?$/, ''))
  await api('/settings', { method: 'PUT', body: { autonomousByDefault: true, baseUrl: slow.url } })
  await page.reload()
  await page.waitForSelector('.sidebar')
  await newChat(page)
  await say(page, '!!reply ' + content)
  return { reply: page.locator('.msg.assistant').last(), slow }
}

test('a streamed 50-row table in a default chat renders exactly 50 rows', async ({ grain }) => {
  const rows = Array.from({ length: 50 }, (_, i) => `| r${i} | ${i} |`).join('\n')
  const { reply, slow } = await deskChat(grain, `| name | n |\n|---|---|\n${rows}`)
  try {
    await expect(reply.locator('tbody tr').last()).toContainText('r49', { timeout: 30_000 })
    // Give a late duplicate the time to paint before counting.
    await grain.page.waitForTimeout(2000)
    await expect(reply.locator('tbody tr')).toHaveCount(50)
  } finally { slow.close() }
})

test('streamed multi-line text in a default chat appears once', async ({ grain }) => {
  const lines = Array.from({ length: 12 }, (_, i) => `plain line ${i}`)
  const { reply, slow } = await deskChat(grain, lines.join('\n'))
  try {
    await expect(reply).toContainText('plain line 11', { timeout: 30_000 })
    await grain.page.waitForTimeout(2000)
    const text = await reply.innerText()
    for (const l of lines) expect(text.match(new RegExp(l + '\\b', 'g')), l).toHaveLength(1)
  } finally { slow.close() }
})

// A table this long keeps the renderer busy while the backend finishes the reply, so the window is far behind the
// stored copy by the time anything refetches the chat: the case that repeated rows in the QA run.
test('a streamed 1000-row table in a default chat renders exactly 1000 rows', async ({ grain }) => {
  const { page, api } = grain
  await api('/settings', { method: 'PUT', body: { autonomousByDefault: true } })
  await page.reload()
  await page.waitForSelector('.sidebar')
  await newChat(page)
  const rows = Array.from({ length: 1000 }, (_, i) => `| r${i} | ${i} | ${i * 2} |`).join('\n')
  await say(page, `!!reply | name | a | b |\n|---|---|---|\n${rows}`)
  const t = page.locator('.msg.assistant').last().locator('.md-table-scroll table')
  await expect(t.locator('tbody tr').last()).toContainText('r999', { timeout: 60_000 })
  await page.waitForTimeout(2000)
  await expect(t.locator('tbody tr')).toHaveCount(1000)
})
