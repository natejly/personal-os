import type { ReactNode } from 'react'

/** A labelled on/off row with an optional tooltip. The checkbox is visually hidden; `.switch` is the control. */
export default function Switch({ title, help, checked, onChange, disabled, danger, plain = true, className, label }: {
  title: ReactNode; help?: ReactNode; checked: boolean; onChange: (v: boolean) => void
  disabled?: boolean; danger?: boolean; /** false: the bare `.toggle-row` used inside a `.setting-list`. */ plain?: boolean; className?: string
  /** Accessible name when the visible title is not plain text. */ label?: string
}): JSX.Element {
  return (
    <label className={['toggle-row', plain && 'plain', className, danger && 'danger'].filter(Boolean).join(' ')}>
      <span className="toggle-text"><b title={typeof help === 'string' ? help : undefined}>{title}</b></span>
      <input type="checkbox" aria-label={label} checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} /><span className="switch" />
    </label>
  )
}
