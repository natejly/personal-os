import { useState } from 'react'
import { CircleHelp, Copy, Download, FolderOpen, RefreshCw } from '../icons'
import { api } from '../lib/api'
import { useStore } from '../store'

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

export default function SupportSettings(): JSX.Element {
  // Diagnostics bundle, log folder and backend restart. Each button acts at once.
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
