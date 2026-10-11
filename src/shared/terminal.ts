/** In-app terminal: pure validation, argv selection, env scrubbing and the PTY hub core (no electron imports). */
import { randomBytes } from 'crypto'
import { isAttachId } from './codingAttach'

export type TermMode = 'attach' | 'shell'
export type TermOpenResult = { termId: string; replay: string; fresh: boolean } | { error: string }

export const WRITE_CAP = 64 * 1024
export const MAX_PTYS = 8
export const RING_CAP = 200 * 1024
export const FLUSH_MS = 16

/** Coding session ids are minted by the backend as 16 lowercase hex characters. */
export const isSessionId = (id: unknown): id is string => typeof id === 'string' && /^[0-9a-f]{16}$/.test(id)
export const isTermMode = (m: unknown): m is TermMode => m === 'attach' || m === 'shell'

/** Integer in 1..500; anything non-finite falls back to `dflt`. */
export function clampDim(n: unknown, dflt = 80): number {
  if (typeof n !== 'number' || !Number.isFinite(n)) return dflt
  return Math.min(500, Math.max(1, Math.round(n)))
}

export const defaultShell = (env: Record<string, string | undefined>, platform: string): string =>
  (env.SHELL && env.SHELL.startsWith('/') ? env.SHELL : '') || (platform === 'darwin' ? '/bin/zsh' : '/bin/bash')

/** Only `claude` sessions with a valid external id can be attached; a plain shell is allowed for any agent. */
export function pickArgv(a: { agent: unknown; external_id: unknown; mode: unknown; shell: string; platform?: string }): string[] | { error: string } {
  if (a.mode === 'shell') return [a.shell, '-l']
  if (a.mode === 'attach') {
    if (a.agent !== 'claude') return { error: 'Only Claude sessions can be attached' }
    if (!isAttachId(a.external_id)) return { error: 'This session has no attachable id yet' }
    return [a.shell, '-l', '-c', `claude attach ${a.external_id}`]
  }
  return { error: 'Unknown terminal mode' }
}

/**
 * The shell inherits the user's environment minus anything that carries Grain or provider credentials:
 * the backend token, the app's own and provider API keys, and any name that looks like a secret.
 */
const SECRET_NAME = /TOKEN|SECRET|API_KEY|PASSWORD|CREDENTIAL/i
const GRAIN_VARS = new Set(['PERSONAL_OS_AUTH_TOKEN', 'PERSONAL_OS_API_KEY', 'ANTHROPIC_API_KEY', 'FIREWORKS_API_KEY', 'BRAVE_API_KEY', 'FIRECRAWL_API_KEY', 'PERPLEXITY_API_KEY'])
export function scrubEnv(env: Record<string, string | undefined>): Record<string, string> {
  const out: Record<string, string> = {}
  for (const [k, v] of Object.entries(env)) {
    if (v === undefined || GRAIN_VARS.has(k) || SECRET_NAME.test(k)) continue
    out[k] = v
  }
  out.TERM = 'xterm-256color'
  out.COLORTERM = 'truecolor'
  return out
}

/** Keeps the newest `cap` characters. */
export class Ring {
  private s = ''
  constructor(private cap = RING_CAP) {}
  push(d: string): void {
    this.s += d
    if (this.s.length > this.cap) this.s = this.s.slice(this.s.length - this.cap)
  }
  get(): string { return this.s }
}

export interface Pty {
  onData(cb: (d: string) => void): void
  onExit(cb: (code: number | null) => void): void
  write(d: string): void
  resize(cols: number, rows: number): void
  kill(): void
}
export interface SessionInfo { agent?: unknown; external_id?: unknown; worktree?: unknown }
export interface HubDeps {
  spawn: (argv: string[], o: { cwd: string; env: Record<string, string>; cols: number; rows: number }) => Pty
  /** Backend lookup; null when the session does not exist. The only source of agent and cwd. */
  fetchSession: (id: string) => Promise<SessionInfo | null>
  isDirectory: (p: string) => boolean
  send: (senderId: number, channel: 'term:data' | 'term:exit', payload: Record<string, unknown>) => void
  env: Record<string, string | undefined>
  platform: string
  isPackaged: boolean
  /** Test-only command override; honoured only when not packaged. */
  fakeCmd?: string
  newId?: () => string
}

interface Entry { termId: string; key: string; senderId: number; pty: Pty; ring: Ring; pending: string; timer: ReturnType<typeof setTimeout> | null }

export function createTermHub(deps: HubDeps) {
  const byId = new Map<string, Entry>()
  const byKey = new Map<string, Entry>()
  const opening = new Map<string, Promise<TermOpenResult>>()
  const newId = deps.newId ?? ((): string => randomBytes(12).toString('hex'))

  const flush = (e: Entry): void => {
    if (e.timer) { clearTimeout(e.timer); e.timer = null }
    if (!e.pending) return
    const data = e.pending
    e.pending = ''
    deps.send(e.senderId, 'term:data', { termId: e.termId, data })
  }
  const drop = (e: Entry): void => {
    if (byId.get(e.termId) === e) byId.delete(e.termId)
    if (byKey.get(e.key) === e) byKey.delete(e.key)
  }
  const own = (senderId: number, termId: unknown): Entry | null => {
    if (typeof termId !== 'string') return null
    const e = byId.get(termId)
    return e && e.senderId === senderId ? e : null
  }

  async function doOpen(senderId: number, sessionId: string, mode: TermMode, cols: number, rows: number, key: string): Promise<TermOpenResult> {
    const reuse = (): TermOpenResult | null => {
      const e = byKey.get(key)
      if (!e) return null
      if (e.senderId !== senderId) return { error: 'Terminal is open in another window' }
      // The ring already holds anything not yet flushed, so the replay is complete on its own.
      e.pending = ''
      if (e.timer) { clearTimeout(e.timer); e.timer = null }
      e.pty.resize(cols, rows)
      return { termId: e.termId, replay: e.ring.get(), fresh: false }
    }
    const first = reuse()
    if (first) return first
    let s: SessionInfo | null
    try { s = await deps.fetchSession(sessionId) } catch { return { error: 'Could not load the session' } }
    if (!s) return { error: 'Session not found' }
    const raced = reuse()
    if (raced) return raced
    if (byId.size >= MAX_PTYS) return { error: `At most ${MAX_PTYS} terminals can be open` }
    const cwd = typeof s.worktree === 'string' ? s.worktree : ''
    if (!cwd.startsWith('/') || !deps.isDirectory(cwd)) return { error: 'The session folder does not exist' }
    const shell = defaultShell(deps.env, deps.platform)
    let argv = pickArgv({ agent: s.agent, external_id: s.external_id, mode, shell, platform: deps.platform })
    if (!Array.isArray(argv)) return argv
    const fake = !deps.isPackaged && deps.fakeCmd ? deps.fakeCmd.trim().split(/\s+/).filter(Boolean) : []
    if (fake.length) argv = fake
    let pty: Pty
    try { pty = deps.spawn(argv, { cwd, env: scrubEnv(deps.env), cols, rows }) } catch { return { error: 'Could not start the terminal' } }
    const e: Entry = { termId: newId(), key, senderId, pty, ring: new Ring(), pending: '', timer: null }
    byId.set(e.termId, e)
    byKey.set(key, e)
    pty.onData((d) => {
      if (byId.get(e.termId) !== e) return
      e.ring.push(d)
      e.pending += d
      if (!e.timer) e.timer = setTimeout(() => flush(e), FLUSH_MS)
    })
    pty.onExit((code) => {
      flush(e)
      drop(e)
      deps.send(e.senderId, 'term:exit', { termId: e.termId, code })
    })
    return { termId: e.termId, replay: '', fresh: true }
  }

  return {
    open(senderId: number, sessionId: unknown, mode: unknown, cols: unknown, rows: unknown): Promise<TermOpenResult> {
      if (!isSessionId(sessionId)) return Promise.resolve({ error: 'Invalid session id' })
      if (!isTermMode(mode)) return Promise.resolve({ error: 'Unknown terminal mode' })
      const key = `${sessionId}:${mode}`
      const flight = `${senderId}:${key}`
      // Concurrent opens of the same terminal share one spawn.
      const inflight = opening.get(flight)
      if (inflight) return inflight
      const p = doOpen(senderId, sessionId, mode, clampDim(cols, 80), clampDim(rows, 24), key).finally(() => opening.delete(flight))
      opening.set(flight, p)
      return p
    },
    write(senderId: number, termId: unknown, data: unknown): boolean {
      const e = own(senderId, termId)
      if (!e || typeof data !== 'string' || data.length > WRITE_CAP) return false
      e.pty.write(data)
      return true
    },
    resize(senderId: number, termId: unknown, cols: unknown, rows: unknown): boolean {
      const e = own(senderId, termId)
      if (!e) return false
      e.pty.resize(clampDim(cols, 80), clampDim(rows, 24))
      return true
    },
    close(senderId: number, termId: unknown): boolean {
      const e = own(senderId, termId)
      if (!e) return false
      drop(e)
      e.pty.kill()
      return true
    },
    killSender(senderId: number): void {
      for (const e of [...byId.values()]) if (e.senderId === senderId) { drop(e); e.pty.kill() }
    },
    killAll(): void {
      for (const e of [...byId.values()]) { drop(e); e.pty.kill() }
    },
    size: (): number => byId.size
  }
}
export type TermHub = ReturnType<typeof createTermHub>
