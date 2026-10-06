import { Loader2 } from 'lucide-react'
import { useStore } from '../store'

/** Shown while the main process is restarting a sidecar that died; the UI stays up underneath it. */
export function BackendBanner(): JSX.Element | null {
  const state = useStore((s) => s.backendState)
  if (state !== 'restarting' && state !== 'starting') return null
  return (
    <div className="backend-banner" role="status">
      <Loader2 size={14} className="spin" />
      <span>The backend stopped and is restarting. Anything that was mid-reply was interrupted; this reconnects on its own.</span>
    </div>
  )
}
