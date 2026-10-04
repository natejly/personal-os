import { useCallback, useEffect, useRef, useState } from 'react'
import {
  AlertTriangle, Check, ChevronDown, ChevronRight, ExternalLink, LogOut, Plug, Plus, RefreshCw, ScrollText, ShieldAlert, Trash2, X
} from 'lucide-react'
import { api } from '../lib/api'
import { useStore } from '../store'
import type { McpGrant, McpReport, McpServer, McpServerDraft, McpSignIn, McpTool, ToolMode } from '@shared/types'

/** A connector that is coming up gets polled; one that has settled does not. */
const POLL_MS = 2500

type HeaderRow = { k: string; v: string }
const EMPTY: McpServerDraft & { secretsText: string; envText: string; argv: string; headerRows: HeaderRow[] } = {
  name: '', transport: 'stdio', command: '', args: [], cwd: '', env: {}, secrets: {}, url: '', headers: {}, description: '',
  argv: '', envText: '', secretsText: '', headerRows: []
}

/** Start a remote server's browser sign-in and resolve once it settles: done, error, or given up after 5 minutes. */
export async function signInMcp(id: string): Promise<McpSignIn> {
  const st = await api.mcp.signIn(id)
  if (st.status === 'error') throw new Error(st.error)
  if (st.auth_url) window.open(st.auth_url, '_blank')
  for (let i = 0; i < 150; i++) {
    await new Promise((r) => setTimeout(r, 2000))
    const x = await api.mcp.signInStatus(id)
    if (x.status !== 'waiting' && x.status !== 'starting') return x
  }
  return { ...st, status: 'error', error: 'sign-in timed out' }
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
 * `{"mcpServers": {"name": {"command": …, "args": […]}}}`, or just the inner object. A remote entry
 * carries `{"url": …, "headers": {…}}` instead of a command.
 */
export function fromConfigJson(text: string):
  | { name?: string; argv: string; envText: string }
  | { name?: string; url: string; headers: Record<string, string> }
  | null {
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
  if (typeof entry?.url === 'string' && entry.url.trim()) {
    const headers = entry.headers && typeof entry.headers === 'object' ? entry.headers as Record<string, unknown> : {}
    return { name, url: entry.url.trim(), headers: Object.fromEntries(Object.entries(headers).map(([k, v]) => [k, String(v)])) }
  }
  if (typeof entry?.command !== 'string') return null
  const args = Array.isArray(entry.args) ? entry.args.map(String) : []
  return { name, argv: joinArgv(entry.command, args), envText: envToText((entry.env ?? {}) as Record<string, string>) }
}

const DOT: Record<string, string> = { ready: 'ok', connecting: 'warn', error: 'bad', disabled: 'off', idle: 'off' }
const STATUS_WORD: Record<string, string> = {
  ready: 'connected', connecting: 'connecting…', error: 'failed', disabled: 'off', idle: 'not running'
}
/** The supervisor stops with this detail when only a browser sign-in can help; it is not a failure. */
const needsSignIn = (s: McpServer): boolean => s.transport === 'http' && s.live.status === 'error' && s.live.detail === 'sign-in required'
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

/** What changed in a tool's definition since the user last saw it, exactly as the model would now read it. */
function DriftBanner({ tool, onAccept }: { tool: McpTool; onAccept: () => void }): JSX.Element | null {
  const d = tool.drift
  if (!d) return null
  const { diff } = d
  const moved = [
    diff.added_params.length ? `added ${diff.added_params.join(', ')}` : '',
    diff.removed_params.length ? `removed ${diff.removed_params.join(', ')}` : '',
    diff.changed_params.length ? `changed ${diff.changed_params.join(', ')}` : '',
    diff.new_required.length ? `now required: ${diff.new_required.join(', ')}` : ''
  ].filter(Boolean)
  return (
    <details className={`mcp-drift ${d.quarantined ? 'quarantined' : ''}`} open={d.quarantined}>
      <summary>
        <b>{d.quarantined ? `Quarantined: ${d.previous.schema_hash ? 'definition changed' : 'new tool'}` : 'Definition changed'}</b>
        {d.quarantined && <span className="muted"> — withheld from the assistant until you accept it</span>}
      </summary>
      {diff.description.length > 0 && (
        <pre className="mcp-diff">{diff.description.filter((l) => !l.startsWith('---') && !l.startsWith('+++')).map((l, i) => (
          <span key={i} className={l.startsWith('+') ? 'add' : l.startsWith('-') ? 'del' : ''}>{l + '\n'}</span>
        ))}</pre>
      )}
      {moved.length > 0 && <p className="small muted">Parameters: {moved.join('; ')}</p>}
      {d.new_findings.length > 0 && (
        <ul className="mcp-findings">
          {d.new_findings.map((f, i) => <li key={i} className={f.severity}><b>{f.code}</b> {f.detail}{f.excerpt ? <i className="muted"> “{f.excerpt}”</i> : null}</li>)}
        </ul>
      )}
      <div className="row-actions">
        <button className="primary-btn sm" onClick={onAccept}>{d.quarantined ? (d.previous.schema_hash ? 'Accept change' : 'Accept tool') : 'Mark as reviewed'}</button>
        <span className="muted small">Accepting does not turn the tool on: it still asks first.</span>
      </div>
    </details>
  )
}

/** A tool's project and chat grants (an approval card's "always for this chat" lands here), each revocable. */
function ScopedGrants({ tool, grants, onRevoke }: { tool: McpTool; grants: McpGrant[]; onRevoke: (g: McpGrant) => void }): JSX.Element | null {
  const projects = useStore((s) => s.projects)
  const conversations = useStore((s) => s.conversations)
  if (!grants.length) return null
  const title = (g: McpGrant): string =>
    (g.scope === 'project' ? projects.find((p) => p.id === g.scope_id)?.name : conversations.find((c) => c.id === g.scope_id)?.title) || g.scope_id
  return (
    <ul className="mcp-scoped-grants">
      {grants.map((g) => (
        <li key={g.id} className="small">
          <span className="muted">{g.scope === 'project' ? 'Project' : 'Chat'}</span> {title(g)}: <b>{g.mode}</b>
          {g.schema_hash && g.schema_hash !== tool.schema_hash && <span className="tag ask">stale</span>}
          <button className="ghost-btn small" onClick={() => onRevoke(g)}>Revoke</button>
        </li>
      ))}
    </ul>
  )
}

function ToolRow({ tool, grants, onMode, onAccept, onRevoke }: {
  tool: McpTool; grants: McpGrant[]; onMode: (mode: ToolMode) => void; onAccept: () => void; onRevoke: (g: McpGrant) => void
}): JSX.Element {
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
        <DriftBanner tool={tool} onAccept={onAccept} />
        <ScopedGrants tool={tool} grants={grants} onRevoke={onRevoke} />
      </span>
      <div className="seg" role="group" aria-label={`Permission for ${tool.name}`}>
        {(['on', 'ask', 'off'] as ToolMode[]).map((m) => (
          <button key={m} type="button" className={eff.mode === m ? 'on' : ''} aria-pressed={eff.mode === m} disabled={gone} onClick={() => onMode(m)}>{m}</button>
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
  const [grants, setGrants] = useState<McpGrant[]>([])
  const nameRef = useRef<HTMLInputElement>(null)
  const signPoll = useRef<ReturnType<typeof setInterval> | null>(null)
  useEffect(() => () => { if (signPoll.current) clearInterval(signPoll.current) }, [])

  const refresh = useCallback(async (): Promise<McpServer[]> => {
    try {
      const [list, t] = await Promise.all([api.mcp.servers(), api.mcp.tools()])
      setServers(list)
      setGrants(t.grants.filter((g) => g.scope !== 'global'))
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
    setDraft((d) => 'url' in parsed
      ? { ...d, name: d.name || (parsed.name ?? ''), transport: 'http', url: parsed.url,
          headerRows: Object.entries(parsed.headers).map(([k, v]) => ({ k, v })) }
      : { ...d, name: d.name || (parsed.name ?? ''), transport: 'stdio', argv: parsed.argv, envText: parsed.envText || d.envText })
    toast('Loaded from config JSON')
    return true
  }

  const draftConfig = (): Partial<McpServerDraft> => {
    if (draft.transport === 'http') {
      const url = draft.url.trim()
      const headers = Object.fromEntries(draft.headerRows.filter((h) => h.k.trim()).map((h) => [h.k.trim(), h.v]))
      return { name: draft.name.trim() || url.replace(/^https?:\/\//, '').split('/')[0], transport: 'http', url, headers, description: draft.description }
    }
    const [command = '', ...args] = tokenize(draft.argv)
    return {
      name: draft.name.trim() || command, transport: 'stdio', command, args, cwd: draft.cwd.trim(),
      env: parseEnvText(draft.envText), secrets: parseEnvText(draft.secretsText), description: draft.description
    }
  }

  const draftReady = (cfg: Partial<McpServerDraft>): void => {
    if (cfg.transport === 'http' ? !/^https?:\/\/\S+$/.test(cfg.url ?? '') : !cfg.command)
      throw new Error(cfg.transport === 'http' ? "Enter the server's https:// URL" : 'Enter the command that starts the server')
  }

  const checkDraft = (): Promise<void> =>
    run('draft-check', async () => {
      const cfg = draftConfig()
      draftReady(cfg)
      setDraftReport(await api.mcp.checkDraft(cfg))
    })

  /** Open the server's sign-in page in the browser, then poll until the backend has the answer. */
  const signIn = (s: McpServer): Promise<void> =>
    run(`sign-${s.id}`, async () => {
      const st = await api.mcp.signIn(s.id)
      if (st.status === 'error') throw new Error(st.error)
      if (st.auth_url) window.open(st.auth_url, '_blank')
      if (signPoll.current) clearInterval(signPoll.current)
      signPoll.current = setInterval(() => {
        void api.mcp.signInStatus(s.id).then(async (x) => {
          if (x.status === 'waiting' || x.status === 'starting') return
          if (signPoll.current) clearInterval(signPoll.current)
          if (x.status === 'error') toast(`${s.name} sign-in failed: ${x.error}`, 'error')
          await refresh()
        }).catch(() => undefined)
      }, 2000)
    })

  const signOut = (s: McpServer): Promise<void> =>
    run(`sign-${s.id}`, async () => { await api.mcp.signOut(s.id); await refresh() })

  const addServer = (): Promise<void> =>
    run('draft-add', async () => {
      const cfg = draftConfig()
      draftReady(cfg)
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

  const revokeGrant = (g: McpGrant): Promise<void> =>
    run(`revoke-${g.id}`, async () => {
      await api.mcp.clearGrant(g.tool_slug, g.scope, g.scope_id)
      await refresh()
    })

  const acceptChange = (slug: string): Promise<void> =>
    run(`accept-${slug}`, async () => {
      await api.mcp.acceptChange(slug)
      await refresh()
    })

  return (
    <div className="mcp">
      <p className="muted">
        Connectors are <a href="https://modelcontextprotocol.io/" target="_blank" rel="noreferrer">MCP</a> servers:
        other people&apos;s programs that hand the assistant extra tools. A local one runs on this machine, under your
        account; a remote one is a web address, reached with the headers or sign-in you give it. Nothing is added or enabled unless you do it here, and every tool from a
        connector <b>asks before it runs</b> until you say otherwise.
      </p>

      {servers.length === 0 && !adding && <p className="empty-row"><Plug size={15} /> No connectors yet. Add one to give the assistant more tools.</p>}

      {servers.map((s) => {
        const signin = needsSignIn(s)
        const dot = signin ? 'warn' : DOT[s.live.status] ?? 'off'
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
                  {signin ? 'needs sign-in' : STATUS_WORD[s.live.status] ?? s.live.status}
                  {s.live.server_info?.version ? ` · ${s.live.server_info.name} ${s.live.server_info.version}` : ''}
                  {live.length ? ` · ${live.length} tool${live.length === 1 ? '' : 's'}` : ''}
                </small>
              </span>
              {s.eval && s.eval.status !== 'pass' && (
                <span className={`tag ${s.eval.status === 'warn' ? 'ask' : 'bad'}`} title={s.eval.summary}>
                  <AlertTriangle size={11} /> {s.eval.status}
                </span>
              )}
              {signin && (
                <button className="primary-btn small" disabled={busy === `sign-${s.id}`} onClick={() => void signIn(s)}>
                  <ExternalLink size={12} /> Sign in
                </button>
              )}
              <label className="switch-wrap" title={s.enabled ? 'Turn this connector off' : 'Turn this connector on'}>
                <input type="checkbox" checked={s.enabled} disabled={busy === `tog-${s.id}`}
                  onChange={(e) => void run(`tog-${s.id}`, async () => { await api.mcp.update(s.id, { enabled: e.target.checked }); await refresh() })} />
                <span className="switch" />
              </label>
            </div>

            {s.live.status === 'error' && s.live.detail && !signin && (
              <p className="test-msg fail mcp-detail">{s.live.detail}</p>
            )}

            {expanded && (
              <div className="mcp-body">
                <div className="mcp-meta">
                  <code className="mono">{s.transport === 'http' ? s.url : joinArgv(s.command, s.args)}</code>
                  {s.cwd && <small className="muted">in {s.cwd}</small>}
                  {s.secret_keys.length > 0 && (
                    <small className="muted">{s.transport === 'http' ? 'headers' : 'secrets'}: {s.secret_keys.join(', ')} (stored in the backend, never shown)</small>
                  )}
                  {s.signed_in && <small className="muted">signed in</small>}
                </div>

                <div className="mcp-actions">
                  {/* Sign in is offered in the header once the server asks for it; a header-only server never does. */}
                  {s.signed_in && (
                    <button className="ghost-btn" disabled={busy === `sign-${s.id}`} onClick={() => void signOut(s)}>
                      <LogOut size={13} /> Sign out
                    </button>
                  )}
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
                    {s.tools.map((t) => <ToolRow key={t.slug} tool={t} grants={grants.filter((g) => g.tool_slug.toLowerCase() === t.slug.toLowerCase())}
                      onMode={(m) => void setMode(t.slug, m)} onAccept={() => void acceptChange(t.slug)} onRevoke={(g) => void revokeGrant(g)} />)}
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
            <div className="seg" role="group" aria-label="Where the server runs">
              {(['stdio', 'http'] as const).map((t) => (
                <button key={t} className={draft.transport === t ? 'on' : ''} aria-pressed={draft.transport === t}
                  onClick={() => { setDraft({ ...draft, transport: t }); setDraftReport(null) }}>
                  {t === 'stdio' ? 'Local' : 'Remote'}
                </button>
              ))}
            </div>
            {draft.transport === 'http' ? (
              <>
                <label><span>URL <small className="muted">(the streamable HTTP endpoint, or paste the server&apos;s config JSON here)</small></span>
                  <input value={draft.url} spellCheck={false} placeholder="https://example.com/mcp"
                    onChange={(e) => setDraft({ ...draft, url: e.target.value })}
                    onPaste={(e) => {
                      const text = e.clipboardData.getData('text')
                      if (text.trim().startsWith('{') && applyPaste(text)) e.preventDefault()
                    }} />
                </label>
                <div className="mcp-headers">
                  <span>Headers <small className="muted">(optional; values are kept in the backend and never shown again)</small></span>
                  {draft.headerRows.map((h, i) => {
                    const set = (patch: Partial<HeaderRow>): void =>
                      setDraft({ ...draft, headerRows: draft.headerRows.map((r, j) => (j === i ? { ...r, ...patch } : r)) })
                    return (
                      <div key={i} className="mcp-header-row">
                        <input aria-label="Header name" value={h.k} placeholder="Authorization" spellCheck={false} onChange={(e) => set({ k: e.target.value })} />
                        <input aria-label="Header value" type="password" value={h.v} placeholder="Bearer …" autoComplete="off" onChange={(e) => set({ v: e.target.value })} />
                        <button className="icon-btn" aria-label="Remove header"
                          onClick={() => setDraft({ ...draft, headerRows: draft.headerRows.filter((_, j) => j !== i) })}><X size={13} /></button>
                      </div>
                    )
                  })}
                  <button className="ghost-btn small" onClick={() => setDraft({ ...draft, headerRows: [...draft.headerRows, { k: '', v: '' }] })}>
                    <Plus size={12} /> Add header
                  </button>
                  <small className="muted">A server that uses a browser sign-in shows a Sign in button once it is added.</small>
                </div>
              </>
            ) : (
              <>
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
              </>
            )}
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
