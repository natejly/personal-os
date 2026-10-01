import { useEffect } from 'react'
import { Users } from 'lucide-react'
import { useStore } from '../store'
import { homeModuleOn } from '../modules'
import '../styles/cowork.css'

/**
 * The Today card: the unseen `needs_you` rows, which is how a desk that finished while the app was
 * closed is still visible without a poller. Clicking a row opens that desk and marks the row seen.
 */

const ago = (ts: number): string => {
  const s = Date.now() / 1000 - ts
  return s < 60 ? 'just now' : s < 3600 ? `${Math.round(s / 60)}m ago` : s < 86400 ? `${Math.round(s / 3600)}h ago` : `${Math.round(s / 86400)}d ago`
}

export default function HomeCowork(): JSX.Element | null {
  const settings = useStore((s) => s.settings)
  const inbox = useStore((s) => s.deskInbox)
  const { refreshDeskInbox, setView, openDesk, markDeskEventSeen } = useStore()

  useEffect(() => { void refreshDeskInbox() }, [refreshDeskInbox])

  if (!homeModuleOn(settings, 'cowork')) return null

  const open = (eventId: string, deskId: string): void => {
    void markDeskEventSeen(eventId)
    setView('cowork')
    void openDesk(deskId)
  }

  return (
    <section className="widget">
      <header>
        <Users size={14} /> Cowork
        {inbox.length > 0 && <span className="muted small">{inbox.length} waiting on you</span>}
        <button className="link small" onClick={() => setView('cowork')}>all</button>
      </header>
      {inbox.length === 0 ? (
        <p className="muted">No desk needs you.</p>
      ) : (
        <ul className="home-desks">
          {inbox.slice(0, 8).map((e) => (
            <li
              key={e.id}
              role="button"
              tabIndex={0}
              onClick={() => open(e.id, e.desk_id)}
              onKeyDown={(k) => { if (k.key === 'Enter' || k.key === ' ') { k.preventDefault(); open(e.id, e.desk_id) } }}
            >
              <span className="home-desk-title">{e.desk_title || 'Desk'}</span>
              <span className="home-desk-body">{e.body || e.kind}</span>
              <span className="home-desk-when">{ago(e.created_at)}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
