import { Globe, FileSearch, Brain, Share2, Terminal, Clock, Wrench, CheckSquare, Mail, Laptop, FolderOpen, ListChecks, Layers, GraduationCap, PenLine, HeartPulse } from 'lucide-react'
import { useState } from 'react'
import { useStore } from '../store'
import { askLocked, type ToolInfo, type ToolMode, type ToolOverride } from '@shared/types'
import { describeCall, humanizeName } from '../lib/toolDisplay'

const GROUP_ICON: Record<string, JSX.Element> = {
  knowledge: <FileSearch size={13} />, memory: <Brain size={13} />, graph: <Share2 size={13} />, web: <Globe size={13} />, code: <Terminal size={13} />,
  utility: <Clock size={13} />, todos: <CheckSquare size={13} />, google: <Mail size={13} />,
  mac: <Laptop size={13} />, files: <FolderOpen size={13} />,
  plan: <ListChecks size={13} />, context: <Layers size={13} />, skills: <GraduationCap size={13} />,
  style: <PenLine size={13} />, health: <HeartPulse size={13} />
}
export const DANGER_LABEL: Record<string, string> = { safe: 'read-only', writes: 'writes in-app data', network: 'reads the internet', executes: 'runs sandboxed code', external: 'acts outside the app', plan: 'always asks: the call is the approval card', schedules: 'books work for later' }
export const MODE_LABEL: Record<ToolMode, string> = { on: 'always on', ask: 'ask each time', off: 'off' }

/** The name a person reads: "Search documents", never the identifier the model calls. */
const toolLabel = (name: string): string => describeCall(name, null).verb

export const normalize = (v: unknown, fallback: ToolMode): ToolMode => (v === true ? 'on' : v === false ? 'off' : v === 'on' || v === 'ask' || v === 'off' ? v : fallback)
const LOCKED_TIP = 'Actions outside the app always ask'
/** A legacy stored 'on' for an ask-locked tool reads as what the backend runs: ask. */
const capped = (t: ToolInfo, m: ToolMode): ToolMode => (m === 'on' && askLocked(t) ? 'ask' : m)

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
        const locked = askLocked(t)
        const raw = value[t.name] ?? 'inherit'
        const ov: ToolOverride = raw === 'on' && locked ? 'ask' : raw
        const base = capped(t, effectiveBase[t.name] ?? t.default_mode)
        const eff: ToolMode = ov === 'inherit' ? base : ov
        return (
          <div key={t.name} className={`tool-perm ${eff === 'off' ? 'off' : ''} ${!t.available ? 'unavailable' : ''}`} title={t.description + (t.available ? '' : ' (integration not connected)')}>
            <span className="tool-icon">{GROUP_ICON[t.group] ?? <Wrench size={13} />}</span>
            <span className="tool-perm-name">{toolLabel(t.name)}<small>{DANGER_LABEL[t.danger]}</small></span>
            {eff === 'ask' && <span className="tag ask">asks</span>}
            <select title={locked ? LOCKED_TIP : undefined} aria-label={`Permission for ${toolLabel(t.name)}`} value={ov} onChange={(e) => onChange({ ...value, [t.name]: e.target.value as ToolOverride })}>
              <option value="inherit">inherit ({MODE_LABEL[base]})</option>
              {!locked && <option value="on">always on</option>}
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
  const [q, setQ] = useState('')
  const needle = q.trim().toLowerCase()
  const shown = needle ? tools.filter((t) => `${toolLabel(t.name)} ${t.name.replace(/_/g, ' ')} ${t.group} ${t.description}`.toLowerCase().includes(needle)) : tools
  const groups = [...new Set(shown.map((t) => t.group))]
  const current = (name: string, fallback: ToolMode): ToolMode => normalize(value[name], fallback)
  const set = (name: string, mode: ToolMode): void => onChange({ ...Object.fromEntries(Object.entries(value).map(([k, v]) => [k, normalize(v, 'on')])), [name]: mode })
  // Groups start collapsed and open while filtering; the full model-facing description is the row's tooltip.
  return (
    <div className="tool-perms">
      <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter tools" aria-label="Filter tools" spellCheck={false} />
      {groups.map((g) => {
        const rows = shown.filter((t) => t.group === g)
        return (
          <details key={g} className="tool-group" open={needle ? true : undefined}>
            <summary><h5>{GROUP_ICON[g] ?? <Wrench size={13} />} {humanizeName(g)} <small className="muted">{rows.length}</small></h5></summary>
            {rows.map((t) => {
              const locked = askLocked(t)
              const mode = capped(t, current(t.name, t.default_mode))
              const label = toolLabel(t.name)
              return (
                <div key={t.name} className={`tool-perm row ${mode === 'off' ? 'off' : ''} ${!t.available ? 'unavailable' : ''}`}>
                  {/* The description is written for the model and runs long: two lines here, all of it on hover. */}
                  <span className="toggle-text"><b>{label} <small className="muted">{DANGER_LABEL[t.danger]}</small></b><small className="clamp-2" title={t.description}>{t.description}</small></span>
                  <div className="seg" role="group" aria-label={`Permission for ${label}`} title={locked ? LOCKED_TIP : undefined}>
                    {((locked ? ['ask', 'off'] : ['on', 'ask', 'off']) as ToolMode[]).map((m) => <button key={m} type="button" className={mode === m ? 'on' : ''} aria-pressed={mode === m} onClick={() => set(t.name, m)}>{m}</button>)}
                  </div>
                </div>
              )
            })}
          </details>
        )
      })}
      {needle && groups.length === 0 && <p className="muted small">No tool matches “{q.trim()}”.</p>}
    </div>
  )
}

