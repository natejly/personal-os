// Shared bits for the projects / memory / voice / graph / retrieval specs.
import { execFileSync } from 'node:child_process'
import { join } from 'node:path'
import { readdirSync } from 'node:fs'
import { ROOT } from '../harness.mjs'

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

export const shrink = (grain) => grain.app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].setSize(820, 520))

export const msgText = (m) => (typeof m.content === 'string' ? m.content : (m.content || []).map((p) => p.text || '').join(' '))
export const systemOf = (call) => (call.messages || []).filter((m) => m.role === 'system').map(msgText).join('\n')
export const lastUserOf = (call) => msgText([...(call.messages || [])].reverse().find((m) => m.role === 'user') || { content: '' })
/** The chat-reply requests (not title / learn side calls) whose last user message contains `marker`. */
export const callWith = (llm, marker) => llm.calls.filter((c) => lastUserOf(c).includes(marker) && (c.tools?.length || c.stream))

export async function send(page, text) {
  const box = page.getByRole('textbox', { name: 'Message' })
  await box.fill(text)
  await box.press('Enter')
}

export async function upload(grain, name, text, projectId) {
  const fd = new FormData()
  fd.append('file', new Blob([text], { type: 'text/plain' }), name)
  if (projectId) fd.append('project_id', projectId)
  const r = await fetch(grain.backend.url + '/documents', { method: 'POST', headers: { Authorization: `Bearer ${grain.token}` }, body: fd })
  if (!r.ok) throw new Error(`upload ${name}: ${r.status} ${await r.text()}`)
  return r.json()
}

/** Run one SQL statement against the isolated data dir's sqlite (for rows no route can create). */
export function sqlite(dataDir, sql, params = []) {
  const py = join(ROOT, 'backend', '.venv', 'bin', 'python')
  const file = readdirSync(dataDir).find((f) => f.endsWith('.db') || f.endsWith('.sqlite'))
  const code = 'import sqlite3,sys,json\nc=sqlite3.connect(sys.argv[1]);c.execute(sys.argv[2],json.loads(sys.argv[3]));c.commit()'
  execFileSync(py, ['-c', code, join(dataDir, file), sql, JSON.stringify(params)])
}
