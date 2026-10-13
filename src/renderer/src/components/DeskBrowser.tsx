import { useCallback, useEffect, useReducer, useState } from 'react'
import { Globe, MonitorUp, EyeOff } from '../icons'
import type { AgentBrowserSession } from '@shared/types'
import { initialLive, markCaption, markRect, reduceLive } from '../lib/browserLive'
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
export default function DeskBrowser(props: { session: string; live: boolean; emptyText?: string }): JSX.Element {
  return <DeskBrowserView key={props.session} {...props} /> // a different session starts from a clean view
}

function DeskBrowserView({ session, live, emptyText = "This desk hasn't opened its browser." }: {
  session: string; live: boolean; emptyText?: string
}): JSX.Element {
  const api = window.os?.agentBrowser
  const [info, setInfo] = useState<AgentBrowserSession | null>(null)
  const [view, dispatch] = useReducer(reduceLive, initialLive)
  const [error, setError] = useState('')
  const ended = view.status === 'ended'

  const check = useCallback(async (): Promise<void> => {
    if (!api) return
    try {
      const found = (await api.list()).find((s) => s.session === session) ?? null
      setInfo(found)
      dispatch({ type: found ? 'present' : 'missing' })
    } catch {
      setInfo(null)
      dispatch({ type: 'missing' })
    }
  }, [api, session])

  useEffect(() => { void check() }, [check, live])
  useEffect(() => {
    if (!live || ended) return
    const t = setInterval(() => { void check() }, RECHECK_MS)
    return () => clearInterval(t)
  }, [live, ended, check])

  const has = info !== null && !ended // once ended there is nothing more to hear
  useEffect(() => {
    if (!api || !has) return
    return api.subscribe(session, (frame) => dispatch({ type: 'frame', frame }), (state) => dispatch({ type: 'state', state }))
  }, [api, has, session])

  const act = (fn: 'show' | 'hide'): void => {
    if (!api) return
    setError('')
    api[fn](session).then(() => check()).catch((e: Error) => setError(e.message))
  }

  const frame = view.frame
  if (!api || (!info && !frame)) {
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

  const url = frame?.url || info?.url || ''
  const title = frame?.title || info?.title || ''
  const { host, rest } = splitUrl(url)
  const rect = markRect(view.mark)
  const caption = markCaption(view.mark)
  return (
    <div className="desk-pane desk-browser">
      <div className="desk-browser-bar">
        <div className="desk-browser-where">
          <div className="desk-browser-title">{title || host || 'New tab'}</div>
          {url && <div className="desk-browser-url muted small"><b>{host}</b>{rest}</div>}
        </div>
        <span className="spacer" />
        <span className={`desk-browser-live muted small ${view.status}`} role="status" aria-live="polite">
          {view.status === 'live' && <><i className="live-dot" /> Live</>}
          {view.status === 'idle' && "Idle — the assistant isn't using the browser"}
          {view.status === 'ended' && `Browser session ended${view.reason ? ` (${view.reason})` : ''}`}
          {view.status === 'error' && `Page failed to load: ${view.reason}`}
        </span>
        <button className="ghost-btn" disabled={!info || ended} onClick={() => act('show')}><MonitorUp size={13} /> Take over</button>
        <button className="ghost-btn" disabled={!info?.visible || ended} onClick={() => act('hide')}><EyeOff size={13} /> Hand back</button>
      </div>
      <p className="muted small desk-browser-help">
        Taking over opens the agent's own browser window, separate from your browser, so you can sign in or get past a challenge.
      </p>
      {error && <p className="small desk-browser-error">{error}</p>}
      <div className="desk-browser-frame">
        {frame ? (
          <div className={`desk-browser-stage${ended ? ' ended' : ''}`}>
            <img src={frame.dataUrl} alt={`The agent's browser showing ${title || host}`} />
            {rect && <div className="desk-browser-mark" style={rect} aria-hidden="true" />}
          </div>
        ) : <p className="muted small">Waiting for the first frame…</p>}
      </div>
      {caption && <p className="desk-browser-caption small" aria-live="polite">{caption}</p>}
    </div>
  )
}
