/** An inline confirmation for a risky change: what it would do, then Cancel or a danger-styled confirm. */
export default function ConfirmDanger({ open, title, consequences, confirmLabel, onConfirm, onCancel }: {
  open: boolean; title: string; consequences: string[]; confirmLabel: string; onConfirm: () => void; onCancel: () => void
}): JSX.Element | null {
  if (!open) return null
  return (
    <div role="alert" className="set-row confirm-danger">
      <p className="small"><b>{title}</b></p>
      {consequences.length > 0 && <ul>{consequences.map((c) => <li key={c}>{c}</li>)}</ul>}
      <div className="confirm-danger-actions">
        <button type="button" className="ghost-btn sm" onClick={onCancel}>Cancel</button>
        <button type="button" className="ghost-btn sm danger" onClick={onConfirm}>{confirmLabel}</button>
      </div>
    </div>
  )
}
