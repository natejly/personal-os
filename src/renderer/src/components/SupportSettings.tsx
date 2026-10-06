import { useState } from 'react'
import { CircleHelp, Copy, Download, FolderOpen, RefreshCw } from 'lucide-react'
import { api } from '../lib/api'
import { useStore } from '../store'

/**
 * Support and reliability: the diagnostics bundle (backend report with secrets masked, plus what only the
 * main process knows: app version, supervisor state, restart history), the log folder, a manual backend
 * restart. Each button acts at once.
 */

async function buildReport(): Promise<string> {
  const [backend, main] = await Promise.all([
    api.diagnostics().catch((e: Error) => ({ error: `Backend unreachable: ${e.message}` })),
    window.os.backendInfo().catch(() => null)
  ])
  return JSON.stringify(
    {
      app: main && { version: main.appVersion, electron: main.electron, platform: window.os.platform, backendState: main.state, backendError: main.error, restarts: main.restarts, logDir: main.logDir },
      renderer: { userAgent: navigator.userAgent },
      backend
    },
    null,
    2
  )
}

/** The diagnostics bundle, the log folder and a manual backend restart. Each button acts at once. */
export default function SupportSettings(): JSX.Element {
  const toast = useStore((s) => s.toast)
  const restart = useStore((s) => s.restartBackend)
  const [busy, setBusy] = useState(false)

  const run = async (fn: () => Promise<void>): Promise<void> => {
    setBusy(true)
    try {
      await fn()
    } catch (e) {
      toast((e as Error).message, 'error')
    } finally {
      setBusy(false)
    }
  }
  const copy = (): Promise<void> => run(async () => {
    await navigator.clipboard.writeText(await buildReport())
    toast('Diagnostics copied. Secrets are masked; skim it before sharing.')
  })
  const save = (): Promise<void> => run(async () => {
    const url = URL.createObjectURL(new Blob([await buildReport()], { type: 'application/json' }))
    const a = document.createElement('a')
    a.href = url
    a.download = `grain-diagnostics-${new Date().toISOString().slice(0, 10)}.json`
    a.click()
    setTimeout(() => URL.revokeObjectURL(url), 10_000)
  })

  return (
    <>
      <h4>Support</h4>
      <p className="muted small">
        Diagnostics bundle versions, settings with keys masked, the last 300 log lines (secrets redacted) and the backend's restart history. Your messages and files are not included.
      </p>
      <div className="button-row">
        <button type="button" className="ghost-btn" onClick={() => useStore.getState().openHelp('guide')}><CircleHelp size={14} /> Help</button>
        <button type="button" className="ghost-btn" disabled={busy} onClick={() => void copy()}><Copy size={14} /> Copy diagnostics</button>
        <button type="button" className="ghost-btn" disabled={busy} onClick={() => void save()}><Download size={14} /> Save diagnostics…</button>
        <button type="button" className="ghost-btn" onClick={() => void window.os.openLogs()}><FolderOpen size={14} /> Open logs</button>
        <button type="button" className="ghost-btn" disabled={busy} onClick={() => void run(() => restart())}><RefreshCw size={14} /> Restart backend</button>
      </div>
    </>
  )
}
