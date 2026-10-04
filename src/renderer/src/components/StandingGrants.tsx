import { useEffect, useState } from 'react'
import type { PermissionRules, Settings } from '@shared/types'
import { api } from '../lib/api'
import { useStore } from '../store'
import { MODE_LABEL, normalize } from './ToolPermissions'

type McpGrant = Awaited<ReturnType<typeof api.mcp.tools>>['grants'][number]

const without = <T,>(m: Record<string, T> | undefined, k: string): Record<string, T> =>
  Object.fromEntries(Object.entries(m ?? {}).filter(([key]) => key !== k))
const dropAllow = (x: PermissionRules | undefined, r: string): PermissionRules =>
  ({ allow: (x?.allow ?? []).filter((a) => a !== r), ask: x?.ask ?? [], deny: x?.deny ?? [] })

/** Every persisted grant in one list, each revocable: global tool modes that differ from the tool's default, saved
 *  allow rules, and MCP grants in every scope. A revoke saves at once and patches the modal draft too, so the
 *  modal's Save cannot write it back. Per-chat built-in overrides live in the chat's context drawer. */
export default function StandingGrants({ draft, patch }: { draft: Settings; patch: (p: Partial<Settings>) => void }): JSX.Element {
  const settings = useStore((s) => s.settings)
  const saveSettings = useStore((s) => s.saveSettings)
  const tools = useStore((s) => s.tools)
  const projects = useStore((s) => s.projects)
  const conversations = useStore((s) => s.conversations)
  const [mcp, setMcp] = useState<McpGrant[]>([])
  const loadMcp = (): void => { void api.mcp.tools().then((r) => setMcp(r.grants ?? [])).catch(() => undefined) }
  useEffect(loadMcp, [])

  const modes = tools.flatMap((t) => {
    const raw = settings.tools?.[t.name]
    const mode = normalize(raw, t.default_mode)
    return raw === undefined || mode === t.default_mode ? [] : [{ name: t.name, mode }]
  })
  const allow = settings.permissionRules?.allow ?? []
  const where = (g: McpGrant): string =>
    g.scope === 'global' ? 'everywhere'
      : g.scope === 'project' ? `project ${projects.find((p) => p.id === g.scope_id)?.name ?? g.scope_id}`
        : `chat ${conversations.find((c) => c.id === g.scope_id)?.title || g.scope_id}`

  const revokeMode = (name: string): void => {
    void saveSettings({ tools: without(settings.tools, name) })
    patch({ tools: without(draft.tools, name) })
  }
  const revokeRule = (r: string): void => {
    void saveSettings({ permissionRules: dropAllow(settings.permissionRules, r) })
    patch({ permissionRules: dropAllow(draft.permissionRules, r) })
  }
  const revokeMcp = (g: McpGrant): void => {
    void api.mcp.clearGrant(g.tool_slug, g.scope, g.scope_id || undefined).then(loadMcp).catch(() => undefined)
  }
  const row = (key: string, label: JSX.Element, revoke: () => void): JSX.Element => (
    <li key={key}>{label}<button className="ghost-btn" onClick={revoke}>Revoke</button></li>
  )

  const empty = modes.length + allow.length + mcp.length === 0
  return (
    <div className="perm-rules">
      <div className="perm-rule-group">
        <h5>Standing grants</h5>
        <small className="muted">What you said "always" to. Revoking takes effect now.</small>
        {empty ? <p className="muted small">None yet.</p> : (
          <ul>
            {modes.map((m) => row(`t:${m.name}`, <span>{m.name.replace(/_/g, ' ')} <small className="muted">{MODE_LABEL[m.mode]}, everywhere</small></span>, () => revokeMode(m.name)))}
            {allow.map((r) => row(`r:${r}`, <span><code>{r}</code> <small className="muted">allowed by rule</small></span>, () => revokeRule(r)))}
            {mcp.map((g) => row(`m:${g.tool_slug}:${g.scope}:${g.scope_id}`, <span>{g.tool_slug} <small className="muted">{MODE_LABEL[g.mode] ?? g.mode}, {where(g)}</small></span>, () => revokeMcp(g)))}
          </ul>
        )}
      </div>
    </div>
  )
}
