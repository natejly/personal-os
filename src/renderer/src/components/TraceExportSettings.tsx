import { useState } from 'react'
import { api } from '../lib/api'
import type { OtelExportConfig } from '@shared/types'
import Switch from './settings/Switch'

const DEFAULTS: OtelExportConfig = { enabled: false, endpoint: '', headers: {}, includeContent: false, allowRemote: false, timeoutSeconds: 5 }

/** Settings block for the opt-in OTLP trace export. Off by default; nothing leaves the machine unless this is filled in. */
export default function TraceExportSettings({ value, onChange }: { value?: OtelExportConfig; onChange: (v: OtelExportConfig) => void }): JSX.Element {
  const cfg = { ...DEFAULTS, ...(value ?? {}) }
  const [result, setResult] = useState<string | null>(null)
  const set = (p: Partial<OtelExportConfig>): void => onChange({ ...cfg, ...p })
  const test = async (): Promise<void> => {
    setResult('Sending…')
    try {
      const r = await api.testTraceExport()
      setResult(r.sent ? `Sent (HTTP ${r.status}).` : r.reason === 'remote-blocked' ? 'Blocked: that endpoint is not on this machine.' : r.reason === 'disabled' ? 'Turn export on and save first.' : `Failed: ${r.error ?? 'unknown error'}`)
    } catch (e) {
      setResult(`Failed: ${(e as Error).message}`)
    }
  }
  return (
    <>
      <h4>Trace export</h4>
      <Switch title="Export traces as OpenTelemetry" help="Sends each reply's trace to a collector. Save settings before testing." checked={cfg.enabled} onChange={(v) => set({ enabled: v })} />
      {cfg.enabled && (
        <>
          <label><span className="toggle-text"><b>Endpoint</b></span><input value={cfg.endpoint} onChange={(e) => set({ endpoint: e.target.value })} placeholder="http://localhost:6006" spellCheck={false} /></label>
          <Switch title="Include message content" help="Tool arguments and the reply text. Never the system prompt or memories." checked={cfg.includeContent} onChange={(v) => set({ includeContent: v })} />
          <Switch title="Send traces off this machine" help="Required for any endpoint that is not localhost." checked={cfg.allowRemote} onChange={(v) => set({ allowRemote: v })} />
          <div className="test-row">
            <button type="button" className="ghost-btn" onClick={() => void test()}>Send test span</button>
            {result && <span className="test-msg" role="status">{result}</span>}
          </div>
        </>
      )}
    </>
  )
}
