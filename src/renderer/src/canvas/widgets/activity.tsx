import { useEffect } from 'react'
import { Eye, EyeOff, MonitorDot, Pause, Play, RefreshCw } from 'lucide-react'
import { useStore } from '../../store'
import type { WidgetDef, WidgetProps } from '../registry'

/** Canvas face of the activity monitor: the live line, the last few periods, and the switch.
 *  Deliberately small - the full panel (signals, privacy, raw log) stays in the Activity view. */

const clock = (ts: number): string =>
  new Date(ts * 1000).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })

function ActivityWidget({ live }: WidgetProps): JSX.Element {
  const st = useStore((s) => s.activity)
  const summaries = useStore((s) => s.activitySummaries)
  const busy = useStore((s) => s.activityBusy)
  const { loadActivity, refreshActivity, startActivity, stopActivity, pauseActivity, resumeActivity, rollupActivity, setView } = useStore()

  useEffect(() => {
    if (live) void loadActivity()
  }, [live, loadActivity])
  // Only polls while the window is actually visible, like every other widget.
  useEffect(() => {
    if (!live) return
    const t = setInterval(() => void refreshActivity(), 5000)
    return () => clearInterval(t)
  }, [live, refreshActivity])

  const recording = Boolean(st?.running && !st.paused)

  if (!live) {
    return (
      <div className="proxy-card">
        <MonitorDot size={18} />
        <strong>Activity</strong>
        <span>{recording ? 'Recording' : st?.paused ? 'Paused' : 'Off'}</span>
      </div>
    )
  }

  return (
    <div className="widget">
      <div className="widget-bar">
        <span className={`act-state ${recording ? 'live' : st?.paused ? 'paused' : 'off'}`}>
          <span className="act-dot" />
          {recording ? 'Recording' : st?.paused ? 'Paused' : 'Off'}
        </span>
        <span className="spacer" />
        {st?.running && (
          st.paused
            ? <button className="icon-btn sm" title="Resume" onClick={() => void resumeActivity()}><Play size={13} /></button>
            : <button className="icon-btn sm" title="Pause 30 minutes" onClick={() => void pauseActivity(30)}><Pause size={13} /></button>
        )}
        <button className="icon-btn sm" title="Summarize now" disabled={busy} onClick={() => void rollupActivity()}>
          <RefreshCw size={13} className={busy ? 'spin' : ''} />
        </button>
        {st?.running
          ? <button className="icon-btn sm" title="Turn the monitor off" onClick={() => void stopActivity()}><EyeOff size={13} /></button>
          : <button className="icon-btn sm" title="Turn the monitor on" disabled={!st?.platform_supported} onClick={() => void startActivity()}><Eye size={13} /></button>}
      </div>
      <div className="widget-scroll">
        <p className="act-now">{st?.now ?? 'Loading…'}</p>
        {summaries.length === 0
          ? <p className="widget-sub">No summaries yet. <button className="link" onClick={() => setView('activity')}>Open the Activity panel</button> to choose what gets recorded.</p>
          : (
            <div className="act-timeline">
              {summaries.slice(0, 8).map((s) => (
                <article key={s.id} className="act-period">
                  <header>
                    <time>{clock(s.period_start)}–{clock(s.period_end)}</time>
                    <b>{s.headline || 'Activity'}</b>
                  </header>
                  <p>{s.body.split('\n\n')[0]}</p>
                </article>
              ))}
            </div>
          )}
      </div>
    </div>
  )
}

export const def: WidgetDef = {
  kind: 'activity',
  label: 'Activity',
  icon: <MonitorDot size={18} />,
  defaultSize: { w: 420, h: 380 },
  minSize: { w: 280, h: 220 },
  chrome: 'full',
  Component: ActivityWidget
}
