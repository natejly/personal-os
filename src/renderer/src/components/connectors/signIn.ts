import { api } from '../../lib/api'
import type { McpSignIn } from '@shared/types'

/** How often a started sign-in is asked whether the browser step finished. */
const SIGN_IN_POLL_MS = 2000
/** 150 polls at 2 s: five minutes to finish the browser step. */
const SIGN_IN_POLLS = 150

/** Start a remote server's browser sign-in and resolve once it settles: done, error, or given up after 5 minutes. */
export async function signInMcp(id: string): Promise<McpSignIn> {
  const st = await api.mcp.signIn(id)
  if (st.status === 'error') throw new Error(st.error)
  if (st.auth_url) window.open(st.auth_url, '_blank')
  for (let i = 0; i < SIGN_IN_POLLS; i++) {
    await new Promise((r) => setTimeout(r, SIGN_IN_POLL_MS))
    const x = await api.mcp.signInStatus(id)
    if (x.status !== 'waiting' && x.status !== 'starting') return x
  }
  return { ...st, status: 'error', error: 'sign-in timed out' }
}
