// Regression: ISSUE-012 — a desk approval card claims the chat read untrusted content
// Found by /qa on 2026-10-06
// Report: .gstack/qa-reports/run-20261006T212932Z/
import { createServer } from 'node:http'
import { test, expect } from './fixtures.mjs'
import { newChat, say } from './helpers/chat.mjs'
import { enableModules } from './helpers/mah.mjs'

test.describe.configure({ timeout: 180_000 })

test('an autonomy-forced approval card does not say the chat read untrusted content', async ({ grain }) => {
  const { page, api } = grain
  await enableModules(api)
  await api('/settings', { method: 'PUT', body: { autonomousByDefault: true, toolDeferAbove: 0 } })
  await page.reload()
  await page.waitForSelector('.sidebar')
  await newChat(page)
  await say(page, '!!tool health_log {"metric":"sleep","value":7.25,"note":"copy check"}')
  const card = page.locator('.tc-approval').first()
  await expect(card).toBeVisible({ timeout: 60_000 })
  await expect(card).toContainText('needs your OK')
  await expect(card).not.toContainText('untrusted')
  const [c] = await api('/conversations?include_desks=true')
  expect((await api(`/conversations/${c.id}`)).settings.tainted).toBeFalsy()
})

/** A provider in front of the harness mock that answers a tool-offering request with the next scripted step, then plain text. */
async function scriptedProvider(upstream, steps) {
  let n = 0
  const server = createServer((req, res) => {
    let raw = ''
    req.on('data', (c) => (raw += c))
    req.on('end', async () => {
      let body = {}
      try { body = JSON.parse(raw || '{}') } catch {}
      if (!req.url.split('?')[0].endsWith('/chat/completions') || !body.stream || !body.tools?.length) {
        const r = await fetch(upstream + req.url, { method: req.method, headers: { 'Content-Type': 'application/json' }, body: req.method === 'GET' ? undefined : raw })
        res.statusCode = r.status
        res.setHeader('Content-Type', r.headers.get('content-type') || 'application/json')
        return res.end(Buffer.from(await r.arrayBuffer()))
      }
      const step = steps[n++] ?? { text: 'Done.' }
      res.setHeader('Content-Type', 'text/event-stream')
      const send = (delta, finish = null) => res.write(`data: ${JSON.stringify({ id: 's', object: 'chat.completion.chunk', model: body.model, choices: [{ index: 0, delta, finish_reason: finish }] })}\n\n`)
      send({ role: 'assistant' })
      if (step.call) {
        send({ tool_calls: [{ index: 0, id: `call_s${n}`, type: 'function', function: { name: step.call.name, arguments: '' } }] })
        send({ tool_calls: [{ index: 0, function: { arguments: JSON.stringify(step.call.args) } }] })
        send({}, 'tool_calls')
      } else {
        send({ content: step.text })
        send({}, 'stop')
      }
      res.write(`data: ${JSON.stringify({ id: 's', object: 'chat.completion.chunk', choices: [], usage: { prompt_tokens: 20, completion_tokens: 10, total_tokens: 30 } })}\n\n`)
      res.write('data: [DONE]\n\n')
      res.end()
    })
  })
  await new Promise((r) => server.listen(0, '127.0.0.1', r))
  return { url: `http://127.0.0.1:${server.address().port}`, close: () => { server.close(); server.closeAllConnections?.() } }
}

test('a card raised after the same reply read untrusted content says so, live, with See why', async ({ grain }) => {
  const { page, api } = grain
  await enableModules(api)
  const llm = await scriptedProvider(grain.llm.url, [
    { call: { name: 'search_documents', args: { query: 'anything' } } },
    { call: { name: 'health_log', args: { metric: 'sleep', value: 7.25, note: 'taint check' } } }
  ])
  try {
    await api('/settings', { method: 'PUT', body: { toolDeferAbove: 0, baseUrl: llm.url } })
    await page.reload()
    await page.waitForSelector('.sidebar')
    await newChat(page)
    await say(page, 'look something up, then log my sleep')
    const card = page.locator('.tc-approval').first()
    await expect(card).toBeVisible({ timeout: 60_000 })
    // Nothing reloaded the chat since the read: the window learned of it from the stream.
    await expect(card).toContainText('This chat has read untrusted content')
    await expect(card.getByRole('button', { name: 'See why' })).toBeVisible()
  } finally { llm.close() }
})
