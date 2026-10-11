import { useEffect, useState } from 'react'
import { CalendarPlus, ExternalLink, X } from '../icons'
import { api } from '../lib/api'
import { useStore } from '../store'
import type { NoticedItem } from '@shared/types'

/** "Wed 6–7 PM" from the stored local wall-clock start and end. */
function when(it: NoticedItem): string {
  const s = new Date(it.start.length === 10 ? `${it.start}T00:00` : it.start)
  if (Number.isNaN(s.getTime())) return it.start
  const day = s.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' })
  if (it.start.length === 10) return day
  const t = (d: Date): string => d.toLocaleTimeString(undefined, { hour: 'numeric', minute: d.getMinutes() ? '2-digit' : undefined })
  const e = it.end ? new Date(it.end) : null
  return `${day}, ${t(s)}${e && !Number.isNaN(e.getTime()) ? `–${t(e)}` : ''}`
}

/**
 * Things Grain noticed in mail on its own. Renders nothing while there are none. Adding to the calendar happens
 * only on the click; the backend creates the event and reads it back before the card goes away.
 */
export default function NoticedGroup(): JSX.Element | null {
  const toast = useStore((s) => s.toast)
  const [items, setItems] = useState<NoticedItem[]>([])
  const [busy, setBusy] = useState<string | null>(null)
  useEffect(() => { api.noticed.list().then((r) => setItems(r.items)).catch(() => undefined) }, [])
  if (items.length === 0) return null
  const drop = (id: string): void => setItems((l) => l.filter((x) => x.id !== id))

  const add = async (it: NoticedItem): Promise<void> => {
    setBusy(it.id)
    try { await api.noticed.accept(it.id); drop(it.id); toast('Added to your calendar') } catch (e) { toast((e as Error).message, 'error') } finally { setBusy(null) }
  }
  const dismiss = async (it: NoticedItem): Promise<void> => {
    drop(it.id)
    try { await api.noticed.dismiss(it.id) } catch (e) { toast((e as Error).message, 'error') }
  }

  return (
    <div className="inbox-group">
      <h5>Noticed <span className="muted small">in your mail, not on your calendar</span></h5>
      <ul className="inbox-list">
        {items.map((it) => (
          <li className="inbox-item" key={it.id}>
            <div className="inbox-row">
              <span className="inbox-job">{it.title}</span>
              <span className="inbox-line" title={it.subject}>{when(it)}{it.location ? `, ${it.location}` : ''}</span>
              <a className="icon-btn sm" href={it.mail_url} target="_blank" rel="noreferrer" title="Open the email" aria-label="Open the email"><ExternalLink size={13} /></a>
              <button className="primary-btn sm" disabled={busy === it.id} onClick={() => void add(it)}><CalendarPlus size={13} /> Add to calendar</button>
              <button className="ghost-btn sm" disabled={busy === it.id} onClick={() => void dismiss(it)}><X size={13} /> Dismiss</button>
            </div>
          </li>
        ))}
      </ul>
    </div>
  )
}
