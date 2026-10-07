import { useEffect, useState } from 'react'
import { RefreshCw, ExternalLink, X, ListPlus, ScanSearch } from 'lucide-react'
import { api } from '../lib/api'
import { useStore } from '../store'
import type { MailWatchList as WatchData, MailWatchThread } from '@shared/types'

type Kind = 'to_reply' | 'awaiting_reply'
const LABEL: Record<Kind, string> = { to_reply: 'Needs reply', awaiting_reply: 'Awaiting reply' }

const fromName = (s: string): string => s.replace(/<.*>/, '').replace(/"/g, '').trim() || s
const age = (d: number): string => (d < 1 ? 'today' : d < 2 ? '1 day' : `${Math.floor(d)} days`)

/** What the chips in the toolbar and the list in the page body share. */
export interface MailWatch {
  kind: Kind | null
  setKind: (k: Kind | null) => void
  data: WatchData | null
  busy: boolean
  refresh: () => Promise<void>
  dismiss: (t: MailWatchThread) => Promise<void>
  followup: (t: MailWatchThread) => Promise<void>
}

/**
 * Needs-reply / Awaiting-reply for the Mail page. Read-only toward Gmail: a follow-up todo is made
 * only on click. The chips sit in the page's one toolbar and the threads they open sit in its body,
 * so the state lives here and both halves are handed it.
 */
export function useMailWatch(): MailWatch {
  const toast = useStore((s) => s.toast)
  const [kind, setKind] = useState<Kind | null>(() => useStore.getState().mailWatchKind)
  const [data, setData] = useState<WatchData | null>(null)
  const [busy, setBusy] = useState(false)

  const load = async (): Promise<void> => {
    try { setData(await api.mailWatch.list()) } catch { setData(null) }
  }
  useEffect(() => { void load(); useStore.setState({ mailWatchKind: null }) }, [])

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
  return { kind, setKind, data, busy, refresh, dismiss, followup }
}

/** The two chips and the re-scan button, for the Mail toolbar. */
export function MailWatchChips({ watch }: { watch: MailWatch }): JSX.Element {
  const { kind, setKind, data, busy, refresh } = watch
  const n = (k: Kind): number => (data?.threads ?? []).filter((t) => t.status === k).length
  const chip = (k: Kind): JSX.Element => (
    <button className={`chip-check ${kind === k ? 'on' : ''}`} aria-pressed={kind === k} onClick={() => setKind(kind === k ? null : k)}>{LABEL[k]} {n(k)}</button>
  )
  return (
    <>
      {chip('to_reply')}
      {chip('awaiting_reply')}
      <button className="icon-btn sm" title="Re-scan recent threads for replies" aria-label="Re-scan recent threads" onClick={() => void refresh()} disabled={busy}>
        <ScanSearch size={13} className={busy ? 'spin' : ''} />
      </button>
    </>
  )
}

/** The threads behind the chip that is on. Renders nothing while neither is. */
export function MailWatchList({ watch }: { watch: MailWatch }): JSX.Element | null {
  const { kind, data, dismiss, followup } = watch
  if (!kind) return null
  const rows = (data?.threads ?? []).filter((t) => t.status === kind)
  return (
    <section className="mail-watch">
      <h4 className="section-h">{LABEL[kind]} <span>{rows.length}</span></h4>
      {rows.length === 0 && <p className="empty-hint">{data && data.threads.length === 0 ? 'Nothing scanned yet. Press the re-scan button above.' : 'Nothing here.'}</p>}
      <div className="mail-list">
        {rows.map((t) => (
          <div key={t.thread_id} className="mail-row static">
            <span className="mail-row-lead" aria-hidden />
            <span className="mail-row-from" title={t.last_from}>{fromName(t.last_from)}</span>
            <span className="mail-row-text">
              <span className="mail-row-subject">{t.subject || '(no subject)'}</span>
              <span className="mail-row-snippet"> — {t.reason}</span>
            </span>
            <span className="mail-row-actions">
              <a className="icon-btn ghost sm" href={`https://mail.google.com/mail/u/0/#all/${t.thread_id}`} target="_blank" rel="noreferrer" title="Open thread" aria-label="Open thread"><ExternalLink size={13} /></a>
              {kind === 'awaiting_reply' && !t.followup_todo_id && (
                <button className="icon-btn ghost sm" title="Add follow-up todo" aria-label="Add follow-up todo" onClick={() => void followup(t)}><ListPlus size={13} /></button>
              )}
              <button className="icon-btn ghost sm" title="Dismiss until new activity" aria-label="Dismiss" onClick={() => void dismiss(t)}><X size={13} /></button>
            </span>
            <span className="mail-row-date">{age(t.age_days)}</span>
          </div>
        ))}
      </div>
    </section>
  )
}
