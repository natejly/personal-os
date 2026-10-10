import { useEffect, useState } from 'react'
import { RefreshCw, X } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useStore } from '../store'
import { homeModuleOn } from '../moduleToggles'
import { hasModelKey } from '../lib/modelLabel'
import { SAFE_MD } from './Message'

/** The daily recap, small and quiet under a new chat's greeting: clipped to a few lines until opened. */
export default function DailyRecap(): JSX.Element | null {
  const recap = useStore((s) => s.recap)
  const loading = useStore((s) => s.recapLoading)
  const settings = useStore((s) => s.settings)
  const refreshRecap = useStore((s) => s.refreshRecap)
  const saveSettings = useStore((s) => s.saveSettings)
  const [open, setOpen] = useState(false)
  const on = homeModuleOn(settings, 'recap') && hasModelKey(settings)
  // The boot fetch only runs with the switch on; turning it on later (or a failed boot read) must still load it, once per mount.
  useEffect(() => { if (on && !recap && !useStore.getState().recapLoading) void refreshRecap() }, [on]) // eslint-disable-line react-hooks/exhaustive-deps
  if (!on || (!recap?.content && !loading)) return null

  const hide = (): void => {
    void saveSettings({ homeWidgets: { ...(settings.homeWidgets ?? {}), recap: false } })
    useStore.getState().toast('Daily recap hidden. Settings → Appearance brings it back.', 'info')
  }
  return (
    <section className="recap recap-inline">
      <header>Daily recap
        <span className="spacer" />
        <button className="icon-btn sm" title="Regenerate" aria-label="Regenerate daily recap" onClick={() => void refreshRecap(true)}><RefreshCw size={12} className={loading ? 'spin' : ''} /></button>
        <button className="icon-btn sm" title="Hide the daily recap" aria-label="Hide the daily recap" onClick={hide}><X size={12} /></button>
      </header>
      {loading && !recap?.content ? <p className="muted">Writing your recap…</p> : (
        <>
          <div className={`markdown${open ? '' : ' clamped'}`}><ReactMarkdown remarkPlugins={[remarkGfm]} components={SAFE_MD}>{recap?.content ?? ''}</ReactMarkdown></div>
          <button className="link small" onClick={() => setOpen((o) => !o)}>{open ? 'Show less' : 'Show more'}</button>
        </>
      )}
    </section>
  )
}
