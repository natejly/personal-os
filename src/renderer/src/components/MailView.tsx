import { useEffect, useMemo, useRef, useState, type Dispatch, type SetStateAction } from 'react'
import { Mail as MailIcon, RefreshCw, Search, Star, Archive, MailOpen, Mail, ExternalLink, MessageSquare, Paperclip, SquarePen, Reply, Sparkles, Send, X } from 'lucide-react'
import { useStore } from '../store'
import { PIM_SETTINGS_TAB, pimLabel, pimProvider, pimStatus } from '../lib/pim'
import { api } from '../lib/api'
import SmartTextarea from './SmartTextarea'
import type { GmailFullMessage, GmailLabel, GmailMessage } from '@shared/types'
import { oneLine } from '../lib/emailAsk'
import { fenced, lines, usePageContext } from '../lib/pageContext'
import { readView, writeView } from '../lib/viewCache'
import AppSwitcher from './AppSwitcher'
import { MailWatchChips, MailWatchList, useMailWatch } from './MailWatchPanel'
import { rowButton } from '../lib/rowButton'
import { useModal } from '../lib/useModal'
import { shortDate, shortDateTime } from '../lib/dates'
import SidebarToggle from './SidebarToggle'

const fromName = (s: string | null): string => (s ?? '').replace(/<.*>/, '').replace(/"/g, '').trim() || (s ?? '')
const fmtDate = (s: string | null): string => {
  if (!s) return ''
  const d = new Date(s)
  if (isNaN(d.getTime())) return s
  const today = new Date()
  return d.toDateString() === today.toDateString()
    ? d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
    : shortDate(d, today)
}
/** Gmail's q syntax matches labels by name with spaces written as hyphens. */
const labelQ = (name: string): string => `label:${name.replace(/\s+/g, '-')}`

type ReadFilter = 'all' | 'unread'

interface Compose {
  to: string
  subject: string
  body: string
  replyTo: GmailMessage | null
  /** Body of the message being replied to; context for the AI review and ghost text. */
  replyBody: string
}
interface Review {
  feedback: string[]
  revised: string
}
const RANGES = [
  { value: '', label: 'Any time' },
  { value: '1d', label: 'Past day' },
  { value: '7d', label: 'Past week' },
  { value: '30d', label: 'Past month' }
]

const isStarred = (m: GmailMessage): boolean => m.labels.includes('STARRED')

/** The open message. Its own component so the dialog behaviour mounts and unmounts with it. */
function MailReader({ message: m, full, onClose, onReply, onStar, onArchive, onSnooze, onUnread, onAsk }: {
  message: GmailMessage
  full: GmailFullMessage | null
  onClose: () => void
  onReply: () => void
  onStar: () => void
  onArchive: () => void
  onSnooze: () => void
  onUnread: () => void
  onAsk: () => void
}): JSX.Element {
  const { titleId, backdrop, modal } = useModal(onClose)
  const starred = isStarred(m)
  const gmail = useStore(pimProvider) === 'google'
  return (
    <div className="modal-backdrop" {...backdrop}>
      <div className="modal wide mail-reader" {...modal}>
        <header>
          <h2 id={titleId}>{m.subject || '(no subject)'}</h2>
          <button className="icon-btn" title="Close" aria-label="Close message" onClick={onClose}><X size={16} /></button>
        </header>
        <section>
          <p className="mail-meta">
            <strong>{fromName(m.from)}</strong> <span className="muted">{m.from?.match(/<(.*)>/)?.[1] ?? ''}</span><br />
            {full?.to && <span className="muted">to {full.to}</span>}{full?.to && <br />}
            <span className="muted">{m.date && !isNaN(new Date(m.date).getTime()) ? shortDateTime(new Date(m.date)) : m.date ?? ''}</span>
          </p>
          {full ? <pre className="doc-text mail-body">{full.body || '(no text content)'}</pre> : <p className="muted">Loading…</p>}
        </section>
        {/* Reply is what an open message is for, so it is the one filled button, last. The two state
            toggles are icons on the left; everything else is a quiet button. */}
        <footer>
          <button className={`icon-btn ${starred ? 'starred' : ''}`} title={starred ? 'Unstar' : 'Star'} aria-label={starred ? 'Unstar' : 'Star'} aria-pressed={starred} onClick={onStar}>
            <Star size={15} fill={starred ? 'currentColor' : 'none'} />
          </button>
          <button className="icon-btn" title="Mark unread" aria-label="Mark unread" onClick={onUnread}><Mail size={15} /></button>
          <span className="spacer" />
          {gmail && <a className="ghost-btn" href={`https://mail.google.com/mail/u/0/#all/${m.thread_id}`} target="_blank" rel="noreferrer"><ExternalLink size={14} /> Gmail</a>}
          <button className="ghost-btn" onClick={onArchive}><Archive size={14} /> Archive</button>
          <button className="ghost-btn" onClick={onSnooze}>Snooze</button>
          <button className="ghost-btn" onClick={onAsk}><MessageSquare size={14} /> Ask assistant</button>
          <button className="primary-btn" onClick={onReply}><Reply size={14} /> Reply</button>
        </footer>
      </div>
    </div>
  )
}

function MailCompose({ compose, setCompose, review, setReview, busy, valid, onDiscard, onSuggestTimes, onReview, onDraft, onSend }: {
  compose: Compose
  setCompose: Dispatch<SetStateAction<Compose | null>>
  review: Review | null
  setReview: (r: Review | null) => void
  busy: 'send' | 'draft' | 'review' | null
  valid: boolean
  onDiscard: () => void
  onSuggestTimes: () => void
  onReview: () => void
  onDraft: () => void
  onSend: () => void
}): JSX.Element {
  // Escape and a backdrop click only leave a message nobody has typed in: a half-written email
  // leaves via Discard, draft or send, never by a stray key or a click that missed the dialog.
  const opened = useRef(compose)
  const untouched = compose.to === opened.current.to && compose.subject === opened.current.subject && compose.body === opened.current.body
  const { titleId, backdrop, modal } = useModal(() => { if (untouched) onDiscard() })
  // A reply opens as the reader closes, and the reader hands focus back to its row after autoFocus
  // has already run: claim the field again, or the first keystroke goes nowhere.
  useEffect(() => { modal.ref.current?.querySelector<HTMLElement>(opened.current.replyTo ? 'textarea' : 'input')?.focus() }, [])
  return (
    <div className="modal-backdrop" {...backdrop}>
      <div className="modal wide mail-compose" {...modal}>
        <header>
          <h2 id={titleId}>{compose.replyTo ? `Reply: ${compose.replyTo.subject || '(no subject)'}` : 'New message'}</h2>
          <button className="icon-btn" title="Discard" aria-label="Discard message"
            onClick={() => { if (untouched || confirm('Discard this message?')) onDiscard() }}><X size={16} /></button>
        </header>
        <section>
          <input placeholder="To" aria-label="To" value={compose.to} onChange={(e) => setCompose((c) => c && { ...c, to: e.target.value })} autoFocus={!compose.replyTo} />
          <input placeholder="Subject" aria-label="Subject" value={compose.subject} onChange={(e) => setCompose((c) => c && { ...c, subject: e.target.value })} />
          <SmartTextarea
            kind="mail"
            value={compose.body}
            onChange={(body) => setCompose((c) => c && { ...c, body })}
            context={`Email subject: ${compose.subject}${compose.replyBody ? `\nIt replies to:\n${compose.replyBody.slice(0, 1500)}` : ''}`}
            placeholder="Write your email…"
            sharedStyle={{ minHeight: 220 }}
            autoFocus={!!compose.replyTo}
          />
          {review && (
            <div className="mail-review">
              <h4><Sparkles size={13} /> AI review</h4>
              <ul>{review.feedback.map((f, i) => <li key={i}>{f}</li>)}</ul>
              {review.revised && (
                <>
                  <pre className="doc-text">{review.revised}</pre>
                  <button className="ghost-btn" onClick={() => { setCompose((c) => c && { ...c, body: review.revised }); setReview(null) }}>
                    Use revised draft
                  </button>
                </>
              )}
            </div>
          )}
        </section>
        <footer>
          <button className="ghost-btn" onClick={onDiscard}>Discard</button>
          <span className="spacer" />
          <button className="ghost-btn" disabled={busy !== null} onClick={onSuggestTimes}>Suggest times</button>
          <button className="ghost-btn" disabled={busy !== null || !compose.body.trim()} onClick={onReview}>
            <Sparkles size={14} /> {busy === 'review' ? 'Reviewing…' : 'AI review'}
          </button>
          <button className="ghost-btn" disabled={busy !== null || !valid} onClick={onDraft}>
            {busy === 'draft' ? 'Saving…' : 'Save draft'}
          </button>
          <button className="primary-btn" disabled={busy !== null || !valid} onClick={onSend}>
            <Send size={14} /> {busy === 'send' ? 'Sending…' : 'Send'}
          </button>
        </footer>
      </div>
    </div>
  )
}

export default function MailView(): JSX.Element {
  // The active mail account (Google or Microsoft); the name keeps the old one to keep the diff small.
  const google = useStore(pimStatus)
  const label = useStore(pimLabel)
  const { toast, askAboutEmail } = useStore()
  const [messages, setMessages] = useState<GmailMessage[]>([])
  const [labels, setLabels] = useState<GmailLabel[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Filters. `folder` is 'inbox', 'all' or a user label name.
  const [read, setRead] = useState<ReadFilter>('all')
  const [starred, setStarred] = useState(false)
  const [attachments, setAttachments] = useState(false)
  const [folder, setFolder] = useState('inbox')
  const [range, setRange] = useState('7d')
  const [search, setSearch] = useState('')
  const [q, setQ] = useState('')
  const [open, setOpen] = useState<GmailMessage | null>(null)
  const [full, setFull] = useState<GmailFullMessage | null>(null)
  // A slow body fetch can land after another message was opened; only the latest one's is kept.
  const openedId = useRef<string | null>(null)
  const [snoozed, setSnoozed] = useState<string[]>([])
  const [compose, setCompose] = useState<Compose | null>(null)
  const [review, setReview] = useState<Review | null>(null)
  const [busy, setBusy] = useState<'send' | 'draft' | 'review' | null>(null)
  const [painted, setPainted] = useState('')
  const seq = useRef(0)
  const watch = useMailWatch()
  const narrowed = !!range || read !== 'all' || starred || attachments || !!search
  const clearFilters = (): void => { setRange(''); setRead('all'); setStarred(false); setAttachments(false); setSearch('') }

  const query = useMemo(() => {
    const parts: string[] = []
    if (folder === 'inbox') parts.push('in:inbox')
    else if (folder !== 'all') parts.push(labelQ(folder))
    if (read === 'unread') parts.push('is:unread')
    if (starred) parts.push('is:starred')
    if (attachments) parts.push('has:attachment')
    if (range) parts.push(`newer_than:${range}`)
    if (q.trim()) parts.push(q.trim())
    return parts.join(' ') || 'in:inbox'
  }, [folder, read, starred, attachments, range, q])

  // Typing is debounced into `q` so a keystroke does not fire a Gmail search.
  useEffect(() => {
    const t = setTimeout(() => setQ(search), 400)
    return (): void => clearTimeout(t)
  }, [search])

  const cacheKey = google?.connected ? `mail:${query}` : ''
  // Show the saved list for this filter before the sync returns, including when the
  // filter changes, so the previous folder does not sit on screen under a spinner.
  if (cacheKey !== painted) {
    setPainted(cacheKey)
    if (cacheKey) {
      const cached = readView<GmailMessage[]>(cacheKey)
      setMessages(cached ?? [])
      setLoading(cached == null)
      setError(null)
    }
  }

  // `refresh` is the Refresh button. Either way the request reuses saved headers and
  // only downloads messages that are new or changed.
  const load = async (refresh = false): Promise<void> => {
    if (!google?.connected) return
    const key = `mail:${query}`
    const mine = ++seq.current
    if (refresh) setLoading(true)
    setError(null)
    try {
      const out = await api.google.gmail(query, 30, refresh)
      if (seq.current !== mine) return
      writeView(key, out)
      setMessages(out)
      // Snoozes wake (and get made elsewhere) between loads, so the hidden set is re-read with the list.
      api.mailWatch.snoozed().then((r) => { if (seq.current === mine) setSnoozed(r.thread_ids) }).catch(() => {})
    } catch (e) {
      if (seq.current === mine) setError((e as Error).message)
    } finally {
      if (seq.current === mine) setLoading(false)
    }
  }
  useEffect(() => { void load() }, [query, google?.connected])
  useEffect(() => {
    if (!google?.connected) return
    api.google.gmailLabels().then(setLabels).catch(() => setLabels([]))
  }, [google?.connected])

  const snooze = async (m: GmailMessage): Promise<void> => {
    const t = new Date(); t.setDate(t.getDate() + 1); t.setHours(8, 0, 0, 0)
    try {
      await api.mailWatch.snooze(m.thread_id, t.toISOString())
      setSnoozed((s) => [...s, m.thread_id])
      if (open?.id === m.id) setOpen(null)
    } catch (e) { toast((e as Error).message, 'error') }
  }
  const suggestTimes = async (): Promise<void> => {
    const day = (n: number): string => { const d = new Date(); d.setDate(d.getDate() + n); return d.toLocaleDateString('en-CA') }
    try {
      const { body } = await api.google.suggestTimes({ window_start: day(1), window_end: day(7) })
      setCompose((c) => c && { ...c, body: c.body ? `${c.body}\n\n${body}` : body })
    } catch (e) { toast((e as Error).message, 'error') }
  }

  const userLabels = labels.filter((l) => l.type === 'user')
  const patchLocal = (id: string, patch: Partial<GmailMessage>): void => {
    setMessages((ms) => ms.map((m) => (m.id === id ? { ...m, ...patch } : m)))
    setOpen((o) => (o?.id === id ? { ...o, ...patch } : o))  // the reader's Star/Unstar reads `open`
  }

  const modify = async (m: GmailMessage, patch: { mark_read?: boolean; archive?: boolean; star?: boolean }): Promise<void> => {
    try {
      await api.google.gmailModify(m.id, patch)
      if (patch.mark_read !== undefined) patchLocal(m.id, { unread: !patch.mark_read })
      if (patch.star !== undefined)
        patchLocal(m.id, { labels: patch.star ? [...m.labels, 'STARRED'] : m.labels.filter((l) => l !== 'STARRED') })
      if (patch.archive) {
        setMessages((ms) => (folder === 'inbox' ? ms.filter((x) => x.id !== m.id) : ms.map((x) => (x.id === m.id ? { ...x, labels: x.labels.filter((l) => l !== 'INBOX') } : x))))
        if (open?.id === m.id) setOpen(null)
      }
    } catch (e) {
      toast((e as Error).message, 'error')
    }
  }

  const openMessage = (m: GmailMessage): void => {
    setOpen(m)
    openedId.current = m.id
    const key = `mailbody:${m.id}`
    const cached = readView<GmailFullMessage>(key)
    setFull(cached)
    api.google.gmailGet(m.id).then((full) => { writeView(key, full); if (openedId.current === m.id) setFull(full) }).catch((e) => toast((e as Error).message, 'error'))
    if (m.unread) void modify(m, { mark_read: true })
  }

  const startCompose = (): void => {
    setReview(null)
    setCompose({ to: '', subject: '', body: '', replyTo: null, replyBody: '' })
  }
  const startReply = (m: GmailMessage): void => {
    const addr = m.from?.match(/<(.*)>/)?.[1] ?? m.from ?? ''
    const subject = /^re:/i.test(m.subject ?? '') ? (m.subject ?? '') : `Re: ${m.subject ?? ''}`
    const orig = full?.id === m.id ? full.body : ''
    const quote = orig
      ? `\n\nOn ${m.date ? new Date(m.date).toLocaleString() : ''}, ${fromName(m.from)} wrote:\n${orig.split('\n').slice(0, 40).map((l) => `> ${l}`).join('\n')}`
      : ''
    setReview(null)
    setOpen(null)
    setCompose({ to: addr, subject, body: quote, replyTo: m, replyBody: orig || m.snippet })
  }
  const composeValid = compose !== null && /\S+@\S+/.test(compose.to) && (compose.subject.trim() !== '' || compose.body.trim() !== '')
  const doSend = async (): Promise<void> => {
    if (!compose) return
    setBusy('send')
    try {
      // Queued behind its undo hold, not sent: say so, and let PendingSends count it down.
      const queued = await api.google.gmailSend({ to: compose.to, subject: compose.subject, body: compose.body, reply_to_message_id: compose.replyTo?.id ?? null })
      if (queued.status === 'sent' && queued.verified === false) toast(queued.error ?? 'Sent, but Gmail did not confirm it. Check your Sent folder.', 'error')
      else toast(queued.status === 'sent' ? 'Email sent.' : `Sending in ${queued.seconds_left}s — you can still undo it.`)
      window.dispatchEvent(new Event('grain-outbox-changed'))
      setCompose(null)
      setReview(null)
    } catch (e) {
      toast((e as Error).message, 'error')
    } finally {
      setBusy(null)
    }
  }
  const doDraft = async (): Promise<void> => {
    if (!compose) return
    setBusy('draft')
    try {
      await api.google.gmailDraft({ to: compose.to, subject: compose.subject, body: compose.body, reply_to_message_id: compose.replyTo?.id ?? null })
      toast('Draft saved in Gmail.')
      setCompose(null)
      setReview(null)
    } catch (e) {
      toast((e as Error).message, 'error')
    } finally {
      setBusy(null)
    }
  }
  const doReview = async (): Promise<void> => {
    if (!compose) return
    setBusy('review')
    try {
      setReview(await api.assist.mailReview({ to: compose.to, subject: compose.subject, body: compose.body, reply_context: compose.replyBody }))
    } catch (e) {
      toast((e as Error).message, 'error')
    } finally {
      setBusy(null)
    }
  }

  const askAssistant = (m: GmailMessage): void => {
    setOpen(null)
    void askAboutEmail(m.id, m.subject)
  }

  // `open` is the row as it was when clicked; the list holds what starring it since has changed.
  const opened = open ? messages.find((x) => x.id === open.id) ?? open : null
  const folderLabel = folder === 'inbox' ? 'Inbox' : folder === 'all' ? 'All mail' : folder

  usePageContext(() => ({
    view: 'mail',
    label: open ? `Email “${oneLine(open.subject || '') || '(no subject)'}”` : `Mail · ${folder}`,
    detail: open
      ? `The user has this message open — Gmail id \`${oneLine(open.id, 80)}\`, thread \`${oneLine(open.thread_id || '', 80)}\`.\nFrom: ${oneLine(open.from || '', 120)}\nSubject: ${oneLine(open.subject || '') || '(no subject)'}\nDate: ${oneLine(open.date || '', 80)}\n\n${fenced(full?.body ?? open.snippet ?? '')}`
      : `The ${folder} list is on screen${q ? ` filtered by “${q}”` : ''}:\n${lines(messages, (m) => `${m.unread ? '[unread] ' : ''}${oneLine(m.from || '', 80)} — ${oneLine(m.subject || '') || '(no subject)'} (\`${oneLine(m.id, 80)}\`)`, 25)}`,
    refs: open ? [{ kind: 'email', id: open.id, name: open.subject ?? '' }] : messages.slice(0, 25).map((m) => ({ kind: 'email', id: m.id, name: m.subject ?? '' })),
    hints: open ? ['Draft a reply', 'What is being asked of me here?'] : ['What needs a reply today?', 'Summarise this inbox']
  }), [open, full, messages, folder, q])

  return (
    <main className="page mail-page">
      <header className="page-header drag">
        <SidebarToggle />
        <h2><MailIcon size={16} /> Mail {google?.email && <span className="muted">· {google.email}</span>}</h2>
        {google?.connected && <div className="no-drag header-right">
          <label className="search">
            <Search size={14} />
            <input placeholder="Search mail (from:, subject:, …)" aria-label="Search mail" value={search} onChange={(e) => setSearch(e.target.value)} />
          </label>
          <button className="icon-btn" title="Refresh" aria-label="Refresh mail" onClick={() => void load(true)} disabled={loading}><RefreshCw size={15} className={loading ? 'spin' : ''} /></button>
          <button className="primary-btn" onClick={startCompose}><SquarePen size={14} /> Compose</button>
        </div>}
        <AppSwitcher />
      </header>

      {/* Disconnected, the filters below have nothing to filter: one clear next step instead of a banner
          over a dead toolbar and a blank page. */}
      {!google?.connected && (
        <div className="empty-state">
          <MailIcon size={28} />
          <h2>Connect your inbox</h2>
          <p>Grain reads and triages your mail once {label} is connected. Nothing is sent without asking you first.</p>
          <button className="primary-btn" onClick={() => useStore.getState().openSettings(PIM_SETTINGS_TAB)}>Connect {label}</button>
        </div>
      )}
      {error && <div className="notice-bar error">{error}</div>}

      {google?.connected && <>
      <div className="mail-toolbar no-drag">
        <div className="seg">
          <button className={read === 'all' ? 'active' : ''} onClick={() => setRead('all')}>All</button>
          <button className={read === 'unread' ? 'active' : ''} onClick={() => setRead('unread')}>Unread</button>
        </div>
        <label className={`chip-check ${starred ? 'on' : ''}`}>
          <input type="checkbox" checked={starred} onChange={() => setStarred((v) => !v)} /><Star size={12} /> Starred
        </label>
        <label className={`chip-check ${attachments ? 'on' : ''}`}>
          <input type="checkbox" checked={attachments} onChange={() => setAttachments((v) => !v)} /><Paperclip size={12} /> Attachments
        </label>
        {/* Not filters on the list below: each opens its own section above it. */}
        <span className="toolbar-sep" aria-hidden />
        <MailWatchChips watch={watch} />
        <div className="toolbar-right">
          <select value={folder} onChange={(e) => setFolder(e.target.value)} title="Folder or label" aria-label="Folder or label">
            <option value="inbox">Inbox</option>
            <option value="all">All mail</option>
            {userLabels.map((l) => <option key={l.id} value={l.name}>{l.name}</option>)}
          </select>
          <select value={range} onChange={(e) => setRange(e.target.value)} title="Time range" aria-label="Time range">
            {RANGES.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
          </select>
        </div>
      </div>

      <div className="page-body wide">
        <MailWatchList watch={watch} />
        {watch.kind && messages.length > 0 && <h4 className="section-h">{folderLabel} <span>{messages.length}</span></h4>}
        {!loading && messages.length === 0 && !error && (
          <div className="empty-state">
            <MailIcon size={28} />
            <h2>No mail here</h2>
            <p>Nothing in {folderLabel} matches these filters. Try a wider time range or another folder.</p>
            {narrowed
              ? <button className="primary-btn" onClick={clearFilters}>Clear filters</button>
              : <button className="primary-btn" onClick={startCompose}><SquarePen size={14} /> Compose</button>}
          </div>
        )}
        <div className="mail-list">
          {messages.filter((m) => !snoozed.includes(m.thread_id)).map((m) => (
            <div key={m.id} className={`mail-row ${m.unread ? 'unread' : ''}`} {...rowButton(() => openMessage(m))}>
              <span className="mail-row-dot" title={m.unread ? 'Unread' : undefined} />
              <button className={`icon-btn ghost sm star ${isStarred(m) ? 'on' : ''}`} title={isStarred(m) ? 'Unstar' : 'Star'} aria-label={isStarred(m) ? 'Unstar' : 'Star'}
                onClick={(e) => { e.stopPropagation(); void modify(m, { star: !isStarred(m) }) }}>
                <Star size={14} fill={isStarred(m) ? 'currentColor' : 'none'} />
              </button>
              <span className="mail-row-from" title={m.from ?? ''}>{fromName(m.from)}</span>
              <span className="mail-row-text">
                <span className="mail-row-subject">{m.subject || '(no subject)'}</span>
                <span className="mail-row-snippet"> — {m.snippet}</span>
              </span>
              <span className="mail-row-actions">
                <button className="icon-btn ghost sm" title={m.unread ? 'Mark read' : 'Mark unread'} aria-label={m.unread ? 'Mark read' : 'Mark unread'}
                  onClick={(e) => { e.stopPropagation(); void modify(m, { mark_read: m.unread }) }}>
                  {m.unread ? <MailOpen size={14} /> : <Mail size={14} />}
                </button>
                <button className="icon-btn ghost sm" title="Archive" aria-label="Archive" onClick={(e) => { e.stopPropagation(); void modify(m, { archive: true }) }}><Archive size={14} /></button>
              </span>
              <span className="mail-row-date">{fmtDate(m.date)}</span>
            </div>
          ))}
        </div>
      </div>
      </>}

      {opened && (
        <MailReader
          message={opened} full={full} onClose={() => setOpen(null)}
          onReply={() => startReply(opened)}
          onStar={() => void modify(opened, { star: !isStarred(opened) })}
          onArchive={() => void modify(opened, { archive: true })}
          onSnooze={() => void snooze(opened)}
          onUnread={() => { void modify(opened, { mark_read: false }); setOpen(null) }}
          onAsk={() => askAssistant(opened)}
        />
      )}

      {compose && (
        <MailCompose
          compose={compose} setCompose={setCompose} review={review} setReview={setReview}
          busy={busy} valid={composeValid}
          onDiscard={() => { setCompose(null); setReview(null) }}
          onSuggestTimes={() => void suggestTimes()} onReview={() => void doReview()} onDraft={() => void doDraft()} onSend={() => void doSend()}
        />
      )}
    </main>
  )
}
