import KernelSpinner from './KernelSpinner'
import { useStore } from '../store'

/** Shown while the main process is restarting a sidecar that died; the UI stays up underneath it. */
export function BackendBanner(): JSX.Element | null {
  const state = useStore((s) => s.backendState)
  if (state !== 'restarting' && state !== 'starting') return null
  return (
    <div className="backend-banner" role="status">
      <KernelSpinner size={14} />
      <span>Restarting the backend…</span>
    </div>
  )
}
