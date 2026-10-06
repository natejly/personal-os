// Shared helpers for the meetings / activity / health specs: module switches, direct SQLite seeding,
// window sizing. Seeding goes through the backend's own venv python so nothing new is installed.
import { execFileSync } from 'node:child_process'
import { join } from 'node:path'
import { ROOT } from '../harness.mjs'

const PY = join(ROOT, 'backend', '.venv', 'bin', 'python')

/** Run INSERT/UPDATE statements against the isolated install's database. stmts: [[sql, [params]], ...] */
export function sql(dataDir, stmts) {
  const prog = [
    'import sys, json, sqlite3',
    'db, stmts = sys.argv[1], json.loads(sys.stdin.read())',
    'c = sqlite3.connect(db, timeout=30)',
    'c.execute("PRAGMA busy_timeout=30000")',
    'for s, p in stmts: c.execute(s, p)',
    'c.commit(); c.close()'
  ].join('\n')
  execFileSync(PY, ['-c', prog, join(dataDir, 'personal-os.db')], { input: JSON.stringify(stmts), encoding: 'utf8' })
}

/** Turn on the modules that ship hidden. Reload the page afterwards so the renderer sees it. */
export async function enableModules(api) {
  await api('/settings', { method: 'PUT', body: { hiddenViews: [], homeWidgets: { meetings: true, health: true } } })
}

export async function small(app) {
  await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].setSize(820, 520))
}

/** The renderer caches settings; reload so a PUT /settings is picked up. */
export async function reload(page) {
  await page.reload()
  await page.waitForSelector('.sidebar, [class*="sidebar"]')
}

const BENIGN = [/Failed to load resource/]
export const realErrors = (errs) => errs.filter((e) => !BENIGN.some((b) => b.test(e)))

/** Segment rows for a meeting: [{id,channel,t,text,speaker,state}] */
export function seedSegments(dataDir, meetingId, segs) {
  const t0 = Date.now() / 1000
  // A diarized clip carries its speaker both on the row and in the detail's utterances, which is where the backend reads ids from.
  sql(dataDir, segs.map((s, i) => [
    'INSERT INTO meeting_segments(id,meeting_id,channel,seq,t_start,t_end,started_at,duration_ms,text,speaker,state,detail,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
    [s.id ?? `seg${i}`, meetingId, s.channel ?? 'mic', i, s.t ?? i * 10, (s.t ?? i * 10) + 8, t0, 8000, s.text, s.speaker ?? '', s.state ?? 'done',
      JSON.stringify(s.speaker ? { utterances: [{ speaker: s.speaker, start: 0, end: 8, text: s.text }] } : {}), t0]
  ]))
  sql(dataDir, [['UPDATE meetings SET transcript=? WHERE id=?', [segs.map((s) => s.text).join('\n'), meetingId]]])
}

/**
 * Electron raises no Playwright 'download' event for blob links, so record them instead: every
 * <a download>.click() is captured (file name + text) and swallowed. Returns a reader for the list.
 */
export async function captureDownloads(page) {
  await page.evaluate(() => {
    window.__downloads = []
    const urls = new Map()
    const make = URL.createObjectURL.bind(URL)
    URL.createObjectURL = (b) => { const u = make(b); urls.set(u, b); return u }
    HTMLAnchorElement.prototype.click = function () {
      const blob = urls.get(this.href)
      if (this.download && blob) blob.text().then((text) => window.__downloads.push({ name: this.download, text }))
    }
  })
  return () => page.evaluate(() => window.__downloads)
}
