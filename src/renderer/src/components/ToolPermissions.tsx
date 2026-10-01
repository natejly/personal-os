import { Globe, FileSearch, Brain, Share2, Terminal, Clock, Wrench, CheckSquare, KanbanSquare, Mail, Laptop, FolderOpen } from 'lucide-react'
import { useStore } from '../store'
import type { ToolMode, ToolOverride } from '@shared/types'

const GROUP_ICON: Record<string, JSX.Element> = {
  knowledge: <FileSearch size={13} />, memory: <Brain size={13} />, graph: <Share2 size={13} />, web: <Globe size={13} />, code: <Terminal size={13} />,
  utility: <Clock size={13} />, todos: <CheckSquare size={13} />, boards: <KanbanSquare size={13} />, google: <Mail size={13} />,
  mac: <Laptop size={13} />, files: <FolderOpen size={13} />
}
export const DANGER_LABEL: Record<string, string> = { safe: 'read-only', writes: 'writes in-app data', network: 'reads the internet', executes: 'runs sandboxed code', external: 'acts outside the app', plan: 'always asks: the call is the approval card' }
const MODE_LABEL: Record<ToolMode, string> = { on: 'always on', ask: 'ask each time', off: 'off' }

const normalize = (v: unknown, fallback: ToolMode): ToolMode => (v === true ? 'on' : v === false ? 'off' : v === 'on' || v === 'ask' || v === 'off' ? v : fallback)

/** Tri-state overrides (inherit / on / ask / off) for a project or a chat. `effectiveBase` is what "inherit" resolves to. */
export function ToolOverrides({ value, onChange, effectiveBase, compact = false }: {
  value: Record<string, ToolOverride>
  onChange: (next: Record<string, ToolOverride>) => void
  effectiveBase: Record<string, ToolMode>
  compact?: boolean
}): JSX.Element {
  const tools = useStore((s) => s.tools)
  return (
    <div className={`tool-perms ${compact ? 'compact' : ''}`}>
      {tools.map((t) => {
        const ov = value[t.name] ?? 'inherit'
        const base = effectiveBase[t.name] ?? t.default_mode
        const eff: ToolMode = ov === 'inherit' ? base : ov
        return (
          <div key={t.name} className={`tool-perm ${eff === 'off' ? 'off' : ''} ${!t.available ? 'unavailable' : ''}`} title={t.description + (t.available ? '' : ' (integration not connected)')}>
            <span className="tool-icon">{GROUP_ICON[t.group] ?? <Wrench size={13} />}</span>
            <span className="tool-perm-name">{t.name.replace(/_/g, ' ')}<small>{DANGER_LABEL[t.danger]}</small></span>
            {eff === 'ask' && <span className="tag ask">asks</span>}
            <select aria-label={`Permission for ${t.name.replace(/_/g, ' ')}`} value={ov} onChange={(e) => onChange({ ...value, [t.name]: e.target.value as ToolOverride })}>
              <option value="inherit">inherit ({MODE_LABEL[base]})</option>
              <option value="on">always on</option>
              <option value="ask">ask each time</option>
              <option value="off">off</option>
            </select>
          </div>
        )
      })}
    </div>
  )
}

/** Global modes (Settings). Missing = the tool's default (external actions ask; everything else on). */
export function ToolGlobalToggles({ value, onChange }: { value: Record<string, ToolMode | boolean>; onChange: (next: Record<string, ToolMode>) => void }): JSX.Element {
  const tools = useStore((s) => s.tools)
  const groups = [...new Set(tools.map((t) => t.group))]
  const current = (name: string, fallback: ToolMode): ToolMode => normalize(value[name], fallback)
  const set = (name: string, mode: ToolMode): void => onChange({ ...Object.fromEntries(Object.entries(value).map(([k, v]) => [k, normalize(v, 'on')])), [name]: mode })
  return (
    <div className="tool-perms">
      {groups.map((g) => (
        <div key={g} className="tool-group">
          <h5>{GROUP_ICON[g]} {g}</h5>
          {tools.filter((t) => t.group === g).map((t) => {
            const mode = current(t.name, t.default_mode)
            return (
              <div key={t.name} className={`tool-perm row ${mode === 'off' ? 'off' : ''} ${!t.available ? 'unavailable' : ''}`}>
                <span className="toggle-text"><b>{t.name.replace(/_/g, ' ')} <small className="muted">{DANGER_LABEL[t.danger]}</small></b><small>{t.description}</small></span>
                <div className="seg">
                  {(['on', 'ask', 'off'] as ToolMode[]).map((m) => <button key={m} className={mode === m ? 'on' : ''} onClick={() => set(t.name, m)}>{m}</button>)}
                </div>
              </div>
            )
          })}
        </div>
      ))}
    </div>
  )
}
