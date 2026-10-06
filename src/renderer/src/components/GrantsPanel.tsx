import { useCallback, useEffect, useState } from 'react'
import { X } from 'lucide-react'
import type { McpGrant, PermissionGrants, PermissionRules, Settings, ToolOverride } from '@shared/types'
import { api } from '../lib/api'
import { useStore } from '../store'
import { MODE_LABEL, normalize } from './ToolPermissions'
import ApprovalHistory from './ApprovalHistory'

/** The grants listing also carries agent tool maps and chats that skip their cards (newer than the shared type). */
type Grants = PermissionGrants & {
  agent_overrides?: { agent_id: string; title: string; tool: string; mode: ToolOverride }[]
  chat_skip?: { conversation_id: string; title: string }[]
}

const without = <T,>(m: Record<string, T> | undefined, k: string): Record<string, T> =>
  Object.fromEntries(Object.entries(m ?? {}).filter(([t]) => t !== k))
const dropAllow = (x: PermissionRules | undefined, r: string): PermissionRules =>
  ({ allow: (x?.allow ?? []).filter((a) => a !== r), ask: x?.ask ?? [], deny: x?.deny ?? [] })

/** Every standing grant with a revoke, plus recent answers: the one place to take any of them back. Everywhere: global
 *  tool modes that differ from the tool's default, saved allow rules and global connector grants; a revoke there saves
 *  at once and patches the modal draft too, so the modal's Save cannot write it back. Chat, agent, project, connector
 *  and session rows list only 'on' overrides, so every revoke narrows: dropping an 'ask' or 'off' override could widen.
 */
export default function GrantsPanel({ draft, patch }: { draft: Settings; patch: (p: Partial<Settings>) => void }): JSX.Element {
  const toast = useStore((s) => s.toast)
  const settings = useStore((s) => s.settings)
  const saveSettings = useStore((s) => s.saveSettings)
  const tools = useStore((s) => s.tools)
  const allProjects = useStore((s) => s.projects)
  const conversations = useStore((s) => s.conversations)
  const [g, setG] = useState<Grants | null>(null)

  const load = useCallback(async (): Promise<void> => {
    setG(await api.permissionGrants())
  }, [])
  useEffect(() => { load().catch((e) => toast((e as Error).message, 'error')) }, [load, toast])

  const act = (fn: () => Promise<unknown>) => (): void => {
    fn().then(load).catch((e) => toast((e as Error).message, 'error'))
  }
  const chatTools = (id: string): Record<string, ToolOverride> =>
    Object.fromEntries((g?.chat_overrides ?? []).filter((o) => o.conversation_id === id).map((o) => [o.tool, o.mode]))
  const projectTools = (id: string): Record<string, ToolOverride> =>
    Object.fromEntries((g?.project_overrides ?? []).filter((o) => o.project_id === id).map((o) => [o.tool, o.mode]))

  const modes = tools.flatMap((t) => {
    const raw = settings.tools?.[t.name]
    const mode = normalize(raw, t.default_mode)
    return raw === undefined || mode === t.default_mode ? [] : [{ name: t.name, mode }]
  })
  const allow = settings.permissionRules?.allow ?? []
  const revokeMode = (name: string): void => {
    void saveSettings({ tools: without(settings.tools, name) })
    patch({ tools: without(draft.tools, name) })
  }
  const revokeRule = (r: string): void => {
    void saveSettings({ permissionRules: dropAllow(settings.permissionRules, r) })
    patch({ permissionRules: dropAllow(draft.permissionRules, r) })
  }
  const revokeBtn = (label: string, onClick: () => void): JSX.Element =>
    <button className="icon-btn" aria-label={`Revoke ${label}`} onClick={onClick}><X size={12} /></button>

  if (!g) return <div className="perm-rules"><h4>Standing grants</h4><p className="muted small">Loading…</p></div>
  const globalMcp: McpGrant[] = g.mcp.filter((m) => m.scope === 'global')
  const chats = g.chat_overrides.filter((o) => o.mode === 'on')
  const projects = g.project_overrides.filter((o) => o.mode === 'on')
  const mcp = g.mcp.filter((m) => m.mode === 'on' && m.scope !== 'global')
  const agents = (g.agent_overrides ?? []).filter((o) => o.mode === 'on')
  const none = !modes.length && !allow.length && !globalMcp.length && !g.session.length && !chats.length && !projects.length && !mcp.length
    && !agents.length

  return (
    <>
    <div className="perm-rules grants-panel">
      <h4>Standing grants</h4>
      <p className="muted small">What you said "always" to, from settings, a chat, an agent, a project, a connector or an approval card. Revoking takes effect now.</p>
      {none && <p className="muted small">None yet.</p>}
      {modes.length + allow.length + globalMcp.length > 0 && (
        <div className="perm-rule-group">
          <b>Everywhere</b>
          <ul>
            {modes.map((m) => (
              <li key={`t:${m.name}`}><span>{m.name.replace(/_/g, ' ')} <small className="muted">{MODE_LABEL[m.mode]}</small></span>{revokeBtn(m.name, () => revokeMode(m.name))}</li>
            ))}
            {allow.map((r) => (
              <li key={`r:${r}`}><span><code>{r}</code> <small className="muted">allowed by rule</small></span>{revokeBtn(r, () => revokeRule(r))}</li>
            ))}
            {globalMcp.map((m) => (
              <li key={m.id}><span><code>{m.tool_slug}</code> <small className="muted">{MODE_LABEL[m.mode] ?? m.mode}</small></span>
                {revokeBtn(m.tool_slug, act(() => api.mcp.clearGrant(m.tool_slug, m.scope, undefined)))}</li>
            ))}
          </ul>
        </div>
      )}
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
      {agents.length > 0 && (
        <div className="perm-rule-group">
          <b>Agents</b> <small className="muted">A tool turned on for one agent.</small>
          <ul>
            {agents.map((o) => (
              <li key={`${o.agent_id}:${o.tool}`}><span><code>{o.tool}</code> <small className="muted">for {o.title}</small></span>
                <button className="icon-btn" aria-label={`Revoke ${o.tool} for ${o.title}`} onClick={act(() => api.revokeAgentGrant(o.agent_id, o.tool))}><X size={12} /></button>
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
    </div>
    <ApprovalHistory />
    </>
  )
}
