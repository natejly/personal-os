/** A thin done/total bar, shared by the progress card and the side panel's checklist headers. */
export default function ProgressBar({ done, total, label = 'Progress' }: { done: number; total: number; label?: string }): JSX.Element {
  const pct = total > 0 ? Math.round((done / total) * 100) : 0
  return (
    <div className={`progress-bar${total > 0 && done >= total ? ' complete' : ''}`} role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={total} aria-valuenow={done}>
      <span style={{ width: `${pct}%` }} />
    </div>
  )
}
