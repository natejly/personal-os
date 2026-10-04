import { useCallback, useEffect, useState } from 'react'
import { Globe, MonitorUp, EyeOff } from 'lucide-react'
import type { AgentBrowserFrame, AgentBrowserSession } from '@shared/types'
import { splitUrl } from '../lib/deskFiles'

/** While the desk works, look again for its browser session this often. A desk at rest is only re-checked on a status change. */
const RECHECK_MS = 5000

/**
 * One agent browser session (`desk:<id>` for a desk, `conv:<id>` for a plain chat), as the agent sees it. Frames
 * are only captured while someone is subscribed, so this subscribes while it is mounted AND a session exists, and
 * lets go on unmount. "Take over" opens that same browser as a window of its own, separate from the user's
 * browser, so they can sign in or get past a challenge the agent cannot. `live`: the owner is working, so look
 * for the session again every few seconds; at rest it is re-checked only when `live` changes.
 */
export default function DeskBrowser({ session, live, emptyText = "This desk hasn't opened its browser." }: {
  session: string; live: boolean; emptyText?: string
}): JSX.Element {
  const api = window.os?.agentBrowser
  const [info, setInfo] = useState<AgentBrowserSession | null>(null)
  const [frame, setFrame] = useState<AgentBrowserFrame | null>(null)
  const [error, setError] = useState('')

  const check = useCallback(async (): Promise<void> => {
    if (!api) return
    try {
      const all = await api.list()
      setInfo(all.find((s) => s.session === session) ?? null)
    } catch {
      setInfo(null)
    }
  }, [api, session])

  useEffect(() => { void check() }, [check, live])
  useEffect(() => {
    if (!live) return
    const t = setInterval(() => { void check() }, RECHECK_MS)
    return () => clearInterval(t)
  }, [live, check])

  const has = info !== null
  useEffect(() => {
    if (!api || !has) { setFrame(null); return }
    return api.subscribe(session, setFrame)
  }, [api, has, session])

  const act = (fn: 'show' | 'hide'): void => {
    if (!api) return
    setError('')
    api[fn](session).then(() => check()).catch((e: Error) => setError(e.message))
  }

  if (!api || !info) {
    return (
      <div className="desk-pane">
        <div className="empty-state">
          <Globe size={20} />
          <p>{emptyText}</p>
          {!api && <p className="muted small">The browser view needs the desktop app.</p>}
        </div>
      </div>
    )
  }

  const url = frame?.url || info.url
  const title = frame?.title || info.title
  const { host, rest } = splitUrl(url)
  return (
    <div className="desk-pane desk-browser">
      <div className="desk-browser-bar">
        <div className="desk-browser-where">
          <div className="desk-browser-title">{title || host || 'New tab'}</div>
          {url && <div className="desk-browser-url muted small"><b>{host}</b>{rest}</div>}
        </div>
        <span className="spacer" />
        {frame && <span className="desk-browser-live muted small"><i className="live-dot" /> live {new Date(frame.at).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit', second: '2-digit' })}</span>}
        <button className="ghost-btn" onClick={() => act('show')}><MonitorUp size={13} /> Take over</button>
        <button className="ghost-btn" disabled={!info.visible} onClick={() => act('hide')}><EyeOff size={13} /> Hide window</button>
      </div>
      <p className="muted small desk-browser-help">
        Taking over opens the agent's own browser window, separate from your browser, so you can sign in or get past a challenge.
      </p>
      {error && <p className="small desk-browser-error">{error}</p>}
      <div className="desk-browser-frame">
        {frame ? <img src={frame.dataUrl} alt={`The agent's browser showing ${title || host}`} /> : <p className="muted small">Waiting for the first frame…</p>}
      </div>
    </div>
  )
}
