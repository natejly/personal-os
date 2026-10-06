// A scripted provider that sits in front of the mock one, so a test can make the assistant take several steps
// (the mock makes exactly one tool call per turn). `llm.push({calls:[{name,args}], text})` queues one reply, consumed by the
// next chat request that offers tools (title / extraction calls carry none and fall through to the mock).
//   const llm = await scriptLLM(grain)         // points the backend at it
//   llm.push({ calls: [{ name: 'desk_write_file', args: {...} }] }, { text: 'all done' })
// A reply with `calls` is a tool-call turn; with only `text` it is the final answer. When the queue is empty the request is
// forwarded to the mock, which answers "MOCK: ..." as usual. `llm.requests` is every tools-bearing body, for assertions.
import { createServer } from 'node:http'

export async function scriptLLM(grain) {
  const upstream = grain.llm.url
  const queue = []
  const requests = []
  let n = 0
  const server = createServer((req, res) => {
    let raw = ''
    req.on('data', (c) => (raw += c))
    req.on('end', async () => {
      let body = {}
      try { body = JSON.parse(raw || '{}') } catch {}
      const isChat = req.url.split('?')[0].endsWith('/chat/completions')
      if (isChat && body.tools?.length) requests.push(body)
      if (isChat && body.tools?.length && queue.length) {
        const step = queue.shift()
        const calls = (step.calls || []).map((c) => ({ id: `call_s${++n}`, type: 'function', function: { name: c.name, arguments: JSON.stringify(c.args ?? {}) } }))
        const usage = { prompt_tokens: 20, completion_tokens: 10, total_tokens: 30 }
        if (step.delay) await new Promise((r) => setTimeout(r, step.delay))
        if (!body.stream) {
          res.setHeader('Content-Type', 'application/json')
          const message = calls.length ? { role: 'assistant', content: step.text ?? null, tool_calls: calls } : { role: 'assistant', content: step.text ?? '' }
          return res.end(JSON.stringify({ id: 'script', object: 'chat.completion', model: body.model, choices: [{ index: 0, message, finish_reason: calls.length ? 'tool_calls' : 'stop' }], usage }))
        }
        res.setHeader('Content-Type', 'text/event-stream')
        const send = (delta, finish = null) => res.write(`data: ${JSON.stringify({ id: 'script', object: 'chat.completion.chunk', model: body.model, choices: [{ index: 0, delta, finish_reason: finish }] })}\n\n`)
        send({ role: 'assistant' })
        if (step.text) for (const w of step.text.split(/(?<= )/)) send({ content: w })
        calls.forEach((c, i) => {
          send({ tool_calls: [{ index: i, id: c.id, type: 'function', function: { name: c.function.name, arguments: '' } }] })
          send({ tool_calls: [{ index: i, function: { arguments: c.function.arguments } }] })
        })
        send({}, calls.length ? 'tool_calls' : 'stop')
        res.write(`data: ${JSON.stringify({ id: 'script', object: 'chat.completion.chunk', choices: [], usage })}\n\n`)
        res.write('data: [DONE]\n\n')
        return res.end()
      }
      // forward everything else to the mock
      const r = await fetch(upstream + req.url, { method: req.method, headers: { 'Content-Type': 'application/json', Authorization: req.headers.authorization || '' }, body: req.method === 'GET' ? undefined : raw })
      res.statusCode = r.status
      res.setHeader('Content-Type', r.headers.get('content-type') || 'application/json')
      const buf = Buffer.from(await r.arrayBuffer())
      res.end(buf)
    })
  })
  await new Promise((r) => server.listen(0, '127.0.0.1', r))
  const url = `http://127.0.0.1:${server.address().port}`
  await grain.api('/settings', { method: 'PUT', body: { baseUrl: url } })
  const orig = grain.close
  grain.close = async () => { server.close(); server.closeAllConnections?.(); await orig() }
  return { url, requests, queue, push: (...steps) => queue.push(...steps), close: () => server.close() }
}
