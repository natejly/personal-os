// A fixture whose backend hears a sine tone instead of a microphone and whose speech-to-text is a stub,
// so the recorder, queue, transcript and doc recording run for real with no hardware and no model.
//   import { test, expect } from './helpers/fakemic.mjs'      // grain.stt = { calls, say, fail }
// `grain.stt.say(text)` sets what the next clips transcribe to (default: "stub speech N"); `grain.stt.fail = true` makes it 500.
import { test as base, expect } from '@playwright/test'
import { createServer, request } from 'node:http'
import { join } from 'node:path'
import { launchApp, ROOT } from '../harness.mjs'

function startSttStub(forwardTo) {
  const stt = { calls: 0, fail: false, next: [], say(t) { this.next.push(t) }, url: '', close: () => {} }
  const server = createServer((req, res) => {
    if (req.method === 'POST' && req.url.split('?')[0].endsWith('/audio/transcriptions')) {
      req.resume()
      req.on('end', () => {
        stt.calls++
        if (stt.fail) { res.statusCode = 500; return res.end('{"error":"stub: transcription is down"}') }
        const text = stt.next.shift() ?? `stub speech ${stt.calls}`
        res.setHeader('Content-Type', 'application/json')
        res.end(JSON.stringify({ text, duration: 5, segments: [] }))
      })
      return
    }
    // everything else (chat, models, embeddings) goes to the mock provider
    const up = new URL(forwardTo)
    const fwd = request({ host: up.hostname, port: up.port, path: req.url, method: req.method, headers: { ...req.headers, host: up.host } }, (r) => {
      res.writeHead(r.statusCode ?? 502, r.headers)
      r.pipe(res)
    })
    fwd.on('error', () => { res.statusCode = 502; res.end('{}') })
    req.pipe(fwd)
  })
  return new Promise((resolve) => server.listen(0, '127.0.0.1', () => {
    stt.url = `http://127.0.0.1:${server.address().port}`
    stt.close = () => server.close()
    resolve(stt)
  }))
}

export const test = base.extend({
  grain: async ({}, use, testInfo) => {
    const g = await launchApp({
      name: testInfo.title.replace(/\W+/g, '-').slice(0, 40),
      backendEnv: { PYTHONPATH: `${join(ROOT, 'tests', 'e2e', 'helpers', 'fakemic')}:${join(ROOT, 'backend')}` }
    })
    const stt = await startSttStub(g.llm.url)
    g.stt = stt
    // The recorder's transcription goes to the base URL in settings; point it at the stub (which forwards chat on).
    await g.api('/settings', { method: 'PUT', body: { baseUrl: stt.url } })
    await g.api('/meetings/config', { method: 'PUT', body: { enabled: true, sources: ['mic'], vadGate: false, segmentSeconds: 5, docSegmentSeconds: 5, dictationSegmentSeconds: 5, sttBackend: 'proxy' } })
    await g.api('/meetings/consent', { method: 'POST' })
    try {
      await use(g)
    } finally {
      if (testInfo.status !== testInfo.expectedStatus) {
        try { await testInfo.attach('screenshot', { body: await g.page.screenshot(), contentType: 'image/png' }) } catch {}
        testInfo.attach('backend.log', { body: g.backend.log().slice(-20000), contentType: 'text/plain' })
        testInfo.attach('console.errors', { body: g.consoleErrors.join('\n'), contentType: 'text/plain' })
      }
      stt.close()
      await g.close()
    }
  }
})
export { expect }
