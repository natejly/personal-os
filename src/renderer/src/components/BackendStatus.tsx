import { useState } from 'react'
import { Loader2, RefreshCw } from 'lucide-react'
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

/** Restart and log-folder buttons for the "backend not running" screen. */
export function BackendActions(): JSX.Element {
  const restart = useStore((s) => s.restartBackend)
  const [busy, setBusy] = useState(false)
  return (
    <p className="backend-actions">
      <button type="button" className="primary-btn" disabled={busy} onClick={() => {
        setBusy(true)
        void restart().finally(() => setBusy(false))
      }}>
        {busy ? <Loader2 size={14} className="spin" /> : <RefreshCw size={14} />} Restart backend
      </button>
      <button type="button" onClick={() => void window.os.openLogs()}>Open logs</button>
    </p>
  )
}
