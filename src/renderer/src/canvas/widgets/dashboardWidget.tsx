import { useEffect, useState } from 'react'
import { LayoutDashboard, RefreshCw } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { Widget } from '@shared/types'
import { api, getBase } from '../../lib/api'
import type { WidgetDef, WidgetProps } from '../registry'
import { SAFE_MD } from '../../components/Message'
import DeclarativeWidget from '../../components/DeclarativeWidget'
import { isDeclarative } from '../../lib/boundWidget'

const time = (t: number): string => new Date(t * 1000).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })

export default function DashboardWidget({ window: win, live, onTitle }: WidgetProps): JSX.Element {
  const dashboardId = typeof win.config.dashboard_id === 'string' ? win.config.dashboard_id : ''
  const refId = win.ref_id
  const [widget, setWidget] = useState<Widget | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  // Bumped by Retry to re-run the load effect.
  const [attempt, setAttempt] = useState(0)

  // Only while the window is live, so an off-screen window issues no request at all. A declarative widget
  // then asks for its data, which is the cache inside its refresh_minutes and a re-bind (never the model) after.
  useEffect(() => {
    if (!live || !dashboardId || !refId) return
    let ok = true
    void api.widgets
      .get(refId)
      .then((w) => (isDeclarative(w.kind) ? api.widgets.data(refId).catch(() => w) : w))
      .then((w) => { if (ok) { setWidget(w); setError(null) } })
      .catch((e: Error) => { if (ok) setError(/404|Not Found/i.test(e.message) ? 'That widget was deleted from its dashboard.' : e.message) })
    return () => { ok = false }
  }, [live, dashboardId, refId, attempt])

  useEffect(() => {
    if (widget && !win.title) onTitle(widget.title)
  }, [widget, win.title, onTitle])

  const refresh = async (): Promise<void> => {
    if (!refId) return
    setBusy(true)
    try {
      setWidget(await api.widgets.refresh(refId))
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  if (!dashboardId || !refId) return <div className="widget"><div className="widget-error">No dashboard widget bound to this window.</div></div>
  // The iframe is the single most expensive thing on the plane; `live` is what unmounts it.
  if (!live) return <div className="widget"><div className="widget-empty">{widget?.title || 'Widget'} · paused</div></div>
  if (!widget && error) {
    return (
      <div className="widget">
        <div className="widget-error">{error}</div>
        <button className="ghost-btn" onClick={() => { setError(null); setAttempt((n) => n + 1) }}><RefreshCw size={12} /> Retry</button>
      </div>
    )
  }
  if (!widget) return <div className="widget"><div className="widget-empty">Loading…</div></div>

  return (
    <div className="widget">
      {/* A failed refresh must not take away the widget that is already loaded. */}
      {error && <div className="widget-error" role="alert">{error}</div>}
      <div className="widget-bar">
        <span className="widget-title">{widget.title}</span>
        <span className="spacer" />
        {widget.refreshed_at !== null && <span>{time(widget.refreshed_at)}</span>}
        <button className="widget-chip" title="Refresh" onClick={() => void refresh()}><RefreshCw size={11} className={busy ? 'spin' : ''} /></button>
      </div>
      {isDeclarative(widget.kind) ? (
        <div className="widget-scroll"><DeclarativeWidget widget={widget} height={Math.max(140, win.h - 90)} /></div>
      ) : widget.kind === 'html' ? (
        widget.code ? (
          // Opaque: a transparent iframe over the vibrancy window reads as a hole to the desktop while
          // the document (re)loads. #232220 matches the dark surface generated widgets style themselves for.
          <iframe key={widget.refreshed_at ?? 0} title={widget.title} sandbox="allow-scripts" src={`${getBase()}/widgets/${widget.id}/render`}
            style={{ flex: 1, width: '100%', border: 0, background: '#232220', display: 'block' }} />
        ) : (
          <div className="widget-empty">{busy ? 'Generating…' : widget.output || 'No code generated yet.'}</div>
        )
      ) : (
        <div className="widget-scroll markdown">
          <ReactMarkdown remarkPlugins={[remarkGfm]} components={SAFE_MD}>{widget.output || (busy ? 'Summarizing…' : 'Nothing yet.')}</ReactMarkdown>
        </div>
      )}
    </div>
  )
}

export const def: WidgetDef = {
  kind: 'dashboard-widget',
  label: 'Widget',
  icon: <LayoutDashboard size={15} />,
  defaultSize: { w: 420, h: 340 },
  minSize: { w: 280, h: 200 },
  chrome: 'full',
  heavy: true,
  needsRef: true,
  Component: DashboardWidget
}
