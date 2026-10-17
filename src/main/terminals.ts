import { app, BrowserWindow } from 'electron'
import { statSync } from 'fs'
import { createTermHub, runBridgeCommand, type Pty, type Screen, type SessionInfo, type TermHub } from '../shared/terminal'
import { backendToken, backendUrl } from './backend'
import { handle } from './ipc'

type NodePty = typeof import('node-pty')
let nodePty: NodePty | null | undefined

/** node-pty is a native module, loaded on first use so a missing or mismatched build only disables the terminal. */
function loadPty(): NodePty | null {
  if (nodePty !== undefined) return nodePty
  try {
    nodePty = require('node-pty') as NodePty
  } catch (e) {
    console.warn('[terminals] node-pty unavailable:', (e as Error).message)
    nodePty = null
  }
  return nodePty
}

/** The subset of @xterm/headless a screen read needs. It is loaded on first use; without it `screen` reads fall back to the last lines. */
interface HeadlessTerminal {
  rows: number
  buffer: { active: { viewportY: number; getLine(y: number): { translateToString(trim: boolean): string } | undefined } }
  write(d: string, cb?: () => void): void
  resize(cols: number, rows: number): void
}
let headless: { Terminal: new (o: { cols: number; rows: number; allowProposedApi: boolean; scrollback: number }) => HeadlessTerminal } | null | undefined
function loadHeadless(): typeof headless {
  if (headless !== undefined) return headless
  try {
    headless = require('@xterm/headless')
  } catch (e) {
    console.warn('[terminals] @xterm/headless unavailable:', (e as Error).message)
    headless = null
  }
  return headless
}

function screenOf(cols: number, rows: number): Screen | null {
  const lib = loadHeadless()
  if (!lib) return null
  const term = new lib.Terminal({ cols, rows, allowProposedApi: true, scrollback: 0 })
  return {
    write: (d) => term.write(d),
    resize: (c, r) => term.resize(c, r),
    // An empty write is answered after everything written before it has been parsed.
    text: () => new Promise((resolve) => term.write('', () => {
      const b = term.buffer.active
      const lines: string[] = []
      for (let y = 0; y < term.rows; y++) lines.push(b.getLine(b.viewportY + y)?.translateToString(true) ?? '')
      resolve(lines.join('\n').replace(/\s+$/, ''))
    }))
  }
}

const isDirectory = (p: string): boolean => {
  try { return statSync(p).isDirectory() } catch { return false }
}

/** Session fields come from the backend only; the renderer never supplies a command, cwd or agent. */
async function fetchSession(id: string): Promise<SessionInfo | null> {
  const t = backendToken()
  const r = await fetch(`${backendUrl()}/coding-sessions/${id}`, { headers: t ? { 'X-Personal-OS-Token': t } : {} })
  if (!r.ok) return null
  return (await r.json()) as SessionInfo
}

export function registerTerminals(getMain: () => BrowserWindow | null): void {
  const hub = createTermHub({
    spawn: (argv, o): Pty => {
      const pty = loadPty()
      if (!pty) throw new Error('node-pty unavailable')
      const p = pty.spawn(argv[0], argv.slice(1), { name: 'xterm-256color', cwd: o.cwd, env: o.env, cols: o.cols, rows: o.rows })
      return {
        onData: (cb) => { p.onData(cb) },
        onExit: (cb) => { p.onExit((e) => cb(e.exitCode ?? null)) },
        write: (d) => p.write(d),
        resize: (c, r) => { try { p.resize(c, r) } catch { /* already exited */ } },
        kill: () => { try { p.kill() } catch { /* already exited */ } }
      }
    },
    fetchSession,
    isDirectory,
    send: (senderId, channel, payload) => {
      const wc = getMain()?.webContents
      if (wc && !wc.isDestroyed() && wc.id === senderId) wc.send(channel, payload.termId, channel === 'term:data' ? payload.data : payload.code)
    },
    env: process.env,
    platform: process.platform,
    isPackaged: app.isPackaged,
    fakeCmd: process.env.GRAIN_TERM_FAKE_CMD,
    screen: screenOf
  })

  // Pop-outs, the agent browser and quick-ask are windows too; only the main window may drive a terminal.
  const mainId = (e: Electron.IpcMainInvokeEvent): number => {
    const wc = getMain()?.webContents
    if (!wc || wc.isDestroyed() || e.sender.id !== wc.id) throw new Error('terminal: main window only')
    return wc.id
  }
  let hooked: number | null = null
  const hook = (id: number): void => {
    const wc = getMain()?.webContents
    if (!wc || hooked === id) return
    hooked = id
    wc.once('destroyed', () => { hub.killSender(id); hooked = null })
  }

  handle('term:available', (e) => { mainId(e); return loadPty() !== null })
  handle('term:open', (e, sessionId: unknown, mode: unknown, cols: unknown, rows: unknown) => {
    const id = mainId(e)
    hook(id)
    return hub.open(id, sessionId, mode, cols, rows)
  })
  handle('term:write', (e, termId: unknown, data: unknown) => hub.write(mainId(e), termId, data))
  handle('term:resize', (e, termId: unknown, cols: unknown, rows: unknown) => hub.resize(mainId(e), termId, cols, rows))
  handle('term:close', (e, termId: unknown) => hub.close(mainId(e), termId))
  // An agent's terminal, opened by the backend: the user takes it over here (the agent can then read it, not type into it), or lets go.
  handle('term:attach', (e, termId: unknown, cols: unknown, rows: unknown) => {
    const id = mainId(e)
    hook(id)
    return hub.attach(id, termId, cols, rows)
  })
  handle('term:detach', (e, termId: unknown) => hub.detach(mainId(e), termId))
  app.on('before-quit', () => hub.killAll())
  startTermBridge(hub, () => loadPty() !== null)
}

const BRIDGE_RETRY_MS = 2000
const sleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms))

/**
 * Long-polls the backend for terminal commands and answers each, for as long as the app runs. The backend never reaches
 * into this process: it only queues what an agent asked for, authenticated with the same token as every other request.
 */
function startTermBridge(hub: TermHub, ready: () => boolean): void {
  const call = async (path: string, body?: unknown): Promise<Response> =>
    fetch(`${backendUrl()}${path}`, {
      method: 'POST',
      headers: { 'X-Personal-OS-Token': backendToken(), 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(35_000)
    })
  void (async () => {
    for (;;) {
      if (!ready()) { await sleep(BRIDGE_RETRY_MS * 5); continue }
      try {
        const r = await call('/terminal-bridge/next')
        if (!r.ok) { await sleep(BRIDGE_RETRY_MS); continue }
        const { command } = (await r.json()) as { command: ({ id: string } & Record<string, unknown>) | null }
        if (!command) continue
        await call('/terminal-bridge/reply', { id: command.id, result: await runBridgeCommand(hub, command) })
      } catch {
        await sleep(BRIDGE_RETRY_MS)
      }
    }
  })()
}
