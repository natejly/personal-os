import { HeartPulse } from 'lucide-react'
import type { TodayDashboard } from '@shared/types'
import { useStore } from '../../store'
import { fmt, meets } from './format'

/** The Today card for health: each shown metric with today's value. HomeView gates it on `homeWidgets.health`. */
export default function HealthCard({ data: d }: { data: TodayDashboard | null }): JSX.Element {
  const setView = useStore((s) => s.setView)
  const rows = d?.health ?? []
  const logged = rows.filter((m) => m.today != null).length
  return (
    <section className="widget">
      <header><HeartPulse size={14} /> Health <span className="muted small">{logged} of {rows.length} logged today</span><button className="link small" onClick={() => setView('health')}>View all</button></header>
      {!d ? <p className="muted">Loading…</p> : rows.length === 0 ? <p className="muted">No metrics shown.</p> : logged === 0 ? <p className="muted">Nothing logged yet today.</p> : (
        <ul className="hl-card">
          {rows.filter((m) => m.today != null).map((m) => {
            const ok = meets(m, m.today)
            return (
              <li key={m.key}>
                <span className="hl-card-label">{m.label}</span>
                <span className={`hl-card-val ${m.today == null ? 'none' : ''}`}>
                  {fmt(m, m.today)}
                  {ok && <span className="hl-card-met" aria-label="goal met" title="Goal met">✓</span>}
                </span>
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}
