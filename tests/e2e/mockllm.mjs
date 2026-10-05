// A tiny OpenAI-compatible provider so chat tests are fast and deterministic. Steer it from the prompt:
//   "!!reply <text>"            → the assistant answers <text> (streamed in word chunks)
//   "!!tool <name> <json args>" → the assistant makes one tool call, then answers "MOCK: tool done" on the next turn
//   "!!slow <ms>"               → wait before answering (for Stop / steer / queue tests)
//   "!!think <text>"            → stream <text> as reasoning first; with !!slow the wait sits between thinking and answer
//   "!!fail <status>"           → answer with that HTTP status once per request (retry paths)
//   anything else               → "MOCK: <last user message>"
// Set E2E_LLM=real to skip this and use the LiteLLM proxy on :4000 instead (slow, costs money).
import { createServer } from 'node:http'

export function startMockLLM() {
  const calls = []
  const server = createServer((req, res) => {
    let raw = ''
    req.on('data', (c) => (raw += c))
    req.on('end', async () => {
      const url = req.url.split('?')[0]
      res.setHeader('Content-Type', 'application/json')
      if (url.endsWith('/models')) {
        return res.end(JSON.stringify({ data: [{ id: 'mock-chat' }, { id: 'mock-chat-2' }, { id: 'mock-embed', mode: 'embedding' }] }))
      }
      if (url.endsWith('/model/info')) return res.end(JSON.stringify({ data: [] }))
      let body = {}
      try { body = JSON.parse(raw || '{}') } catch {}
      if (url.endsWith('/embeddings')) {
        const inputs = Array.isArray(body.input) ? body.input : [body.input]
        const data = inputs.map((t, i) => ({ index: i, embedding: vec(String(t)) }))
        return res.end(JSON.stringify({ data, usage: { prompt_tokens: inputs.length * 8 } }))
      }
      if (!url.endsWith('/chat/completions')) {
        res.statusCode = 404
        return res.end('{"error":"mock: unknown route"}')
      }
      calls.push(body)
      const msgs = body.messages || []
      const lastUser = [...msgs].reverse().find((m) => m.role === 'user')
      const lastMsg = msgs[msgs.length - 1]
      const text = typeof lastUser?.content === 'string' ? lastUser.content : (lastUser?.content || []).map((p) => p.text || '').join(' ')
      const fail = text.match(/!!fail (\d+)/)
      const slow = text.match(/!!slow (\d+)/)
      const tool = text.match(/!!tool (\S+) (\{.*\})/s)
      const reply = text.match(/!!reply ([\s\S]*)/)
      // "!!think <text>" streams that text as reasoning first; with !!slow the wait then falls between the thinking and the answer.
      const think = text.match(/!!think ((?:(?!!!)[\s\S])*)/)
      const wait = () => slow && new Promise((r) => setTimeout(r, Number(slow[1])))
      if (!(think && body.stream)) await wait()
      if (fail && !body.__failed) {
        res.statusCode = Number(fail[1])
        return res.end(JSON.stringify({ error: { message: `mock failure ${fail[1]}` } }))
      }
      const toolAlready = lastMsg?.role === 'tool'
      let content = reply ? reply[1].trim() : `MOCK: ${text.replace(/!!\w+.*$/s, '').trim() || '(empty)'}`
      if (toolAlready) content = 'MOCK: tool done'
      const toolCall = tool && !toolAlready ? { id: 'call_mock1', type: 'function', function: { name: tool[1], arguments: tool[2] } } : null
      const usage = { prompt_tokens: 20, completion_tokens: 10, total_tokens: 30 }
      if (!body.stream) {
        const message = toolCall ? { role: 'assistant', content: null, tool_calls: [toolCall] } : { role: 'assistant', content }
        return res.end(JSON.stringify({ id: 'mock', object: 'chat.completion', model: body.model, choices: [{ index: 0, message, finish_reason: toolCall ? 'tool_calls' : 'stop' }], usage }))
      }
      res.setHeader('Content-Type', 'text/event-stream')
      const send = (delta, finish = null) => res.write(`data: ${JSON.stringify({ id: 'mock', object: 'chat.completion.chunk', model: body.model, choices: [{ index: 0, delta, finish_reason: finish }] })}\n\n`)
      send({ role: 'assistant' })
      if (think) {
        send({ reasoning_content: think[1].trim() })
        await wait()
      }
      if (toolCall) {
        send({ tool_calls: [{ index: 0, id: toolCall.id, type: 'function', function: { name: toolCall.function.name, arguments: '' } }] })
        send({ tool_calls: [{ index: 0, function: { arguments: toolCall.function.arguments } }] })
        send({}, 'tool_calls')
      } else {
        for (const w of content.split(/(?<= )/)) send({ content: w })
        send({}, 'stop')
      }
      res.write(`data: ${JSON.stringify({ id: 'mock', object: 'chat.completion.chunk', choices: [], usage })}\n\n`)
      res.write('data: [DONE]\n\n')
      res.end()
    })
  })
  return new Promise((resolve) => server.listen(0, '127.0.0.1', () => resolve({ url: `http://127.0.0.1:${server.address().port}`, calls, close: () => server.close() })))
}

function vec(s) {
  const out = new Array(64).fill(0)
  for (let i = 0; i < s.length; i++) out[(s.charCodeAt(i) + i) % 64] += 1
  const n = Math.hypot(...out) || 1
  return out.map((x) => x / n)
}
