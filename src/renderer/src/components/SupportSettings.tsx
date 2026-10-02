import { useState } from 'react'
import { Copy, Download, FolderOpen, RefreshCw } from 'lucide-react'
import { api } from '../lib/api'
import { useStore } from '../store'
import type { Settings } from '@shared/types'

/**
 * Support and reliability: the diagnostics bundle (backend report with secrets masked, plus what only the
 * main process knows: app version, supervisor state, restart history), the log folder, a manual backend
 * restart, and the knobs for provider retries and for how long bookkeeping history is kept.
 * Two components, because the knobs belong to the settings draft and the support buttons act at once.
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

function Num({ label, hint, unit, value, min, max, onChange }: { label: string; hint?: string; unit?: string; value: number; min: number; max: number; onChange: (v: number) => void }): JSX.Element {
  // Typed text is held locally and clamped on blur: clamping each keystroke made "30" unenterable with a minimum of 7.
  const [text, setText] = useState<string | null>(null)
  const commit = (): void => {
    const n = Number(text)
    if (text !== null && text.trim() !== '' && Number.isFinite(n)) onChange(Math.min(max, Math.max(min, Math.round(n))))
    setText(null)
  }
  // A value already in range goes to the draft as it is typed, so Save knows about it before the blur.
  const type = (raw: string): void => {
    setText(raw)
    const n = Number(raw)
    if (raw.trim() !== '' && Number.isInteger(n) && n >= min && n <= max) onChange(n)
  }
  return (
    <label className="setting-row">
      <span className="toggle-text"><b>{label}</b>{hint && <small>{hint}</small>}</span>
      <span className="num-unit">
        <input type="number" min={min} max={max} value={text ?? value} onChange={(e) => type(e.target.value)} onBlur={commit} />
        {unit && <em>{unit}</em>}
      </span>
    </label>
  )
}

/** Provider retries and how long bookkeeping history is kept. Part of the settings draft, so it saves with it. */
export function ReliabilitySettings({ draft, patch }: { draft: Settings; patch: (p: Partial<Settings>) => void }): JSX.Element {
  return (
    <>
      <h4>Reliability</h4>
      <Num label="Provider retries" hint="For rate limits, 5xx and dropped connections, before a reply starts. 0 turns it off." value={draft.llmRetries ?? 3} min={0} max={10} onChange={(v) => patch({ llmRetries: v })} />
      <Num label="Stream idle timeout" hint="A reply that goes silent this long is abandoned." unit="seconds" value={draft.llmIdleSeconds ?? 90} min={10} max={3600} onChange={(v) => patch({ llmIdleSeconds: v })} />

      <h4>History kept</h4>
      <p className="muted small">A daily sweep trims bookkeeping only. Your messages, memories, documents and notes are never deleted.</p>
      <Num label="Usage log" unit="days" value={draft.retainUsageDays ?? 365} min={7} max={3650} onChange={(v) => patch({ retainUsageDays: v })} />
      <Num label="Run traces on old replies" unit="days" value={draft.retainTraceDays ?? 60} min={1} max={3650} onChange={(v) => patch({ retainTraceDays: v })} />
      <Num label="Stored tool results" unit="days" value={draft.retainToolResultDays ?? 30} min={1} max={3650} onChange={(v) => patch({ retainToolResultDays: v })} />
      <Num label="Decided approvals and run journal" unit="days" value={draft.retainApprovalDays ?? 90} min={1} max={3650} onChange={(v) => patch({ retainApprovalDays: v })} />
    </>
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
        Diagnostics bundle versions, settings with keys masked, the last 300 log lines (secrets redacted) and the backend's restart history. Your messages and documents are not included.
      </p>
      <div className="button-row">
        <button type="button" className="ghost-btn" disabled={busy} onClick={() => void copy()}><Copy size={14} /> Copy diagnostics</button>
        <button type="button" className="ghost-btn" disabled={busy} onClick={() => void save()}><Download size={14} /> Save diagnostics…</button>
        <button type="button" className="ghost-btn" onClick={() => void window.os.openLogs()}><FolderOpen size={14} /> Open logs</button>
        <button type="button" className="ghost-btn" disabled={busy} onClick={() => void run(() => restart())}><RefreshCw size={14} /> Restart backend</button>
      </div>
    </>
  )
}
