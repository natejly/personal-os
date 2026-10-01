import { useState } from 'react'
import { AlertTriangle, ChevronDown, ChevronRight, Plug, Plus, RefreshCw, ScrollText, ShieldCheck, Trash2 } from 'lucide-react'
import { useStore } from '../store'
import { api } from '../lib/api'
import type { McpReport, McpServer, McpTool, ToolMode } from '@shared/types'

const STATUS_LABEL: Record<McpServer['status'], string> = {
  idle: 'not started', connecting: 'connecting', ready: 'connected', error: 'failed', disabled: 'off'
}
const EVAL_LABEL: Record<string, string> = { pass: 'nothing suspicious', warn: 'worth a look', fail: 'problems found', error: 'did not connect' }

/** `args` is argv, not a shell line, so it is edited as whitespace-separated words and never parsed by a shell. */
const splitArgs = (s: string): string[] => s.split(/\s+/).filter(Boolean)

function Findings({ report }: { report: Pick<McpReport, 'status' | 'summary' | 'findings'> }): JSX.Element {
  return (
    <div className={`mcp-eval ${report.status}`}>
      <b>{EVAL_LABEL[report.status] ?? report.status}</b> — {report.summary}
      {report.findings.length > 0 && (
        <ul>
          {report.findings.map((f, i) => (
            <li key={i}><span className={`tag ${f.severity}`}>{f.severity}</span> <code>{f.where}</code> {f.detail}</li>
          ))}
        </ul>
      )}
    </div>
  )
}

function ToolRow({ tool }: { tool: McpTool }): JSX.Element {
  const { setConnectorGrant } = useStore()
  return (
    <div className={`mcp-tool ${tool.mode === 'off' ? 'off' : ''} ${tool.missing ? 'unavailable' : ''}`}>
      <span className="mcp-tool-name">
        {tool.name}
        <small className="muted" title="What the model sees. Derived here, never chosen by the server.">{tool.slug}</small>
      </span>
      <span className="mcp-tool-desc muted">{tool.description || '(no description)'}</span>
      {tool.stale && (
        <span className="tag warn" title="The server changed this tool's shape since you approved it, so it asks again until you re-approve.">
          <AlertTriangle size={11} /> shape changed
        </span>
      )}
      {tool.missing && <span className="tag">withdrawn</span>}
      {!tool.connected && !tool.missing && <span className="tag">offline</span>}
      <div className="seg">
        {(['on', 'ask', 'off'] as ToolMode[]).map((m) => (
          <button key={m} className={tool.mode === m ? 'on' : ''} disabled={tool.missing}
            onClick={() => void setConnectorGrant(tool.slug, m)}>{m}</button>
        ))}
      </div>
    </div>
  )
}

function ServerCard({ server }: { server: McpServer }): JSX.Element {
  const tools = useStore((s) => s.mcpTools.filter((t) => t.server_id === server.id))
  const busy = useStore((s) => s.mcpBusy === server.id)
  const { updateConnector, deleteConnector, restartConnector, evaluateConnector } = useStore()
  const [open, setOpen] = useState(false)
  const [log, setLog] = useState<string[] | null>(null)

  const showLog = async (): Promise<void> => setLog(log ? null : (await api.mcp.log(server.id)).lines)

  return (
    <div className={`mcp-server ${server.status}`}>
      <div className="mcp-head" onClick={() => setOpen(!open)} role="button" tabIndex={0}
        onKeyDown={(e) => { if (e.key === 'Enter') setOpen(!open) }}>
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <span className={`dot ${server.status}`} />
        <span className="mcp-name">{server.name}<small className="muted">{server.slug}</small></span>
        <span className="muted small">{STATUS_LABEL[server.status]}{server.detail ? ` — ${server.detail}` : ''}</span>
        <span className="muted small">{server.tool_count} tool{server.tool_count === 1 ? '' : 's'}</span>
        {server.eval && <span className={`tag ${server.eval.status}`}>{EVAL_LABEL[server.eval.status] ?? server.eval.status}</span>}
        <div className="mcp-actions no-drag" onClick={(e) => e.stopPropagation()}>
          <div className="seg" title={server.enabled ? 'Stop this connector' : 'Start this connector'}>
            {([true, false] as const).map((on) => (
              <button key={String(on)} className={server.enabled === on ? 'on' : ''} disabled={busy}
                onClick={() => void updateConnector(server.id, { enabled: on })}>{on ? 'on' : 'off'}</button>
            ))}
          </div>
          <button className="icon-btn" aria-label="Restart" title="Restart" disabled={busy || !server.enabled}
            onClick={() => void restartConnector(server.id)}><RefreshCw size={13} /></button>
          <button className="icon-btn" aria-label="Review" title="Review what this server advertises" disabled={busy}
            onClick={() => void evaluateConnector(server.id)}><ShieldCheck size={13} /></button>
          <button className="icon-btn" aria-label="Log" title="Server output" onClick={() => void showLog()}><ScrollText size={13} /></button>
          <button className="icon-btn ghost danger" aria-label={`Remove ${server.name}`} onClick={() => void deleteConnector(server.id)}><Trash2 size={13} /></button>
        </div>
      </div>
      {open && (
        <div className="mcp-body">
          <div className="mcp-config muted small">
            <code>{server.command} {server.args.join(' ')}</code>
            {server.cwd && <span> in <code>{server.cwd}</code></span>}
            {server.server_info?.name && <span> · {server.server_info.name} {server.server_info.version}</span>}
          </div>
          {server.eval && <Findings report={server.eval} />}
          {tools.length === 0 && <p className="muted small">No tools discovered yet. Turn the connector on, then restart it if nothing appears.</p>}
          {tools.map((t) => <ToolRow key={t.id} tool={t} />)}
        </div>
      )}
      {log && (
        <pre className="mcp-log">{log.length ? log.join('\n') : '(nothing on stderr)'}</pre>
      )}
    </div>
  )
}

const BLANK = { name: '', command: '', args: '', cwd: '', description: '' }

export default function ConnectorsPanel(): JSX.Element {
  const servers = useStore((s) => s.mcpServers)
  const { addConnector } = useStore()
  const [adding, setAdding] = useState(false)
  const [form, setForm] = useState(BLANK)
  const [report, setReport] = useState<McpReport | null>(null)
  const [testing, setTesting] = useState(false)

  const test = async (): Promise<void> => {
    setTesting(true)
    try {
      setReport(await api.mcp.probe({ command: form.command.trim(), args: splitArgs(form.args), cwd: form.cwd.trim() }))
    } catch (e) {
      setReport({ status: 'error', summary: (e as Error).message, findings: [], tools: [], server_info: {}, stderr: [] })
    } finally {
      setTesting(false)
    }
  }

  const save = async (): Promise<void> => {
    const srv = await addConnector({
      name: form.name.trim() || 'Connector', command: form.command.trim(),
      args: splitArgs(form.args), cwd: form.cwd.trim(), description: form.description.trim()
    })
    if (srv) {
      setForm(BLANK)
      setReport(null)
      setAdding(false)
    }
  }

  return (
    <div className="library-panel">
      <div className="add-row">
        <button className="primary-btn" onClick={() => setAdding(!adding)}><Plus size={14} /> Add connector</button>
        <span className="muted small">
          An MCP server runs as a local process and its tools join the assistant’s toolbox under their own names.
          A new connector starts off, every tool asks before it runs, and a permission you grant is tied to the exact
          tool you saw — if the server changes it, it has to ask again.
        </span>
      </div>
      {adding && (
        <div className="skill-body standalone">
          <label>Name<input autoFocus value={form.name} placeholder="Linear" onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
          <label>Command<input value={form.command} placeholder="npx" onChange={(e) => setForm({ ...form, command: e.target.value })} /></label>
          <label>Arguments<input value={form.args} placeholder="-y @modelcontextprotocol/server-filesystem /Users/me/notes" onChange={(e) => setForm({ ...form, args: e.target.value })} /></label>
          <label>Working directory <small className="muted">optional</small><input value={form.cwd} onChange={(e) => setForm({ ...form, cwd: e.target.value })} /></label>
          <div className="row-actions">
            <button className="small" disabled={!form.command.trim() || testing} onClick={() => void test()}>
              {testing ? 'Connecting…' : 'Test & review'}
            </button>
            <button className="primary-btn small" disabled={!form.command.trim()} onClick={() => void save()}>Add</button>
            <button className="small" onClick={() => { setAdding(false); setReport(null) }}>Cancel</button>
            <span className="muted small">Test first: it connects once, lists the tools, and throws the connection away.</span>
          </div>
          {report && (
            <>
              <Findings report={report} />
              {report.tools.length > 0 && (
                <ul className="mcp-probe-tools">
                  {report.tools.map((t, i) => <li key={i}><code>{t.name}</code> <span className={`tag ${t.status}`}>{t.status}</span></li>)}
                </ul>
              )}
              {report.stderr.length > 0 && <pre className="mcp-log">{report.stderr.join('\n')}</pre>}
            </>
          )}
        </div>
      )}
      {servers.length === 0 && !adding && (
        <div className="empty-hint big">
          <Plug size={22} />
          <p>No connectors yet.</p>
          <p className="muted small">Add an MCP server to give the assistant tools this app does not ship with.</p>
        </div>
      )}
      {servers.map((s) => <ServerCard key={s.id} server={s} />)}
    </div>
  )
}
