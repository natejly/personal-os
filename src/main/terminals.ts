import { app, BrowserWindow } from 'electron'
import { statSync } from 'fs'
import { createTermHub, type Pty, type SessionInfo } from '../shared/terminal'
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
    fakeCmd: process.env.GRAIN_TERM_FAKE_CMD
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
  app.on('before-quit', () => hub.killAll())
}
