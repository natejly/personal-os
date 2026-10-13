import type { DestructiveAction, DestructiveDefaults as Defaults, DestructiveKind } from '@shared/types'

const ROWS: { kind: DestructiveKind; label: string; note?: string }[] = [
  { kind: 'shell', label: 'Shell commands that delete or rewrite history' },
  { kind: 'files', label: 'Deleting files outside the workspace' },
  { kind: 'items', label: 'Deleting mail, events and tasks' },
  { kind: 'send', label: 'Sending email and messages', note: 'Block means the assistant cannot send mail in Manual mode.' }
]
const ACTIONS: { id: DestructiveAction; label: string }[] = [
  { id: 'block', label: 'Block' },
  { id: 'ask', label: 'Ask' },
  { id: 'allow', label: 'Allow' }
]
export const DEFAULT_DESTRUCTIVE: Defaults = { shell: 'block', files: 'block', items: 'block', send: 'block' }

/** What Manual mode does with each kind of destructive action: refuse it, ask first, or leave it to the usual rules. */
export function DestructiveDefaults({ value, onChange }: { value?: Partial<Defaults>; onChange: (next: Defaults) => Promise<void> }): JSX.Element {
  const cur = { ...DEFAULT_DESTRUCTIVE, ...value }
  return (
    <div className="destructive-defaults" role="group" aria-label="Destructive actions in Manual mode">
      <b>Destructive actions</b>
      <small className="muted">Manual mode only. Block refuses the action and tells the assistant not to work around it.</small>
      {ROWS.map((r) => (
        <div key={r.kind} className="row">
          <span>{r.label}{r.note && <small className="muted"> {r.note}</small>}</span>
          <div className="seg" role="radiogroup" aria-label={r.label}>
            {ACTIONS.map((a) => (
              <button key={a.id} type="button" role="radio" aria-checked={cur[r.kind] === a.id} className={cur[r.kind] === a.id ? 'on' : ''}
                onClick={() => { if (cur[r.kind] !== a.id) void onChange({ ...cur, [r.kind]: a.id }) }}>{a.label}</button>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}
