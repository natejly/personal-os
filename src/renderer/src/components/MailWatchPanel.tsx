import { useEffect, useState } from 'react'
import { RefreshCw, ExternalLink, X, ListPlus } from 'lucide-react'
import { api } from '../lib/api'
import { useStore } from '../store'
import type { MailWatchList, MailWatchThread } from '@shared/types'

type Kind = 'to_reply' | 'awaiting_reply'

const fromName = (s: string): string => s.replace(/<.*>/, '').replace(/"/g, '').trim() || s
const age = (d: number): string => (d < 1 ? 'today' : d < 2 ? '1 day' : `${Math.floor(d)} days`)

/** Needs-reply / Awaiting-reply chips for the Mail page. Read-only toward Gmail: a follow-up todo is made only on click. */
export default function MailWatchPanel(): JSX.Element {
  const toast = useStore((s) => s.toast)
  const [kind, setKind] = useState<Kind | null>(null)
  const [data, setData] = useState<MailWatchList | null>(null)
  const [busy, setBusy] = useState(false)

  const load = async (): Promise<void> => {
    try { setData(await api.mailWatch.list()) } catch { setData(null) }
  }
  useEffect(() => { void load() }, [])

  const refresh = async (): Promise<void> => {
    setBusy(true)
    try { await api.mailWatch.refresh(); await load() } catch (e) { toast((e as Error).message, 'error') } finally { setBusy(false) }
  }
  const dismiss = async (t: MailWatchThread): Promise<void> => {
    try { await api.mailWatch.dismiss(t.thread_id); await load() } catch (e) { toast((e as Error).message, 'error') }
  }
  const followup = async (t: MailWatchThread): Promise<void> => {
    try { await api.mailWatch.followup(t.thread_id); toast('Follow-up todo added'); await load() } catch (e) { toast((e as Error).message, 'error') }
  }

  const rows = (data?.threads ?? []).filter((t) => t.status === kind)
  const n = (k: Kind): number => (data?.threads ?? []).filter((t) => t.status === k).length
  const chip = (k: Kind, label: string): JSX.Element => (
    <button className={`chip-check ${kind === k ? 'on' : ''}`} onClick={() => setKind(kind === k ? null : k)}>{label} {n(k)}</button>
  )
  return (
    <div className="mail-watch no-drag">
      <div className="mail-toolbar">
        {chip('to_reply', 'Needs reply')}
        {chip('awaiting_reply', 'Awaiting reply')}
        <button className="icon-btn" title="Re-scan recent threads" aria-label="Re-scan recent threads" onClick={() => void refresh()} disabled={busy}>
          <RefreshCw size={13} className={busy ? 'spin' : ''} />
        </button>
      </div>
      {kind && (
        <div className="mail-list">
          {rows.length === 0 && <div className="empty-hint"><p>{data && data.threads.length === 0 ? 'Nothing scanned yet. Press the refresh button.' : 'Nothing here.'}</p></div>}
          {rows.map((t) => (
            <div key={t.thread_id} className="mail-row">
              <span className="mail-from">{fromName(t.last_from)}</span>
              <span className="mail-subject">{t.subject || '(no subject)'} <span className="muted">· {t.reason}</span></span>
              <span className="mail-date">{age(t.age_days)}</span>
              <a className="icon-btn ghost" href={`https://mail.google.com/mail/u/0/#all/${t.thread_id}`} target="_blank" rel="noreferrer" title="Open thread" aria-label="Open thread"><ExternalLink size={13} /></a>
              {kind === 'awaiting_reply' && !t.followup_todo_id && (
                <button className="icon-btn ghost" title="Add follow-up todo" aria-label="Add follow-up todo" onClick={() => void followup(t)}><ListPlus size={13} /></button>
              )}
              <button className="icon-btn ghost" title="Dismiss until new activity" aria-label="Dismiss" onClick={() => void dismiss(t)}><X size={13} /></button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
