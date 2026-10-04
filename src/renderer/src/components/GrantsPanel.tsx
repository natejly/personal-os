import { useCallback, useEffect, useState } from 'react'
import { X } from 'lucide-react'
import type { PendingApproval, PermissionGrants, ToolOverride } from '@shared/types'
import { api } from '../lib/api'
import { useStore } from '../store'

const when = (t: number | null): string => (t ? new Date(t * 1000).toLocaleString() : '')

/** What runs without asking, outside the global toggles and rules above: each grant with a revoke, plus recent answers.
 *  Only 'on' overrides are listed, so every revoke narrows: dropping an 'ask' or 'off' override could widen. */
export default function GrantsPanel(): JSX.Element {
  const toast = useStore((s) => s.toast)
  const allProjects = useStore((s) => s.projects)
  const conversations = useStore((s) => s.conversations)
  const [g, setG] = useState<PermissionGrants | null>(null)
  const [history, setHistory] = useState<PendingApproval[]>([])

  const load = useCallback(async (): Promise<void> => {
    const [grants, decided] = await Promise.all([api.permissionGrants(), api.decidedApprovals(50)])
    setG(grants)
    setHistory(decided)
  }, [])
  useEffect(() => { load().catch((e) => toast((e as Error).message, 'error')) }, [load, toast])

  const act = (fn: () => Promise<unknown>) => (): void => {
    fn().then(load).catch((e) => toast((e as Error).message, 'error'))
  }
  const chatTools = (id: string): Record<string, ToolOverride> =>
    Object.fromEntries((g?.chat_overrides ?? []).filter((o) => o.conversation_id === id).map((o) => [o.tool, o.mode]))
  const projectTools = (id: string): Record<string, ToolOverride> =>
    Object.fromEntries((g?.project_overrides ?? []).filter((o) => o.project_id === id).map((o) => [o.tool, o.mode]))
  const without = (m: Record<string, ToolOverride>, k: string): Record<string, ToolOverride> =>
    Object.fromEntries(Object.entries(m).filter(([t]) => t !== k))

  if (!g) return <div className="perm-rules"><h5>Granted</h5><p className="muted small">Loading…</p></div>
  const chats = g.chat_overrides.filter((o) => o.mode === 'on')
  const projects = g.project_overrides.filter((o) => o.mode === 'on')
  const mcp = g.mcp.filter((m) => m.mode === 'on' && m.scope !== 'global')
  const none = !g.session.length && !chats.length && !projects.length && !mcp.length

  return (
    <div className="perm-rules grants-panel">
      <h5>Granted</h5>
      <p className="muted small">Standing approvals given from a chat, a project or an approval card. Revoking one makes that call ask again.</p>
      {none && <p className="muted small">Nothing granted beyond the settings above.</p>}
      {g.session.length > 0 && (
        <div className="perm-rule-group">
          <b>This session</b> <small className="muted">Allowed for one chat until the app restarts.</small>
          <ul>
            {g.session.flatMap((s) => s.keys.map((k) => (
              <li key={`${s.conversation_id}:${k}`}><span><code>{k}</code> <small className="muted">in {s.title || s.conversation_id}</small></span>
                <button className="icon-btn" aria-label={`Revoke ${k}`} onClick={act(() => api.revokeSessionGrant(s.conversation_id, k))}><X size={12} /></button>
              </li>
            )))}
          </ul>
        </div>
      )}
      {chats.length > 0 && (
        <div className="perm-rule-group">
          <b>Chats</b> <small className="muted">A tool turned on for one chat.</small>
          <ul>
            {chats.map((o) => (
              <li key={`${o.conversation_id}:${o.tool}`}><span><code>{o.tool}</code> <small className="muted">in {o.title}</small></span>
                <button className="icon-btn" aria-label={`Revoke ${o.tool} in ${o.title}`}
                  onClick={act(() => api.conversations.patch(o.conversation_id, { settings: { tools: without(chatTools(o.conversation_id), o.tool) } }))}><X size={12} /></button>
              </li>
            ))}
          </ul>
        </div>
      )}
      {projects.length > 0 && (
        <div className="perm-rule-group">
          <b>Projects</b> <small className="muted">A tool turned on for every chat in a project.</small>
          <ul>
            {projects.map((o) => (
              <li key={`${o.project_id}:${o.tool}`}><span><code>{o.tool}</code> <small className="muted">in {o.title}</small></span>
                <button className="icon-btn" aria-label={`Revoke ${o.tool} in ${o.title}`}
                  onClick={act(() => api.projects.update(o.project_id, { tools: without(projectTools(o.project_id), o.tool) }))}><X size={12} /></button>
              </li>
            ))}
          </ul>
        </div>
      )}
      {mcp.length > 0 && (
        <div className="perm-rule-group">
          <b>Connectors</b> <small className="muted">A connector tool allowed for one chat or project.</small>
          <ul>
            {mcp.map((m) => (
              <li key={m.id}><span><code>{m.tool_slug}</code> <small className="muted">in {(m.scope === 'project' ? allProjects.find((p) => p.id === m.scope_id)?.name : conversations.find((c) => c.id === m.scope_id)?.title) || m.scope_id}</small></span>
                <button className="icon-btn" aria-label={`Revoke ${m.tool_slug}`} onClick={act(() => api.mcp.clearGrant(m.tool_slug, m.scope, m.scope_id))}><X size={12} /></button>
              </li>
            ))}
          </ul>
        </div>
      )}
      <div className="perm-rule-group">
        <b>Recent decisions</b>
        {history.length === 0 ? <p className="muted small">No approvals answered yet.</p> : (
          <ul>
            {history.map((a) => (
              <li key={a.call_id}>
                <span><code>{a.tool}</code> <b>{a.status === 'denied' ? 'denied' : 'allowed'}</b>
                  {a.decided_by && a.decided_by !== 'user' ? ` by ${a.decided_by}` : ''}
                  {a.conversation_title ? <small className="muted"> in {a.conversation_title}</small> : null}
                  {a.note ? <small className="muted"> “{a.note}”</small> : null}</span>
                <small className="muted">{when(a.decided_at)}</small>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
