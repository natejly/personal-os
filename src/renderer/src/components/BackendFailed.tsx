import { useState } from 'react'
import { AlertTriangle, Copy, FolderOpen, RefreshCw } from 'lucide-react'
import { useStore } from '../store'

/** Shown when the Python backend could not start. Plain language first; the developer detail sits behind a disclosure. */
export default function BackendFailed({ message }: { message: string }): JSX.Element {
  const [retrying, setRetrying] = useState(false)
  const toast = useStore((s) => s.toast)

  // Asks the main process to respawn the backend, then re-runs init against it.
  const retry = async (): Promise<void> => {
    setRetrying(true)
    try {
      await useStore.getState().restartBackend()
    } catch {
      // A failed restart still falls through to init, which reports the current error.
    }
    useStore.setState({ ready: false, backendError: null })
    await useStore.getState().init()
    setRetrying(false)
  }

  const details = [
    `Grain backend error: ${message}`,
    'Setup from source: cd backend && uv venv && uv pip install -e .',
    'Or run the backend yourself and set PERSONAL_OS_BACKEND_URL.'
  ].join('\n')
  const copy = (): void => {
    void navigator.clipboard.writeText(details).then(() => toast('Details copied'), () => toast('Could not copy', 'error'))
  }

  return (
    <div className="app loading">
      <div className="backend-error drag" role="alert">
        <AlertTriangle size={28} />
        <h2>Grain could not start</h2>
        <p>Something went wrong while starting Grain&apos;s background service. Your data is safe. Trying again often fixes it; if it does not, quit and reopen Grain.</p>
        <div className="backend-error-actions">
          <button className="primary-btn" onClick={() => void retry()} disabled={retrying}><RefreshCw size={13} className={retrying ? 'spin' : undefined} /> {retrying ? 'Trying…' : 'Try again'}</button>
          <button className="ghost-btn" onClick={copy}><Copy size={13} /> Copy details</button>
          <button className="ghost-btn" onClick={() => void window.os.openLogs()}><FolderOpen size={13} /> Open logs</button>
        </div>
        <details>
          <summary>Details</summary>
          <pre>{message}</pre>
          <p className="muted">
            Running from source? Set the backend up once with <code>cd backend && uv venv && uv pip install -e .</code>, then relaunch.
            Or run it yourself and set <code>PERSONAL_OS_BACKEND_URL</code>.
          </p>
        </details>
      </div>
    </div>
  )
}
