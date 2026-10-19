/** In-app terminal: pure validation, argv selection, env scrubbing and the PTY hub core (no electron imports). */
import { randomBytes } from 'crypto'
import { isAttachId, isOpencodeId } from './codingAttach'

export type TermMode = 'attach' | 'shell' | 'opencode'
export type TermOpenResult = { termId: string; replay: string; fresh: boolean } | { error: string }

export const WRITE_CAP = 64 * 1024
export const MAX_PTYS = 8
export const RING_CAP = 200 * 1024
export const FLUSH_MS = 16
/** Agent terminals (the ones the backend opens for an agent) among MAX_PTYS: two are always left for the user's own. */
export const AGENT_MAX = 6
export const AGENT_ID = /^tm_[0-9a-f]{16}$/

/** Coding session ids are minted by the backend as 16 lowercase hex characters. */
export const isSessionId = (id: unknown): id is string => typeof id === 'string' && /^[0-9a-f]{16}$/.test(id)
export const isTermMode = (m: unknown): m is TermMode => m === 'attach' || m === 'shell' || m === 'opencode'

/** Integer in 1..500; anything non-finite falls back to `dflt`. */
export function clampDim(n: unknown, dflt = 80): number {
  if (typeof n !== 'number' || !Number.isFinite(n)) return dflt
  return Math.min(500, Math.max(1, Math.round(n)))
}

export const defaultShell = (env: Record<string, string | undefined>, platform: string): string =>
  (env.SHELL && env.SHELL.startsWith('/') ? env.SHELL : '') || (platform === 'darwin' ? '/bin/zsh' : '/bin/bash')

/** Only `claude` sessions with a valid external id can be attached; a plain shell is allowed for any agent. */
export function pickArgv(a: { agent: unknown; external_id?: unknown; session_id?: unknown; status?: unknown; worktree?: unknown; mode: unknown; shell: string; platform?: string }): string[] | { error: string } {
  if (a.mode === 'shell') return [a.shell, '-l']
  if (a.mode === 'attach') {
    if (a.agent !== 'claude') return { error: 'Only Claude sessions can be attached' }
    if (!isAttachId(a.external_id)) return { error: 'This session has no attachable id yet' }
    return [a.shell, '-l', '-c', `claude attach ${a.external_id}`]
  }
  if (a.mode === 'opencode') {
    if (a.agent !== 'opencode') return { error: 'Only OpenCode sessions can be opened in OpenCode' }
    if (!isOpencodeId(a.session_id)) return { error: 'This session has no OpenCode id yet' }
    if (a.status === 'starting' || a.status === 'working' || a.status === 'needs_you') return { error: 'The session is still running' }
    if (typeof a.worktree !== 'string' || !a.worktree.startsWith('/')) return { error: 'The session folder does not exist' }
    // Fixed argv: the login shell only finds `opencode` on PATH; id and folder are positional parameters, never interpolated.
    return [a.shell, '-l', '-c', 'exec opencode -s "$1" "$2"', 'opencode', a.session_id, a.worktree]
  }
  return { error: 'Unknown terminal mode' }
}

/**
 * The shell inherits the user's environment minus anything that carries Grain or provider credentials:
 * the backend token, the app's own and provider API keys, and any name that looks like a secret.
 */
const SECRET_NAME = /TOKEN|SECRET|API_KEY|PASSWORD|CREDENTIAL/i
const GRAIN_VARS = new Set(['PERSONAL_OS_AUTH_TOKEN', 'ANTHROPIC_API_KEY', 'FIREWORKS_API_KEY', 'BRAVE_API_KEY', 'FIRECRAWL_API_KEY', 'PERPLEXITY_API_KEY'])
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

/**
 * Keeps the newest `cap` characters. `total` counts every character ever pushed, so a position in the output is an integer
 * (a cursor) and a reader can tell that what it wanted to read has been dropped (`base` is the offset of the oldest kept one).
 */
export class Ring {
  private s = ''
  total = 0
  constructor(private cap = RING_CAP) {}
  push(d: string): void {
    this.total += d.length
    this.s += d
    if (this.s.length > this.cap) this.s = this.s.slice(this.s.length - this.cap)
  }
  get(): string { return this.s }
  get base(): number { return this.total - this.s.length }
  /** Everything from absolute offset `cursor` on, and whether the start of it was already dropped. */
  since(cursor: number): { text: string; lost: boolean } {
    const from = Math.min(Math.max(cursor, this.base), this.total)
    return { text: this.s.slice(from - this.base), lost: cursor < this.base }
  }
}

/** What is on a terminal's screen as plain text. A real emulator when one is available, else the ring's last lines. */
export interface Screen {
  write(d: string): void
  resize(cols: number, rows: number): void
  text(): Promise<string>
}
// eslint-disable-next-line no-control-regex
const ANSI = /\u001b\[[0-?]*[ -/]*[@-~]|\u001b\][^\u0007\u001b]*(?:\u0007|\u001b\\)|\u001b[@-Z\\-_]/g
/** The last `rows` non-empty lines of raw output with control sequences removed: the screen when no emulator is there. */
export const lastLines = (raw: string, rows: number): string => raw.replace(ANSI, '').split(/\r?\n/).map((l) => l.replace(/^.*\r/, '')).filter((l) => l.trim()).slice(-rows).join('\n')

export interface Pty {
  onData(cb: (d: string) => void): void
  onExit(cb: (code: number | null) => void): void
  write(d: string): void
  resize(cols: number, rows: number): void
  kill(): void
}
export interface SessionInfo { agent?: unknown; external_id?: unknown; session_id?: unknown; status?: unknown; worktree?: unknown }
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
  /** A screen emulator for an agent terminal, or null when none is installed (`screen` reads then fall back to the last lines). */
  screen?: (cols: number, rows: number) => Screen | null
}

/** What an agent terminal has beyond a user's: no window owns it until the user attaches one, and the agent can see what it did. */
interface Control { screen: Screen | null; rows: number; exited: boolean; exitCode: number | null; attached: boolean; dirty: boolean }
interface Entry { termId: string; key: string; senderId: number; pty: Pty; ring: Ring; pending: string; timer: ReturnType<typeof setTimeout> | null; control?: Control }

export interface ControlOpen { terminal_id: unknown; argv: unknown; env: unknown; cwd: unknown; cols: unknown; rows: unknown }

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
  /** An exited agent terminal stays readable until it is closed, but holds no process: it does not count toward the limits. */
  const running = (): Entry[] => [...byId.values()].filter((x) => !x.control?.exited)
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
    if (running().length >= MAX_PTYS) return { error: `At most ${MAX_PTYS} terminals can be open` }
    const cwd = typeof s.worktree === 'string' ? s.worktree : ''
    if (!cwd.startsWith('/') || !deps.isDirectory(cwd)) return { error: 'The session folder does not exist' }
    const shell = defaultShell(deps.env, deps.platform)
    let argv = pickArgv({ agent: s.agent, external_id: s.external_id, session_id: s.session_id, status: s.status, worktree: s.worktree, mode, shell, platform: deps.platform })
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

  const detach = (e: Entry): void => {
    if (!e.control) return
    flush(e)
    e.senderId = 0
    e.control.attached = false
  }
  const agentTerminal = (terminalId: unknown): Entry | null => {
    const e = typeof terminalId === 'string' ? byId.get(terminalId) : undefined
    return e?.control ? e : null
  }
  const state = (e: Entry, c: Control): Record<string, unknown> => ({ attached: c.attached, dirty: c.dirty, exited: c.exited, exit_code: c.exitCode, cursor: e.ring.total })

  /** Starts an agent's terminal from what the backend sent. Nothing here comes from the renderer. */
  function controlOpen(a: ControlOpen): { ok: true } | { error: string } {
    const { terminal_id: id, argv, env, cwd } = a
    if (typeof id !== 'string' || !AGENT_ID.test(id) || byId.has(id)) return { error: 'Invalid terminal id' }
    if (!Array.isArray(argv) || !argv.length || !argv.every((x) => typeof x === 'string')) return { error: 'Invalid argv' }
    if (!(argv[0] as string).startsWith('/')) return { error: 'The program must be an absolute path' }
    if (typeof cwd !== 'string' || !cwd.startsWith('/') || !deps.isDirectory(cwd)) return { error: 'The folder does not exist' }
    if (typeof env !== 'object' || env === null) return { error: 'Invalid environment' }
    const live = running()
    if (live.length >= MAX_PTYS || live.filter((x) => x.control).length >= AGENT_MAX) return { error: 'Too many terminals are open' }
    const cols = clampDim(a.cols, 120), rows = clampDim(a.rows, 30)
    let pty: Pty
    try { pty = deps.spawn(argv as string[], { cwd, env: scrubEnv(env as Record<string, string | undefined>), cols, rows }) } catch { return { error: 'Could not start the terminal' } }
    const c: Control = { screen: deps.screen?.(cols, rows) ?? null, rows, exited: false, exitCode: null, attached: false, dirty: false }
    const e: Entry = { termId: id, key: `agent:${id}`, senderId: 0, pty, ring: new Ring(), pending: '', timer: null, control: c }
    byId.set(id, e)
    pty.onData((d) => {
      e.ring.push(d)
      c.screen?.write(d)
      if (!e.senderId) return
      e.pending += d
      if (!e.timer) e.timer = setTimeout(() => flush(e), FLUSH_MS)
    })
    pty.onExit((code) => {
      flush(e)
      c.exited = true
      c.exitCode = code
      if (e.senderId) deps.send(e.senderId, 'term:exit', { termId: id, code })
    })
    return { ok: true }
  }

  function controlWrite(terminalId: unknown, data: unknown, clearDirty: boolean): Record<string, unknown> {
    const e = agentTerminal(terminalId)
    if (!e?.control) return { error: 'No such terminal' }
    if (typeof data !== 'string' || data.length > WRITE_CAP) return { error: 'Invalid data' }
    if (e.control.exited) return { error: 'The terminal has exited' }
    if (e.control.attached) return { blocked: 'attached' }
    if (e.control.dirty && !clearDirty) return { blocked: 'dirty' }
    if (clearDirty) e.control.dirty = false
    e.pty.write(data)
    return { ok: true, ...state(e, e.control) }
  }

  async function controlRead(terminalId: unknown, cursor: unknown, maxBytes: unknown, screen: unknown): Promise<Record<string, unknown>> {
    const e = agentTerminal(terminalId)
    if (!e?.control) return { error: 'No such terminal' }
    const { text, lost } = e.ring.since(typeof cursor === 'number' && Number.isFinite(cursor) ? cursor : 0)
    const cap = typeof maxBytes === 'number' && maxBytes > 0 ? maxBytes : RING_CAP
    const out: Record<string, unknown> = { text: text.slice(-cap), from_cursor_lost: lost, ...state(e, e.control) }
    if (screen === true) out.screen = e.control.screen ? await e.control.screen.text() : lastLines(e.ring.get(), e.control.rows)
    return out
  }

  function controlStatus(terminalId: unknown): Record<string, unknown> {
    const e = agentTerminal(terminalId)
    return e?.control ? { alive: !e.control.exited, ...state(e, e.control) } : { error: 'No such terminal' }
  }

  function controlClose(terminalId: unknown): Record<string, unknown> {
    const e = agentTerminal(terminalId)
    if (!e?.control) return { error: 'No such terminal' }
    const code = e.control.exitCode
    drop(e)
    e.pty.kill()
    return { closed: true, exit_code: code }
  }

  /** The user opens an agent's terminal in a window: it is theirs from now on (the agent can read it, not type into it). */
  function attach(senderId: number, terminalId: unknown, cols: unknown, rows: unknown): TermOpenResult {
    const e = agentTerminal(terminalId)
    if (!e?.control) return { error: 'No such terminal' }
    if (e.control.attached && e.senderId !== senderId) return { error: 'Terminal is open in another window' }
    e.pending = ''
    if (e.timer) { clearTimeout(e.timer); e.timer = null }
    e.senderId = senderId
    e.control.attached = true
    if (!e.control.exited) e.pty.resize(clampDim(cols, 80), clampDim(rows, 24))
    return { termId: e.termId, replay: e.ring.get(), fresh: false }
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
      if (e.control) e.control.dirty = true  // the user typed: the shell's line editor may hold text the agent never saw
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
      for (const e of [...byId.values()]) {
        if (e.senderId !== senderId) continue
        if (e.control) detach(e)  // an agent's terminal outlives the window that had it open
        else { drop(e); e.pty.kill() }
      }
    },
    killAll(): void {
      for (const e of [...byId.values()]) { drop(e); e.pty.kill() }
    },
    size: (): number => byId.size,
    controlOpen, controlWrite, controlRead, controlStatus, controlClose, attach,
    detach(senderId: number, terminalId: unknown): boolean {
      const e = own(senderId, terminalId)
      if (!e?.control) return false
      detach(e)
      return true
    }
  }
}

export type TermHub = ReturnType<typeof createTermHub>

/** One command from the backend's terminal bridge, run on the hub; its answer goes back to the backend as it is. */
export async function runBridgeCommand(hub: TermHub, cmd: unknown): Promise<Record<string, unknown>> {
  const c = (typeof cmd === 'object' && cmd !== null ? cmd : {}) as Record<string, unknown>
  switch (c.type) {
    case 'open': return hub.controlOpen(c as unknown as ControlOpen)
    case 'write': return hub.controlWrite(c.terminal_id, c.data, c.clear_dirty === true)
    case 'read': return hub.controlRead(c.terminal_id, c.cursor, c.max_bytes, c.screen)
    case 'status': return hub.controlStatus(c.terminal_id)
    case 'close': return hub.controlClose(c.terminal_id)
    default: return { error: 'Unknown terminal command' }
  }
}
