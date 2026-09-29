import { useEffect, useRef } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { RefreshCw, Sparkles } from 'lucide-react'
import { useStore } from '../../store'
import type { WidgetDef, WidgetProps } from '../registry'

function RecapWidget({ live }: WidgetProps): JSX.Element {
  const content = useStore((s) => s.recap?.content ?? '')
  const day = useStore((s) => s.recap?.day ?? '')
  const cached = useStore((s) => s.recap?.cached ?? false)
  const loading = useStore((s) => s.recapLoading)
  const refreshRecap = useStore((s) => s.refreshRecap)
  const asked = useRef(false)

  // The only GET this widget makes, and it never fires for a window that is off-screen, minimized or
  // zoomed out. Once per mount, because the day it generates for is the backend's, not this clock's —
  // a re-check against `day` would spin. The button covers a rollover.
  useEffect(() => {
    if (!live || asked.current || content || loading) return
    asked.current = true
    void refreshRecap()
  }, [live, content, loading, refreshRecap])

  if (!live) {
    return (
      <div className="proxy-card">
        <Sparkles size={18} />
        <strong>Daily recap</strong>
        <span>{content ? day : 'Paused while off-screen'}</span>
      </div>
    )
  }

  return (
    <div className="widget">
      <div className="widget-bar">
        <Sparkles size={12} />
        <span className="widget-meta">{day || '—'}</span>
        <span className="widget-sub">{loading ? 'writing…' : cached ? 'generated earlier today' : 'fresh'}</span>
        <span className="spacer" />
        <button className="icon-btn sm" title="Regenerate" disabled={loading} onClick={() => void refreshRecap(true)}>
          <RefreshCw size={13} className={loading ? 'spin' : ''} />
        </button>
      </div>
      <div className="widget-scroll">
        {content
          ? <div className="markdown"><ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown></div>
          : <p className="widget-sub">{loading ? 'Writing your recap…' : 'Nothing recapped yet.'}</p>}
      </div>
    </div>
  )
}

export const def: WidgetDef = {
  kind: 'recap',
  label: 'Recap',
  icon: <Sparkles size={18} />,
  defaultSize: { w: 420, h: 360 },
  minSize: { w: 280, h: 220 },
  chrome: 'full',
  Component: RecapWidget
}

export default RecapWidget
