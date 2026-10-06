/**
 * Rotating log files for the Electron main process and the backend's raw stdout/stderr.
 * Electron-free on purpose (the folder is passed in), so it is unit-testable. Every line goes through
 * redact() first: a log is something a person pastes into a bug report.
 */
import { appendFileSync, existsSync, mkdirSync, renameSync, rmSync, statSync } from 'fs'
import { join } from 'path'
import { format } from 'util'

export const MAX_BYTES = 5 * 1024 * 1024
export const BACKUPS = 5

const PATTERNS: Array<[RegExp, string]> = [
  [/(authorization|x-personal-os-token|x-api-key|api-key|proxy-authorization|cookie|set-cookie)(['"]?\s*[:=]\s*['"]?)(?:bearer\s+|basic\s+)?[^\s'",;}]+/gi, '$1$2[redacted]'],
  [/\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}/gi, '$1 [redacted]'],
  [/(['"]?[\w-]*(?:api[_-]?key|secret|token|password|passwd|credential)[\w-]*['"]?\s*[:=]\s*)(['"]?)(?!\d{1,7}\b)[^\s'",;&}\]]+/gi, '$1$2[redacted]'],
  [/\b([a-z][a-z0-9+.-]*:\/\/)[^\s/@:]+:[^\s/@]+@/gi, '$1[redacted]@'],
  [/([?&](?:key|api_key|apikey|token|access_token|auth|code|client_secret)=)[^&\s'"]+/gi, '$1[redacted]'],
  [/\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}\b/g, '[redacted]'],
  [/\b(?:ghp|gho|ghu|ghs|ghr|github_pat)_[A-Za-z0-9_]{16,}\b/g, '[redacted]'],
  [/\bxox[baprs]-[A-Za-z0-9-]{10,}\b/g, '[redacted]'],
  [/\bAKIA[0-9A-Z]{16}\b/g, '[redacted]'],
  [/\bya29\.[A-Za-z0-9._-]{20,}\b/g, '[redacted]'],
  [/\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b/g, '[redacted]']
]

/** Exact secrets this process knows (the sidecar token). Matching the literal catches any shape. */
const secrets = new Set<string>()

export function registerSecret(value: string | undefined | null): void {
  if (value && value.trim().length >= 8) secrets.add(value.trim())
}

export function redact(text: string): string {
  let out = text
  for (const s of [...secrets].sort((a, b) => b.length - a.length)) out = out.split(s).join('[redacted]')
  for (const [re, repl] of PATTERNS) out = out.replace(re, repl)
  return out
}

/** Append-only file that rolls to name.1 ... name.N once it passes maxBytes. */
export class RotatingLog {
  private size = 0

  constructor(
    private readonly path: string,
    private readonly maxBytes = MAX_BYTES,
    private readonly backups = BACKUPS
  ) {
    try {
      this.size = existsSync(path) ? statSync(path).size : 0
    } catch {
      this.size = 0
    }
  }

  private roll(): void {
    rmSync(`${this.path}.${this.backups}`, { force: true })
    for (let i = this.backups - 1; i >= 1; i--) {
      if (existsSync(`${this.path}.${i}`)) renameSync(`${this.path}.${i}`, `${this.path}.${i + 1}`)
    }
    if (existsSync(this.path)) renameSync(this.path, `${this.path}.1`)
    this.size = 0
  }

  write(text: string): void {
    const line = redact(text).replace(/\n?$/, '\n')
    try {
      if (this.size > 0 && this.size + Buffer.byteLength(line) > this.maxBytes) this.roll()
      appendFileSync(this.path, line)
      this.size += Buffer.byteLength(line)
    } catch {
      /* a full disk or a vanished folder must not take the app down */
    }
  }
}

let dir = ''
let mainLog: RotatingLog | null = null
let processLog: RotatingLog | null = null

export function logDir(): string {
  return dir
}

/** Create the folder and open main.log and backend-process.log. Returns the folder ('' if it cannot be made). */
export function initLogs(folder: string): string {
  try {
    mkdirSync(folder, { recursive: true })
  } catch {
    return ''
  }
  dir = folder
  mainLog = new RotatingLog(join(folder, 'main.log'))
  processLog = new RotatingLog(join(folder, 'backend-process.log'))
  return folder
}

const stamp = (): string => new Date().toISOString()

export function logMain(level: string, ...args: unknown[]): void {
  mainLog?.write(`${stamp()} ${level.toUpperCase()} ${format(...args)}`)
}

/** Raw backend stdout/stderr, line-stamped; what the Python logger could not write itself (a crash, an import error). */
export function logBackendOutput(chunk: string): void {
  if (!processLog) return
  for (const line of chunk.split('\n')) if (line.trim()) processLog.write(`${stamp()} ${line}`)
}

/** Mirror console.* into main.log, still printing as before. Safe to call once. */
export function hookConsole(): void {
  for (const level of ['log', 'info', 'warn', 'error'] as const) {
    const orig = console[level].bind(console)
    console[level] = (...args: unknown[]): void => {
      orig(...args)
      logMain(level, ...args)
    }
  }
}
