#!/usr/bin/env node
/**
 * Live check of the agent's interactive browser (src/main/agentBrowser.ts + axSnapshot.ts, served by pagefetch.ts).
 *
 * Run from the repo root after `npm run build`:
 *     node scripts/verify-agent-browser.mjs            # all checks
 *     node scripts/verify-agent-browser.mjs forms dialogs   # only checks whose name contains one of the words
 *     KEEP=1 node scripts/verify-agent-browser.mjs     # leave the scratch dir behind
 *
 * It needs public internet (example.com, httpbin.org, wikipedia.org, the-internet.herokuapp.com, w3.org).
 * Prints PASS / FAIL per check and exits non-zero if any FAIL. Deliberately not part of `npm test`.
 *
 * Safety (this exists because an earlier session destroyed user data, so none of it is optional):
 *  - A stand-in "backend" on a free port above 8950 answers /health and records POST /bridge/page, which is how
 *    the script learns the bridge URL and bearer secret. The real backend and its port are never contacted.
 *  - Electron starts through a wrapper main script that points userData at a scratch dir BEFORE loading
 *    out/main/index.js, with <scratch>/data created first so the app cannot fall back to the real profile.
 *    PERSONAL_OS_BACKEND_URL is set, so the app takes no single-instance lock and spawns no backend.
 *  - Only the PID this script spawned is ever killed, by PID. Nothing is killed by name or pattern.
 */
import { spawn } from 'node:child_process'
import { createServer } from 'node:http'
import { createServer as createNetServer } from 'node:net'
import { mkdtempSync, mkdirSync, writeFileSync, existsSync, readdirSync, readFileSync, rmSync, statSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { inflateSync } from 'node:zlib'

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const ELECTRON = join(ROOT, 'node_modules', '.bin', 'electron')
const MAIN = join(ROOT, 'out', 'main', 'index.js')
const only = process.argv.slice(2)
const SCRATCH = mkdtempSync(join(tmpdir(), 'grain-verify-browser-'))
const AUTH = 'verify-' + Math.random().toString(36).slice(2)

if (!existsSync(MAIN)) {
  console.error('out/main/index.js is missing: run `npm run build` first')
  process.exit(2)
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

function freePort(from = 8951) {
  return new Promise((res, rej) => {
    const tryPort = (p) => {
      if (p > 9400) return rej(new Error('no free port'))
      const s = createNetServer()
      s.once('error', () => tryPort(p + 1))
      s.listen(p, '127.0.0.1', () => s.close(() => res(p)))
    }
    tryPort(from + Math.floor(Math.random() * 200))
  })
}

// ---- stand-in backend ------------------------------------------------------------------------------------------
let bridge = null // {url, token, capabilities}
let backendHits = []
const stub = createServer((req, res) => {
  let raw = ''
  req.on('data', (c) => (raw += c))
  req.on('end', () => {
    backendHits.push(`${req.method} ${req.url}`)
    res.setHeader('Content-Type', 'application/json')
    if (req.url === '/bridge/page') {
      try {
        bridge = JSON.parse(raw)
      } catch {}
      return res.end('{"ok":true}')
    }
    // /health and anything else main polls: say yes.
    res.end(JSON.stringify({ ok: true, status: 'ok' }))
  })
})

let child = null
let childLog = ''
function stopElectron() {
  if (child && child.exitCode === null) {
    try {
      process.kill(child.pid, 'SIGTERM') // our own PID only
    } catch {}
  }
}

async function launch(stubPort) {
  const profile = join(SCRATCH, 'profile')
  mkdirSync(join(profile, 'data'), { recursive: true })
  const wrapper = join(SCRATCH, 'wrapper.cjs')
  writeFileSync(
    wrapper,
    `const { app, BrowserWindow } = require('electron')
const fs = require('fs')
app.setPath('userData', ${JSON.stringify(profile)})
require(${JSON.stringify(MAIN)})
// Renderer frame path: the app's own window (preload API, real IPC) subscribes to session "frames1" and we record
// what arrives. Nothing is clicked or typed into that window.
let armed = null
setInterval(async () => {
  try {
    if (!armed) {
      for (const w of BrowserWindow.getAllWindows()) {
        if (w.isDestroyed() || w.webContents.isDestroyed()) continue
        const ok = await w.webContents.executeJavaScript(
          "(() => { if (!window.os || !window.os.agentBrowser) return false; window.__frames = []; window.__unsub = window.os.agentBrowser.subscribe('frames1', (f) => window.__frames.push({ head: f.dataUrl.slice(0, 23), len: f.dataUrl.length, url: f.url, at: f.at })); return true })()"
        ).catch(() => false)
        if (ok) { armed = w; break }
      }
    } else if (!armed.isDestroyed()) {
      const frames = await armed.webContents.executeJavaScript('JSON.stringify(window.__frames || [])')
      fs.writeFileSync(${JSON.stringify(join(SCRATCH, 'frames.json'))}, frames)
      if (fs.existsSync(${JSON.stringify(join(SCRATCH, 'unsub'))})) await armed.webContents.executeJavaScript('window.__unsub && window.__unsub()')
    }
  } catch {}
}, 500)
`
  )
  const env = {
    ...process.env,
    PERSONAL_OS_BACKEND_URL: `http://127.0.0.1:${stubPort}`,
    PERSONAL_OS_AUTH_TOKEN: AUTH,
    PERSONAL_OS_DATA_DIR: join(profile, 'data'),
    GRAIN_USER_DATA: profile
  }
  delete env.ELECTRON_RUN_AS_NODE
  child = spawn(ELECTRON, [wrapper], { env, stdio: ['ignore', 'pipe', 'pipe'], cwd: ROOT })
  child.stdout.on('data', (d) => (childLog += d))
  child.stderr.on('data', (d) => (childLog += d))
  for (let i = 0; i < 120 && !bridge; i++) await sleep(250)
  if (!bridge) throw new Error('Electron never registered its bridge; log tail:\n' + childLog.slice(-1500))
}

// ---- HTTP helpers ----------------------------------------------------------------------------------------------
async function post(path, body, token = bridge.token) {
  const r = await fetch(bridge.url + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: JSON.stringify(body)
  })
  let j = null
  try {
    j = await r.json()
  } catch {}
  return { status: r.status, ...(j ?? {}) }
}
const S = 'v1'
// The public test sites are occasionally slow to finish loading; one retry keeps that from reading as a browser bug.
const open = async (url, extra = {}, session = S) => {
  let r = await post('/browser/open', { session, url, ...extra })
  // (the-internet.herokuapp.com sometimes serves one of its render-blocking scripts in ~30 s, which is the site, not the browser)
  for (let i = 0; i < 2 && r.ok && (r.notes ?? []).some((n) => /still loading/.test(n)); i++) r = await post('/browser/open', { session, url, ...extra })
  return r
}
const act = (a, extra = {}, session = S) => post('/browser/act', { session, action: a, ...extra })
const manage = (a, extra = {}, session = S) => post('/browser/manage', { session, action: a, ...extra })
const snap = (extra = {}, session = S) => post('/browser/snapshot', { session, ...extra })
const preview = (a, extra = {}, session = S) => post('/browser/preview', { session, action: a, ...extra })
const close = (session = S) => post('/browser/close', { session })
const refOf = (snapshot, re) => {
  for (const line of String(snapshot).split('\n')) {
    const m = line.match(/^(e\d+) (.*)$/)
    if (m && re.test(m[2])) return m[1]
  }
  return null
}

// ---- tiny PNG decoder (size + non-blank) -----------------------------------------------------------------------
function pngInfo(b64) {
  const buf = Buffer.from(b64, 'base64')
  if (buf.readUInt32BE(0) !== 0x89504e47) throw new Error('not a PNG')
  const w = buf.readUInt32BE(16)
  const h = buf.readUInt32BE(20)
  const bitDepth = buf[24]
  const colorType = buf[25]
  let off = 8
  const idat = []
  while (off < buf.length) {
    const len = buf.readUInt32BE(off)
    const type = buf.toString('ascii', off + 4, off + 8)
    if (type === 'IDAT') idat.push(buf.subarray(off + 8, off + 8 + len))
    off += 12 + len
  }
  const raw = inflateSync(Buffer.concat(idat))
  const bpp = (colorType === 6 ? 4 : colorType === 2 ? 3 : 1) * (bitDepth / 8)
  const stride = w * bpp
  // Distinct byte values across the (filtered) data is a crude but sufficient "not one flat colour" test.
  const seen = new Set()
  for (let i = 0; i < raw.length; i += 7) seen.add(raw[i])
  return { w, h, distinct: seen.size, bytes: buf.length, stride }
}

// ---- check runner ----------------------------------------------------------------------------------------------
const results = []
async function check(name, fn) {
  if (only.length && !only.some((w) => name.includes(w))) return
  const t0 = Date.now()
  try {
    const evidence = await fn()
    results.push({ name, ok: true })
    console.log(`PASS  ${name}${evidence ? '  -- ' + evidence : ''}  (${Date.now() - t0} ms)`)
  } catch (e) {
    results.push({ name, ok: false })
    console.log(`FAIL  ${name}  -- ${e.message}`)
  }
}
const need = (cond, msg) => {
  if (!cond) throw new Error(msg)
}
const brief = (r) => JSON.stringify({ ok: r.ok, code: r.code, error: r.error, url: r.url }).slice(0, 220)

// ---- the checks ------------------------------------------------------------------------------------------------
async function runChecks() {
  await check('01 bridge registered with browser capability', async () => {
    need(bridge.capabilities?.includes('browser'), 'capabilities=' + JSON.stringify(bridge.capabilities))
    return 'capabilities ' + JSON.stringify(bridge.capabilities)
  })

  await check('02 auth: no/wrong secret is 401', async () => {
    const r = await post('/browser/sessions', {}, 'nope')
    need(r.status === 401, 'status ' + r.status)
    return 'status 401'
  })

  await check('03 open example.com: heading + link ref', async () => {
    const r = await open('https://example.com')
    need(r.ok, brief(r))
    need(/Example Domain|This domain is for use/.test(r.snapshot + r.title), 'no page text:\n' + r.snapshot)
    const ref = refOf(r.snapshot, /^link /)
    need(ref, 'no link ref:\n' + r.snapshot)
    need(/<page id="[a-z0-9]+">/.test(r.snapshot) && /<\/page>/.test(r.snapshot), 'page delimiters missing')
    return `title=${JSON.stringify(r.title)} link=${ref} snapshotId=${r.snapshotId}`
  })

  await check('04 click link navigates; back returns', async () => {
    const s = await snap()
    const ref = refOf(s.snapshot, /^link /)
    const r = await act('click', { ref })
    need(r.ok, brief(r))
    need(/iana\.org/.test(r.url), 'url after click: ' + r.url)
    need(/^heading "/m.test(r.snapshot), 'no heading on the destination page:\n' + r.snapshot)
    const b = await manage('back')
    need(b.ok && /example\.com/.test(b.url), 'back: ' + brief(b))
    return `clicked -> ${r.url}; back -> ${b.url}`
  })

  await check('05 stale ref after navigation', async () => {
    // Refs are only meaningful for the snapshot that issued them: one the page never had is stale_ref.
    const r = await act('click', { ref: 'e999' })
    need(!r.ok && r.code === 'stale_ref', 'got ' + brief(r))
    need(typeof r.snapshot === 'string' && r.snapshot.includes('<page'), 'no fresh snapshot attached')
    return 'code stale_ref with fresh snapshot'
  })

  await check('06 forms: preview risk, type, select/checkbox/radio, submit, echo', async () => {
    const o = await open('https://httpbin.org/forms/post')
    need(o.ok, brief(o))
    const s = o.snapshot
    const name = refOf(s, /^textbox "Customer name/)
    const tel = refOf(s, /^textbox "Telephone/)
    const email = refOf(s, /^textbox "E-mail/)
    const submit = refOf(s, /^button "Submit order"/)
    const radio = refOf(s, /^radio "Medium"/)
    const box = refOf(s, /^checkbox "Bacon"/)
    need(name && tel && email && submit && radio && box, 'missing refs:\n' + s)
    const pv = await preview('type', { ref: name })
    need(pv.ok && pv.risk === 'none', 'type preview ' + JSON.stringify(pv))
    const ps = await preview('click', { ref: submit })
    need(ps.ok && ps.risk === 'submit', 'submit preview ' + JSON.stringify(ps))
    need(/httpbin\.org\/post/.test(ps.formAction ?? ''), 'formAction ' + ps.formAction)
    const pe = await preview('press', { ref: name, key: 'Enter' })
    need(pe.ok && pe.risk === 'submit', 'Enter-in-field preview ' + JSON.stringify(pe))
    let r = await act('type', { ref: name, text: 'Ada Lovelace' })
    need(r.ok, 'type ' + brief(r))
    need(/Ada Lovelace/.test(r.snapshot), 'typed value not echoed in snapshot:\n' + r.snapshot)
    r = await act('type', { ref: tel, text: '555-0100' })
    r = await act('type', { ref: email, text: 'ada@example.com' })
    r = await act('click', { ref: radio })
    need(r.ok, 'radio ' + brief(r))
    r = await act('click', { ref: box })
    need(r.ok && /checkbox "Bacon"[^\n]*\[checked\]/.test(r.snapshot), 'checkbox not checked:\n' + r.snapshot)
    r = await act('click', { ref: submit })
    need(r.ok, 'submit ' + brief(r))
    need(/httpbin\.org\/post/.test(r.url), 'url ' + r.url)
    // (text lines are clipped at 160 chars, so only the first fields of the echoed JSON are readable)
    need(/Ada Lovelace/.test(r.snapshot) && /555-0100/.test(r.snapshot) && /ada@example\.com/.test(r.snapshot), 'echo missing:\n' + r.snapshot.slice(0, 1500))
    return 'submitted; httpbin echoed name, phone, email'
  })

  await check('07 forms: native <select> and press Enter submit', async () => {
    // Wikipedia's search form has a text box; Enter submits it.
    const o = await open('https://en.wikipedia.org/wiki/Main_Page')
    need(o.ok, brief(o))
    const box = refOf(o.snapshot, /^(searchbox|textbox|combobox) "Search/)
    need(box, 'no search box:\n' + o.snapshot.slice(0, 1500))
    let r = await act('type', { ref: box, text: 'Alan Turing' })
    need(r.ok, 'type ' + brief(r))
    r = await act('press', { ref: box, key: 'Enter' })
    need(r.ok, 'press ' + brief(r))
    need(/Turing/.test(r.title + r.url), `after Enter: ${r.url} / ${r.title}`)
    return `Enter -> ${r.url}`
  })

  await check('07b native <select> by label', async () => {
    const o = await open('https://the-internet.herokuapp.com/dropdown')
    need(o.ok, brief(o))
    const sel = refOf(o.snapshot, /^combobox/)
    need(sel, 'no select:\n' + o.snapshot)
    const r = await act('select', { ref: sel, values: ['Option 2'] })
    need(r.ok && /= "Option 2"/.test(r.snapshot), 'select not reflected:\n' + r.snapshot)
    const bad = await act('select', { ref: refOf(r.snapshot, /^combobox/), values: ['No such option'] })
    need(!bad.ok && bad.code === 'not_interactable' && /Option 1/.test(bad.error), 'bad option ' + brief(bad))
    return 'selected "Option 2"; unknown option lists the choices'
  })

  await check('08 long page: caps, query, full, scroll, collapse', async () => {
    const o = await open('https://en.wikipedia.org/wiki/Alan_Turing')
    need(o.ok, brief(o))
    need(o.snapshot.length <= 8200, `default snapshot ${o.snapshot.length} chars`)
    need(/more|cut|omitted|below/i.test(o.snapshot.split('</page>')[1] ?? ''), 'footer does not say what was cut:\n' + o.snapshot.split('</page>')[1])
    const q = await snap({ query: 'Enigma' })
    need(q.ok && /enigma/i.test(q.snapshot) && q.snapshot.length < o.snapshot.length + 500, 'query ' + q.snapshot.length)
    const f = await snap({ full: true })
    need(f.ok && f.snapshot.length > o.snapshot.length && f.snapshot.length <= 12_500, `full ${f.snapshot.length} vs ${o.snapshot.length}`)
    const head = (t) => (t.match(/scrolled (\d+)%/) ?? [])[1]
    const before = head(o.snapshot)
    const sc = await act('scroll', { direction: 'down', amount: 1200 })
    need(sc.ok, 'scroll ' + brief(sc))
    const after = head(sc.snapshot)
    need(Number(after) > Number(before), `scroll line ${before} -> ${after}`)
    const similar = /… \d+ more similar/.test(o.snapshot) || /… \d+ more similar/.test(f.snapshot)
    return `default=${o.snapshot.length} full=${f.snapshot.length} scrolled ${before}%->${after}% similar-collapse=${similar}`
  })

  await check('09 screenshot PNG <=1568 edge, non-blank', async () => {
    await open('https://example.com')
    const r = await manage('screenshot')
    need(r.ok && r.pngBase64, 'no png ' + brief(r))
    const info = pngInfo(r.pngBase64)
    need(Math.max(info.w, info.h) <= 1568, `size ${info.w}x${info.h}`)
    need(info.w === r.width && info.h === r.height, `declared ${r.width}x${r.height} vs real ${info.w}x${info.h}`)
    need(info.distinct > 8, 'looks blank: distinct=' + info.distinct)
    const full = await manage('screenshot', { fullPage: true })
    need(full.ok && Math.max(pngInfo(full.pngBase64).w, pngInfo(full.pngBase64).h) <= 1568, 'fullPage too big')
    return `${info.w}x${info.h}, ${info.bytes} bytes, ${info.distinct} distinct byte values`
  })

  await check('10 tabs: target=_blank, switch, close, cap', async () => {
    let w = await open('https://the-internet.herokuapp.com/windows', { maxTabs: 2 })
    if (!/Click Here/.test(w.snapshot)) w = await open('https://the-internet.herokuapp.com/windows', { maxTabs: 2 }) // site is occasionally slow
    const s = await snap()
    const ref = refOf(s.snapshot, /^link "Click Here"/)
    need(ref, 'no link:\n' + s.snapshot)
    const r = await act('click', { ref })
    need(r.ok && r.tabs === 2, `after target=_blank tabs=${r.tabs} ${brief(r)}`)
    const t = await manage('tabs')
    need(t.ok && t.tabList?.length === 2 && t.tabList.some((x) => x.active), 'tabs ' + JSON.stringify(t.tabList))
    const first = t.tabList[0].tab
    const sw = await manage('switch_tab', { tab: first })
    need(sw.ok && /windows$/.test(sw.url), 'switch_tab ' + brief(sw))
    // cap: another new tab on a 2-tab cap is denied with a note
    const s2 = await snap()
    const ref2 = refOf(s2.snapshot, /^link "Click Here"/)
    const r2 = await act('click', { ref: ref2 })
    need((r2.tabs ?? 0) <= 2 && (r2.notes ?? []).some((n) => /tab/i.test(n)), `cap: tabs=${r2.tabs} notes=${JSON.stringify(r2.notes)}`)
    const t2 = await manage('tabs')
    const second = t2.tabList.find((x) => x.tab !== first)?.tab ?? 2
    const c = await manage('close_tab', { tab: second })
    need(c.ok, 'close_tab ' + brief(c))
    const t3 = await manage('tabs')
    need(t3.tabList.length === 1, 'tabs after close ' + JSON.stringify(t3.tabList))
    return `2 tabs after _blank; cap note ${JSON.stringify(r2.notes)}; closed -> 1`
  })

  await check('11 dialogs: dialog_open blocks, accept/dismiss', async () => {
    const o = await open('https://the-internet.herokuapp.com/javascript_alerts')
    need(o.ok, brief(o))
    const alertBtn = refOf(o.snapshot, /^button "Click for JS Alert"/)
    const confirmBtn = refOf(o.snapshot, /^button "Click for JS Confirm"/)
    need(alertBtn && confirmBtn, 'buttons missing:\n' + o.snapshot)
    const r = await act('click', { ref: alertBtn })
    need(r.ok || r.code === 'dialog_open', 'click alert ' + brief(r))
    const blocked = await snap()
    need(!blocked.ok && blocked.code === 'dialog_open' && /alert/i.test(blocked.error ?? ''), 'snapshot during dialog ' + brief(blocked))
    const acc = await manage('dialog', { accept: true })
    need(acc.ok, 'accept ' + brief(acc))
    need(/successfully clicked an alert/i.test(acc.snapshot), 'result text missing:\n' + acc.snapshot)
    await act('click', { ref: refOf(acc.snapshot, /^button "Click for JS Confirm"/) })
    const dis = await manage('dialog', { accept: false })
    need(dis.ok && /clicked: Cancel/i.test(dis.snapshot), 'dismiss ' + dis.snapshot)
    return 'alert blocked other calls with dialog_open; accept + dismiss both reflected on the page'
  })

  await check('12 downloads: blocked without allow, saved with allow', async () => {
    const dl = join(SCRATCH, 'downloads')
    mkdirSync(dl, { recursive: true })
    // A page whose links are served as attachments (a PDF would render inline instead of downloading).
    const o = await open('https://the-internet.herokuapp.com/download', { downloadDir: dl })
    need(o.ok, brief(o))
    const pick = /^link "[^"]*\.(png|pdf)"/
    const link = refOf(o.snapshot, pick)
    need(link, 'no download link:\n' + o.snapshot.slice(0, 800))
    const fname = (o.snapshot.split('\n').find((l) => l.startsWith(link + ' ')) ?? '').match(/"(.*)"/)?.[1] ?? ''
    const remote = (await (await fetch('https://the-internet.herokuapp.com/download/' + encodeURIComponent(fname))).arrayBuffer()).byteLength
    const denied = await act('click', { ref: link })
    need((denied.notes ?? []).some((n) => /download blocked/.test(n)), 'no blocked note: ' + JSON.stringify(denied.notes))
    need(readdirSync(dl).length === 0, 'file landed without allowDownload')
    const allowed = await act('click', { ref: refOf((await snap()).snapshot, pick), allowDownload: true })
    const note = (allowed.notes ?? []).find((n) => n.startsWith('downloaded: '))
    need(note, 'no downloaded note: ' + JSON.stringify(allowed.notes))
    const p = note.slice('downloaded: '.length)
    need(p.startsWith(dl) && existsSync(p) && statSync(p).size === remote, `file ${p}: ${existsSync(p) ? statSync(p).size : 'missing'} bytes vs ${remote} remote`)
    return `${note} (${statSync(p).size} bytes)`
  })

  await check('13 safety: private/credential/file URLs refused', async () => {
    const bad = [
      `http://127.0.0.1:${new URL(bridge.url).port}/`,
      `http://127.0.0.1:${stubPort}/`,
      'http://localhost/',
      'http://169.254.169.254/latest/meta-data/',
      'http://[::1]/',
      'http://10.0.0.1/',
      'file:///etc/passwd',
      'https://user:pass@example.com/',
      'ftp://example.com/'
    ]
    const out = []
    for (const u of bad) {
      const r = await open(u, {}, 'safety')
      need(!r.ok && ['blocked_host', 'bad_request'].includes(r.code), `${u} -> ${brief(r)}`)
      out.push(`${r.code}`)
    }
    const s = await post('/browser/sessions', {})
    need(!(s.sessions ?? []).some((x) => x.session === 'safety' && /127\.|localhost|169\.254|file:/.test(x.url ?? '')), 'a session navigated: ' + JSON.stringify(s.sessions))
    await close('safety')
    return out.join(',')
  })

  await check('14 safety: redirect to a private host is blocked', async () => {
    // httpbin redirects to wherever we say; the guard must stop the second hop.
    const r = await open('https://httpbin.org/redirect-to?url=http%3A%2F%2F127.0.0.1%3A' + stubPort + '%2F', {}, 'redir')
    const hit = backendHits.some((h) => h === 'GET /' || h.startsWith('GET / '))
    await close('redir')
    need(!hit, 'the stand-in backend received a request from the browser')
    need(!r.ok && r.code === 'blocked_host', 'expected blocked_host, got ' + brief(r))
    return `result ${brief(r)}`
  })

  await check('15 lifecycle: busy on concurrent calls', async () => {
    await open('https://example.com', {}, 'busy')
    const first = open('https://en.wikipedia.org/wiki/Alan_Turing', {}, 'busy')
    await sleep(400) // let it get past URL validation and take the session
    const [a, b] = await Promise.all([first, snap({}, 'busy')])
    const codes = [a, b].filter((x) => !x.ok).map((x) => x.code)
    await close('busy')
    need(codes.includes('busy'), 'no busy: ' + brief(a) + ' ' + brief(b))
    return 'second concurrent call -> busy'
  })

  await check('16 lifecycle: 4th session -> too_many_sessions; close', async () => {
    for (const x of (await post('/browser/sessions', {})).sessions) await close(x.session) // leave nothing from earlier checks
    for (const n of ['s1', 's2', 's3']) {
      const r = await open('https://example.com', {}, n)
      need(r.ok, n + ' ' + brief(r))
    }
    const r4 = await open('https://example.com', {}, 's4')
    need(!r4.ok && r4.code === 'too_many_sessions', brief(r4))
    const list = await post('/browser/sessions', {})
    need(list.ok, 'sessions ' + JSON.stringify(list))
    for (const n of ['s1', 's2', 's3']) await close(n)
    const after = await post('/browser/sessions', {})
    need(!after.sessions.some((x) => /^s[1-4]$/.test(x.session)), 'sessions left ' + JSON.stringify(after.sessions))
    const gone = await snap({}, 's1')
    need(!gone.ok && gone.code === 'no_session', 'after close ' + brief(gone))
    return 'cap at 3, close works, no_session afterwards'
  })

  await check('17 lifecycle: idle teardown', async () => {
    // idleSeconds is clamped to >= 30 by the browser, so this check waits ~35 s
    await open('https://example.com', { idleSeconds: 30 }, 'idle')
    await sleep(35_000)
    const r = await snap({}, 'idle')
    need(!r.ok && r.code === 'no_session', 'still alive: ' + brief(r))
    return 'torn down after idleSeconds=30'
  })

  await check('18 lifecycle: show / hide', async () => {
    await open('https://example.com', {}, 'vis')
    const sh = await manage('show', {}, 'vis')
    need(sh.ok, 'show ' + brief(sh))
    let list = await post('/browser/sessions', {})
    need(list.sessions.find((x) => x.session === 'vis')?.visible === true, 'not visible ' + JSON.stringify(list.sessions))
    const hi = await manage('hide', {}, 'vis')
    need(hi.ok, 'hide ' + brief(hi))
    list = await post('/browser/sessions', {})
    need(list.sessions.find((x) => x.session === 'vis')?.visible === false, 'still visible')
    await close('vis')
    return 'visible true -> false'
  })

  await check('14b safety: a page that frames / loads a private host cannot reach it', async () => {
    const html = `<html><body><h1>probe</h1><iframe src="http://127.0.0.1:${stubPort}/iframe-hit"></iframe><img src="http://127.0.0.1:${stubPort}/img-hit"><script src="http://127.0.0.1:${stubPort}/script-hit"></script></body></html>`
    const r = await open('https://httpbin.org/base64/' + Buffer.from(html).toString('base64').replace(/\+/g, '-').replace(/\//g, '_'), {}, 'frame')
    await sleep(1500) // give any request a chance to arrive
    await close('frame')
    need(r.ok && /probe/.test(r.snapshot), 'probe page did not load: ' + brief(r) + ' ' + (r.snapshot ?? ''))
    const hits = backendHits.filter((h) => /-hit/.test(h))
    need(!hits.length, 'the browser reached the private host: ' + hits.join(', '))
    return 'probe page loaded; stand-in backend saw no iframe/img/script request'
  })

  await check('14c renderer frames over IPC subscribe', async () => {
    const framesFile = join(SCRATCH, 'frames.json')
    const read = () => {
      try {
        return JSON.parse(readFileSync(framesFile, 'utf8'))
      } catch {
        return []
      }
    }
    await open('https://example.com', {}, 'frames1')
    for (let i = 0; i < 20 && !read().length; i++) await sleep(500)
    const got = read()
    need(got.length >= 1, 'no frames reached the renderer')
    need(got[0].head === 'data:image/jpeg;base64,', 'frame is not a JPEG data URL: ' + got[0].head)
    need(/example\.com/.test(got[got.length - 1].url), 'frame url ' + got[got.length - 1].url)
    // unsubscribe: the count must stop growing
    writeFileSync(join(SCRATCH, 'unsub'), '1')
    await sleep(2500)
    const n = read().length
    await sleep(4000)
    const after = read().length
    await close('frames1')
    need(after === n, `frames kept arriving after unsubscribe: ${n} -> ${after}`)
    return `${got.length} frame(s), ${got[0].len} chars each, stopped after unsubscribe`
  })

  await check('19 POST /page reader still works', async () => {
    const r = await post('/page', { url: 'https://example.com' })
    need(r.status === 200 && r.title === 'Example Domain' && /documentation examples/.test(r.text ?? ''), JSON.stringify(r).slice(0, 200))
    const bad = await post('/page', { url: 'http://127.0.0.1/' })
    need(bad.status === 400, 'private page status ' + bad.status)
    return `title=${JSON.stringify(r.title)}`
  })

  await check('20 quit with sessions open exits promptly', async () => {
    await open('https://example.com', {}, 'q1')
    await open('https://example.com', {}, 'q2')
    const t0 = Date.now()
    const done = new Promise((r) => child.once('exit', r))
    stopElectron()
    const code = await Promise.race([done, sleep(8000).then(() => 'timeout')])
    need(code !== 'timeout', 'Electron still running 8 s after SIGTERM')
    return `exited in ${Date.now() - t0} ms`
  })
}

// ---- main ------------------------------------------------------------------------------------------------------
let stubPort = 0
process.on('exit', stopElectron)
process.on('SIGINT', () => {
  stopElectron()
  process.exit(130)
})
try {
  stubPort = await freePort()
  await new Promise((r) => stub.listen(stubPort, '127.0.0.1', r))
  console.log(`scratch ${SCRATCH}; stand-in backend on ${stubPort}`)
  await launch(stubPort)
  console.log(`bridge ${bridge.url} (secret captured, not printed)`)
  if (process.env.SERVE) {
    // Exploration mode: leave Electron up and write the bridge details to a file; stops when that file's sibling `stop` appears.
    writeFileSync(process.env.SERVE, JSON.stringify(bridge))
    while (!existsSync(process.env.SERVE + '.stop')) await sleep(500)
  } else await runChecks()
} catch (e) {
  console.log('FAIL  harness  -- ' + e.message)
  results.push({ name: 'harness', ok: false })
} finally {
  stopElectron()
  stub.close()
  if (process.env.KEEP) console.log('kept ' + SCRATCH)
  else rmSync(SCRATCH, { recursive: true, force: true })
}
const failed = results.filter((r) => !r.ok)
console.log(`\n${results.length - failed.length}/${results.length} passed`)
if (process.env.VERBOSE) console.log(childLog.slice(-4000))
process.exit(failed.length ? 1 : 0)
