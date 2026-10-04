import { useEffect, useState } from 'react'
import { CalendarClock } from 'lucide-react'
import { api } from '../lib/api'
import { useStore } from '../store'
import { blockKey, blockWhen, pickedBlocks } from '../lib/todayCards'
import type { PlannerBlock, PlannerSuggestion } from '@shared/types'

const when = blockWhen
const key = blockKey
const why = (b: PlannerBlock): string =>
  b.why ? `Score ${b.score.toFixed(2)}: due ${b.why.due.toFixed(2)}, priority ${b.why.priority.toFixed(2)}, energy ${b.why.energy.toFixed(2)}, time of day ${b.why.time.toFixed(2)}` : `Score ${b.score.toFixed(2)}`

type Plan = Pick<PlannerSuggestion, 'blocks' | 'unplaced'>
const fromBlocks = (blocks?: PlannerBlock[]): Plan | null => (blocks?.length ? { blocks, unplaced: [] } : null)

/**
 * "Plan my day" (or week, with `days`): proposes calendar blocks for todos with estimates. Nothing reaches
 * Google until "Add selected". `initial` shows blocks already proposed (Today's dashboard) without a call.
 */
export default function PlannerPanel({ initial, days, onApplied }: { initial?: PlannerBlock[]; days?: number; onApplied?: () => void }): JSX.Element {
  const toast = useStore((s) => s.toast)
  const google = useStore((s) => s.google)
  const [plan, setPlan] = useState<Plan | null>(() => fromBlocks(initial))
  const [picked, setPicked] = useState<Set<string>>(() => new Set((initial ?? []).map(key)))
  const [busy, setBusy] = useState(false)
  useEffect(() => { setPlan(fromBlocks(initial)); setPicked(new Set((initial ?? []).map(key))) }, [initial])

  if (!google?.connected) return <></>

  const suggest = async (): Promise<void> => {
    setBusy(true)
    try {
      const p = await api.planner.suggest(days)
      setPlan(p)
      setPicked(new Set(p.blocks.map(key)))
    } catch (e) {
      toast((e as Error).message, 'error')
    } finally { setBusy(false) }
  }
  const apply = async (): Promise<void> => {
    if (!plan) return
    setBusy(true)
    try {
      const { results } = await api.planner.apply(pickedBlocks(plan.blocks, picked))
      const failed = results.filter((r) => !r.ok)
      if (failed.length) toast(`${results.length - failed.length} added, ${failed.length} failed: ${failed[0].error}`, 'error')
      else toast(`${results.length} block${results.length === 1 ? '' : 's'} added to your calendar`)
      setPlan(null)
      onApplied?.()
    } catch (e) {
      toast((e as Error).message, 'error')
    } finally { setBusy(false) }
  }

  return (
    <div className="planner-panel">
      {!plan && <button className="ghost-btn" onClick={() => void suggest()} disabled={busy}><CalendarClock size={14} /> {busy ? 'Planning…' : (days ?? 1) > 1 ? 'Plan my week' : 'Plan my day'}</button>}
      {plan && (
        <section className="todo-section">
          <h4 className="section-h">Proposed blocks <span>{plan.blocks.length}</span></h4>
          {plan.blocks.length === 0 && <p className="empty-hint">Nothing to place. Give todos a due date or an estimate in minutes.</p>}
          {plan.blocks.map((b) => (
            <label key={key(b)} className="planner-row" title={why(b)}>
              <input type="checkbox" checked={picked.has(key(b))} onChange={() => setPicked((p) => { const n = new Set(p); n.has(key(b)) ? n.delete(key(b)) : n.add(key(b)); return n })} />
              <span>{b.title}{b.part[1] > 1 ? ` (part ${b.part[0]}/${b.part[1]})` : ''}</span>
              <span className="planner-when">{when(b)}</span>
            </label>
          ))}
          {plan.unplaced.length > 0 && <p className="empty-hint">{plan.unplaced.length} could not fit before their due date.</p>}
          <div className="planner-row">
            <button className="primary-btn" onClick={() => void apply()} disabled={busy || picked.size === 0}>Add selected to calendar</button>
            <button className="ghost-btn" onClick={() => setPlan(null)} disabled={busy}>Dismiss</button>
          </div>
        </section>
      )}
    </div>
  )
}
