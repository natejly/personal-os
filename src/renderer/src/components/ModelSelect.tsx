import { useStore } from '../store'
import { selectView } from '../lib/modelSelect'
import type { PickKind } from '../lib/providerModels'

/** One model setting: a list of every model from every provider with a key, grouped by provider. Never free text. */
export default function ModelSelect({ kind, value, onChange, blank, disabled, label }: {
  kind: PickKind; value: string; onChange: (v: string) => void
  /** The empty option's label; '' leaves no empty option (a placeholder shows while nothing is chosen). */
  blank: string; disabled?: boolean; label: string
}): JSX.Element {
  const models = useStore((s) => s.models)
  const providers = useStore((s) => s.settings.providers)
  const view = selectView(models, kind, value, providers ?? [])
  const several = view.groups.length > 1
  const rows = (g: (typeof view.groups)[number]): JSX.Element[] => g.rows.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)
  return (
    <select aria-label={label} value={view.value} disabled={disabled} onChange={(e) => onChange(e.target.value)}>
      {blank ? <option value="">{blank}</option> : !view.value && <option value="" disabled>Choose a model</option>}
      {view.stale && <option value={view.stale.value} disabled>{view.stale.label}</option>}
      {view.groups.map((g) => several ? <optgroup key={g.provider} label={g.name}>{rows(g)}</optgroup> : rows(g))}
    </select>
  )
}
