import { useState } from 'react'
import { api } from '../lib/api'
import type { OtelExportConfig } from '@shared/types'

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
      <h4 className="usage-sub">Trace export (developer)</h4>
      <label className="toggle-row plain">
        <span className="toggle-text"><b>Export traces as OpenTelemetry</b><small>After each reply, post its trace (OTLP/JSON, GenAI conventions) to a collector such as a local Phoenix. Off by default. Save settings before testing.</small></span>
        <input type="checkbox" checked={cfg.enabled} onChange={(e) => set({ enabled: e.target.checked })} /><span className="switch" />
      </label>
      {cfg.enabled && (
        <>
          <label><span>Endpoint</span><input value={cfg.endpoint} onChange={(e) => set({ endpoint: e.target.value })} placeholder="http://localhost:6006" spellCheck={false} /></label>
          <label className="toggle-row plain">
            <span className="toggle-text"><b>Include message content</b><small>Tool arguments and the reply text. Never the system prompt or memories, and never for a reply that used activity or meetings.</small></span>
            <input type="checkbox" checked={cfg.includeContent} onChange={(e) => set({ includeContent: e.target.checked })} /><span className="switch" />
          </label>
          <label className="toggle-row plain">
            <span className="toggle-text"><b>Send traces off this machine</b><small>Required for any endpoint that is not localhost.</small></span>
            <input type="checkbox" checked={cfg.allowRemote} onChange={(e) => set({ allowRemote: e.target.checked })} /><span className="switch" />
          </label>
          <div className="row">
            <button type="button" className="ghost-btn" onClick={() => void test()}>Send test span</button>
            {result && <span className="muted small"> {result}</span>}
          </div>
        </>
      )}
    </>
  )
}
