/**
 * The agent browser's saved sign-ins: the cookies and site storage in the shared `persist:agent` session, which
 * keeps whatever the user signed in to during a handoff. Pure over a session-like object so it tests without
 * Electron; agentBrowser.ts wires it to IPC.
 */
import type { AgentBrowserSignIn } from '../shared/types'

type Cookie = Pick<Electron.Cookie, 'domain' | 'path' | 'name' | 'secure'>
export interface CookieSession {
  cookies: { get: (filter: Record<string, never>) => Promise<Cookie[]>; remove: (url: string, name: string) => Promise<void> }
  clearStorageData: (opts?: { origin?: string }) => Promise<void>
}

const site = (c: Cookie): string => (c.domain ?? '').replace(/^\./, '').toLowerCase()

/** One row per site (leading dot dropped, so `.x.com` and `x.com` are one row), most cookies first. */
export async function listSignIns(ses: CookieSession): Promise<AgentBrowserSignIn[]> {
  const counts = new Map<string, number>()
  for (const c of await ses.cookies.get({})) {
    const d = site(c)
    if (d) counts.set(d, (counts.get(d) ?? 0) + 1)
  }
  return [...counts].map(([domain, count]) => ({ domain, count })).sort((a, b) => b.count - a.count || a.domain.localeCompare(b.domain))
}

/**
 * Forgets one site. There is no per-domain cookie clear, so each of its cookies is removed by URL and name, then the
 * site's own storage (local storage, IndexedDB), where sign-ins also live. Only that exact site, never its
 * subdomains, which are rows of their own.
 */
export async function clearSignIn(ses: CookieSession, domain: string): Promise<number> {
  const want = String(domain ?? '').replace(/^\./, '').toLowerCase()
  if (!want) return 0
  const mine = (await ses.cookies.get({})).filter((c) => site(c) === want)
  for (const c of mine) await ses.cookies.remove(`http${c.secure ? 's' : ''}://${want}${c.path || '/'}`, c.name)
  for (const scheme of ['https', 'http']) await ses.clearStorageData({ origin: `${scheme}://${want}` }).catch(() => undefined)
  return mine.length
}
