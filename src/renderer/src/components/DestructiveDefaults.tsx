import type { DestructiveAction, DestructiveDefaults as Defaults, DestructiveKind } from '@shared/types'

const ROWS: { kind: DestructiveKind; label: string }[] = [
  { kind: 'shell', label: 'Shell commands that delete or rewrite history' },
  { kind: 'files', label: 'Deleting files outside the workspace' },
  { kind: 'items', label: 'Deleting mail, events and tasks' },
  { kind: 'send', label: 'Sending email and messages' }
]
const ACTIONS: { id: DestructiveAction; label: string }[] = [
  { id: 'block', label: 'Block' },
  { id: 'ask', label: 'Ask' },
  { id: 'allow', label: 'Allow' }
]
export const DEFAULT_DESTRUCTIVE: Defaults = { shell: 'block', files: 'block', items: 'block', send: 'block' }

export function DestructiveDefaults({ value, onChange }: { value?: Partial<Defaults>; onChange: (next: Defaults) => void }): JSX.Element {
  const cur = { ...DEFAULT_DESTRUCTIVE, ...value }
  return (
    <div className="destructive-defaults" role="group" aria-label="Destructive actions in Manual mode">
      <b title="Manual mode only. Block refuses the action outright.">Destructive actions</b>
      {ROWS.map((r) => (
        <div key={r.kind} className="row">
          <span>{r.label}</span>
          <div className="seg" role="radiogroup" aria-label={r.label}>
            {ACTIONS.map((a) => (
              <button key={a.id} type="button" role="radio" aria-checked={cur[r.kind] === a.id} className={cur[r.kind] === a.id ? 'on' : ''}
                onClick={() => { if (cur[r.kind] !== a.id) onChange({ ...cur, [r.kind]: a.id }) }}>{a.label}</button>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}
