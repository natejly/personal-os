import { useCallback, useEffect, useRef, useState } from 'react'
import {
  AlertTriangle, Check, ChevronDown, ChevronRight, Plug, Plus, RefreshCw, ScrollText, ShieldAlert, Trash2, X
} from 'lucide-react'
import { api } from '../lib/api'
import { useStore } from '../store'
import type { McpReport, McpServer, McpServerDraft, McpTool, ToolMode } from '@shared/types'

/** A connector that is coming up gets polled; one that has settled does not. */
const POLL_MS = 2500

const EMPTY: McpServerDraft & { secretsText: string; envText: string; argv: string } = {
  name: '', transport: 'stdio', command: '', args: [], cwd: '', env: {}, secrets: {}, description: '',
  argv: '', envText: '', secretsText: ''
}

/** Split a pasted command line into argv, honouring simple quoting. */
export const tokenize = (line: string): string[] =>
  (line.match(/"[^"]*"|'[^']*'|\S+/g) ?? []).map((t) => t.replace(/^(['"])([\s\S]*)\1$/, '$2'))

/** The inverse, for showing a stored command back in one field. */
export const joinArgv = (command: string, args: string[]): string =>
  [command, ...args].filter(Boolean).map((a) => (/\s/.test(a) ? `"${a}"` : a)).join(' ')

export const parseEnvText = (text: string): Record<string, string> =>
  Object.fromEntries(
    text
      .split('\n')
      .map((l) => l.trim())
      .filter((l) => l && !l.startsWith('#'))
      .map((l) => {
        const i = l.indexOf('=')
        return i < 0 ? [l, ''] : [l.slice(0, i).trim(), l.slice(i + 1).trim()]
      })
      .filter(([k]) => k)
  )

const envToText = (env: Record<string, string>): string =>
  Object.entries(env)
    .map(([k, v]) => `${k}=${v}`)
    .join('\n')

/**
 * Accept a `claude_desktop_config.json`-style block, which is how MCP servers are published:
 * `{"mcpServers": {"name": {"command": …, "args": […]}}}`, or just the inner object.
 */
export function fromConfigJson(text: string): { name?: string; argv: string; envText: string } | null {
  let j: unknown
  try {
    j = JSON.parse(text)
  } catch {
    return null
  }
  if (!j || typeof j !== 'object') return null
  const obj = j as Record<string, any>
  const servers = obj.mcpServers ?? obj.servers
  let name: string | undefined
  let entry: Record<string, any> | undefined = obj
  if (servers && typeof servers === 'object') {
    const [k, v] = Object.entries(servers)[0] ?? []
    if (!k || !v || typeof v !== 'object') return null
    name = k
    entry = v as Record<string, any>
  }
  if (typeof entry?.command !== 'string') return null
  const args = Array.isArray(entry.args) ? entry.args.map(String) : []
  return { name, argv: joinArgv(entry.command, args), envText: envToText((entry.env ?? {}) as Record<string, string>) }
}

const DOT: Record<string, string> = { ready: 'ok', connecting: 'warn', error: 'bad', disabled: 'off', idle: 'off' }
const STATUS_WORD: Record<string, string> = {
  ready: 'connected', connecting: 'connecting…', error: 'failed', disabled: 'off', idle: 'not running'
}
const VERDICT: Record<string, string> = {
  pass: 'nothing suspicious found', warn: 'worth a look', fail: 'problems found', error: 'could not check'
}

function Report({ report, label }: { report: Pick<McpReport, 'status' | 'summary' | 'findings'> & Partial<McpReport>; label?: string }): JSX.Element {
  return (
    <div className={`mcp-report ${report.status}`}>
      <div className="mcp-report-head">
        {report.status === 'pass' ? <Check size={13} /> : <ShieldAlert size={13} />}
        <b>{label ? `${label}: ` : ''}{VERDICT[report.status] ?? report.status}</b>
        <span className="muted">{report.summary}</span>
      </div>
      {report.findings.length > 0 && (
        <ul className="mcp-findings">
          {report.findings.map((f, i) => (
            <li key={`${f.code}-${i}`} className={f.severity}>
              <span className="tag">{f.severity}</span>
              <span>
                <b>{f.where}</b> — {f.detail}
                {f.excerpt && <code>{f.excerpt}</code>}
              </span>
            </li>
          ))}
        </ul>
      )}
      {/* mcp_eval states outright that a clean report is not proof of safety; it ships the caveats
          with every report precisely so they are shown, not summarised away. */}
      {!!report.limits?.length && (
        <details className="mcp-limits">
          <summary>What this check cannot tell you</summary>
          <ul>{report.limits.map((l) => <li key={l}>{l}</li>)}</ul>
        </details>
      )}
    </div>
  )
}

function ToolRow({ tool, onMode }: { tool: McpTool; onMode: (mode: ToolMode) => void }): JSX.Element {
  const eff = tool.effective
  const gone = !!tool.missing_since
  return (
    <div className={`tool-perm row mcp-tool ${eff.mode === 'off' ? 'off' : ''} ${gone ? 'unavailable' : ''}`}>
      <span className="toggle-text">
        <b>
          {tool.name}
          {eff.stale && <span className="tag ask" title={`Approved shape ${eff.approved_hash.slice(0, 8)}, now offering ${eff.schema_hash.slice(0, 8)}`}>changed since approved</span>}
          {gone && <span className="tag">no longer offered</span>}
        </b>
        <small>{tool.description || <i className="muted">no description</i>}</small>
        <small className="muted mono">{tool.slug}</small>
      </span>
      <div className="seg">
        {(['on', 'ask', 'off'] as ToolMode[]).map((m) => (
          <button key={m} className={eff.mode === m ? 'on' : ''} disabled={gone} onClick={() => onMode(m)}>{m}</button>
        ))}
      </div>
    </div>
  )
}

export default function McpSettings(): JSX.Element {
  const toast = useStore((s) => s.toast)
  const [servers, setServers] = useState<McpServer[]>([])
  const [open, setOpen] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  const [draft, setDraft] = useState(EMPTY)
  const [draftReport, setDraftReport] = useState<McpReport | null>(null)
  const [reports, setReports] = useState<Record<string, McpReport>>({})
  const [logs, setLogs] = useState<Record<string, string[]>>({})
  const [busy, setBusy] = useState<string>('')
  const [confirmDel, setConfirmDel] = useState<string | null>(null)
  const nameRef = useRef<HTMLInputElement>(null)

  const refresh = useCallback(async (): Promise<McpServer[]> => {
    try {
      const list = await api.mcp.servers()
      setServers(list)
      return list
    } catch (e) {
      toast((e as Error).message, 'error')
      return []
    }
  }, [toast])

  useEffect(() => { void refresh() }, [refresh])

  // Connecting is the only state that changes on its own, so polling stops once everything settles.
  const settling = servers.some((s) => s.enabled && !s.live.ready && s.live.status !== 'error')
  useEffect(() => {
    if (!settling) return
    const t = setInterval(() => void refresh(), POLL_MS)
    return () => clearInterval(t)
  }, [settling, refresh])

  const run = async (key: string, fn: () => Promise<unknown>): Promise<void> => {
    setBusy(key)
    try {
      await fn()
    } catch (e) {
      toast((e as Error).message, 'error')
    } finally {
      setBusy('')
    }
  }

  const applyPaste = (text: string): boolean => {
    const parsed = fromConfigJson(text)
    if (!parsed) return false
    setDraft((d) => ({ ...d, name: d.name || (parsed.name ?? ''), argv: parsed.argv, envText: parsed.envText || d.envText }))
    toast('Loaded from config JSON')
    return true
  }

  const draftConfig = (): Partial<McpServerDraft> => {
    const [command = '', ...args] = tokenize(draft.argv)
    return {
      name: draft.name.trim() || command, transport: 'stdio', command, args, cwd: draft.cwd.trim(),
      env: parseEnvText(draft.envText), secrets: parseEnvText(draft.secretsText), description: draft.description
    }
  }

  const checkDraft = (): Promise<void> =>
    run('draft-check', async () => {
      const cfg = draftConfig()
      if (!cfg.command) throw new Error('Enter the command that starts the server')
      setDraftReport(await api.mcp.checkDraft(cfg))
    })

  const addServer = (): Promise<void> =>
    run('draft-add', async () => {
      const cfg = draftConfig()
      if (!cfg.command) throw new Error('Enter the command that starts the server')
      const created = await api.mcp.create({ ...cfg, name: cfg.name || 'MCP server', enabled: true })
      setDraft(EMPTY)
      setDraftReport(null)
      setAdding(false)
      setOpen(created.id)
      await refresh()
      toast(`Added ${created.name} — its tools ask before running`)
    })

  const setMode = (slug: string, mode: ToolMode): Promise<void> =>
    run(`grant-${slug}`, async () => {
      await api.mcp.setGrant(slug, mode, 'global')
      await refresh()
    })

  return (
    <div className="mcp">
      <p className="muted">
        Connectors are <a href="https://modelcontextprotocol.io/" target="_blank" rel="noreferrer">MCP</a> servers:
        other people&apos;s programs that hand the assistant extra tools. They run on this machine, under your account,
        with whatever access you give them. Nothing is added or enabled unless you do it here, and every tool from a
        connector <b>asks before it runs</b> until you say otherwise.
      </p>

      {servers.length === 0 && !adding && <p className="muted empty">No connectors yet.</p>}

      {servers.map((s) => {
        const dot = DOT[s.live.status] ?? 'off'
        const expanded = open === s.id
        const live = s.tools.filter((t) => !t.missing_since)
        return (
          <div key={s.id} className={`mcp-server ${expanded ? 'open' : ''}`}>
            <div className="mcp-head">
              <button className="icon-btn" aria-label={expanded ? 'Collapse' : 'Expand'} onClick={() => setOpen(expanded ? null : s.id)}>
                {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
              </button>
              <span className={`dot ${dot}`} />
              <span className="mcp-name">
                <b>{s.name}</b>
                <small className="muted">
                  {STATUS_WORD[s.live.status] ?? s.live.status}
                  {s.live.server_info?.version ? ` · ${s.live.server_info.name} ${s.live.server_info.version}` : ''}
                  {live.length ? ` · ${live.length} tool${live.length === 1 ? '' : 's'}` : ''}
                </small>
              </span>
              {s.eval && s.eval.status !== 'pass' && (
                <span className={`tag ${s.eval.status === 'warn' ? 'ask' : 'bad'}`} title={s.eval.summary}>
                  <AlertTriangle size={11} /> {s.eval.status}
                </span>
              )}
              <label className="switch-wrap" title={s.enabled ? 'Turn this connector off' : 'Turn this connector on'}>
                <input type="checkbox" checked={s.enabled} disabled={busy === `tog-${s.id}`}
                  onChange={(e) => void run(`tog-${s.id}`, async () => { await api.mcp.update(s.id, { enabled: e.target.checked }); await refresh() })} />
                <span className="switch" />
              </label>
            </div>

            {s.live.status === 'error' && s.live.detail && (
              <p className="test-msg fail mcp-detail">{s.live.detail}</p>
            )}

            {expanded && (
              <div className="mcp-body">
                <div className="mcp-meta">
                  <code className="mono">{joinArgv(s.command, s.args)}</code>
                  {s.cwd && <small className="muted">in {s.cwd}</small>}
                  {s.secret_keys.length > 0 && <small className="muted">secrets: {s.secret_keys.join(', ')} (stored in the backend, never shown)</small>}
                </div>

                <div className="mcp-actions">
                  <button className="ghost-btn" disabled={busy === `chk-${s.id}`}
                    onClick={() => void run(`chk-${s.id}`, async () => {
                      const report = await api.mcp.check(s.id)
                      setReports((r) => ({ ...r, [s.id]: report }))
                      await refresh()
                    })}>
                    <ShieldAlert size={13} /> {busy === `chk-${s.id}` ? 'Checking…' : 'Check'}
                  </button>
                  <button className="ghost-btn" disabled={busy === `re-${s.id}`}
                    onClick={() => void run(`re-${s.id}`, async () => { await api.mcp.restart(s.id); await refresh() })}>
                    <RefreshCw size={13} /> Restart
                  </button>
                  <button className="ghost-btn" disabled={busy === `log-${s.id}`}
                    onClick={() => void run(`log-${s.id}`, async () => {
                      const { stderr } = await api.mcp.logs(s.id)
                      setLogs((l) => ({ ...l, [s.id]: stderr }))
                    })}>
                    <ScrollText size={13} /> Logs
                  </button>
                  {confirmDel === s.id ? (
                    <>
                      <button className="ghost-btn danger" onClick={() => void run(`del-${s.id}`, async () => { await api.mcp.remove(s.id); setConfirmDel(null); await refresh() })}>
                        Really remove
                      </button>
                      <button className="ghost-btn" onClick={() => setConfirmDel(null)}>Keep</button>
                    </>
                  ) : (
                    <button className="ghost-btn danger" onClick={() => setConfirmDel(s.id)}><Trash2 size={13} /> Remove</button>
                  )}
                </div>

                {/* A filed report carries its findings, so a failed verdict is never just a one-liner. */}
                {reports[s.id] ? <Report report={reports[s.id]} /> : s.eval ? <Report report={s.eval} label="last check" /> : null}

                {logs[s.id] && (
                  <pre className="mcp-log">{logs[s.id].length ? logs[s.id].join('\n') : 'Nothing on stderr.'}</pre>
                )}

                {s.tools.length > 0 ? (
                  <div className="tool-perms">
                    <h5>Tools</h5>
                    {s.tools.map((t) => <ToolRow key={t.slug} tool={t} onMode={(m) => void setMode(t.slug, m)} />)}
                  </div>
                ) : (
                  <p className="muted empty">{s.live.ready ? 'This server offers no tools.' : 'Tools appear once the server connects.'}</p>
                )}
              </div>
            )}
          </div>
        )
      })}

      {adding ? (
        <div className="mcp-server open mcp-add">
          <div className="mcp-head"><b>Add a connector</b>
            <button className="icon-btn" aria-label="Cancel" onClick={() => { setAdding(false); setDraftReport(null) }}><X size={14} /></button>
          </div>
          <div className="mcp-body">
            <label><span>Name</span>
              <input ref={nameRef} value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} placeholder="Filesystem" spellCheck={false} />
            </label>
            <label><span>Command <small className="muted">(or paste the server&apos;s config JSON here)</small></span>
              <input value={draft.argv} spellCheck={false} placeholder="npx -y @modelcontextprotocol/server-filesystem ~/Documents"
                onChange={(e) => setDraft({ ...draft, argv: e.target.value })}
                onPaste={(e) => {
                  const text = e.clipboardData.getData('text')
                  if (text.trim().startsWith('{') && applyPaste(text)) e.preventDefault()
                }} />
            </label>
            <label><span>Working directory <small className="muted">(optional)</small></span>
              <input value={draft.cwd} onChange={(e) => setDraft({ ...draft, cwd: e.target.value })} placeholder="~/code/project" spellCheck={false} />
            </label>
            <label><span>Environment <small className="muted">(KEY=value, one per line)</small></span>
              <textarea rows={2} value={draft.envText} onChange={(e) => setDraft({ ...draft, envText: e.target.value })} spellCheck={false} />
            </label>
            <label><span>Secrets <small className="muted">(KEY=value; kept in the backend and never sent to a model or shown again)</small></span>
              <textarea rows={2} value={draft.secretsText} onChange={(e) => setDraft({ ...draft, secretsText: e.target.value })} spellCheck={false} />
            </label>
            <div className="mcp-actions">
              <button className="ghost-btn" disabled={busy === 'draft-check'} onClick={() => void checkDraft()}>
                <ShieldAlert size={13} /> {busy === 'draft-check' ? 'Checking…' : 'Check it first'}
              </button>
              <button className="primary-btn" disabled={busy === 'draft-add'} onClick={() => void addServer()}>Add connector</button>
            </div>
            {draftReport && <Report report={draftReport} />}
            {draftReport?.stderr?.length ? <pre className="mcp-log">{draftReport.stderr.join('\n')}</pre> : null}
          </div>
        </div>
      ) : (
        <button className="ghost-btn" onClick={() => { setAdding(true); requestAnimationFrame(() => nameRef.current?.focus()) }}>
          <Plus size={13} /> Add a connector
        </button>
      )}

      {servers.length > 0 && (
        <p className="muted mcp-foot">
          <Plug size={12} /> A connector&apos;s tools are offered to the assistant only while its server is connected.
          A server that changes a tool after you approved it goes back to asking.
        </p>
      )}
    </div>
  )
}
