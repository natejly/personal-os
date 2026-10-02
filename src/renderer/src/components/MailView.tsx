import { useEffect, useMemo, useRef, useState } from 'react'
import { Mail as MailIcon, PanelLeftOpen, RefreshCw, Search, Star, Archive, MailOpen, Mail, ExternalLink, MessageSquare, Paperclip, SquarePen, Reply, Sparkles, Send } from 'lucide-react'
import { useStore } from '../store'
import { api } from '../lib/api'
import SmartTextarea from './SmartTextarea'
import type { GmailFullMessage, GmailLabel, GmailMessage } from '@shared/types'
import { oneLine } from '../lib/emailAsk'
import { fenced, lines, usePageContext } from '../lib/pageContext'
import { readView, writeView } from '../lib/viewCache'
import AppSwitcher from './AppSwitcher'

const fromName = (s: string | null): string => (s ?? '').replace(/<.*>/, '').replace(/"/g, '').trim() || (s ?? '')
const fmtDate = (s: string | null): string => {
  if (!s) return ''
  const d = new Date(s)
  if (isNaN(d.getTime())) return s
  const today = new Date()
  return d.toDateString() === today.toDateString()
    ? d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
    : d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', ...(d.getFullYear() !== today.getFullYear() ? { year: 'numeric' } : {}) })
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

export default function MailView(): JSX.Element {
  const sidebarOpen = useStore((s) => s.sidebarOpen)
  const google = useStore((s) => s.google)
  const { toggleSidebar, setSettingsOpen, toast, askAboutEmail } = useStore()
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
  const [compose, setCompose] = useState<Compose | null>(null)
  const [review, setReview] = useState<Review | null>(null)
  const [busy, setBusy] = useState<'send' | 'draft' | 'review' | null>(null)
  const [painted, setPainted] = useState('')
  const seq = useRef(0)

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

  const userLabels = labels.filter((l) => l.type === 'user')
  const patchLocal = (id: string, patch: Partial<GmailMessage>): void =>
    setMessages((ms) => ms.map((m) => (m.id === id ? { ...m, ...patch } : m)))

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
    const key = `mailbody:${m.id}`
    const cached = readView<GmailFullMessage>(key)
    setFull(cached)
    api.google.gmailGet(m.id).then((full) => { writeView(key, full); setFull(full) }).catch((e) => toast((e as Error).message, 'error'))
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

  const isStarred = (m: GmailMessage): boolean => m.labels.includes('STARRED')

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
        {!sidebarOpen && <button className="icon-btn no-drag" title="Show sidebar (⌘B)" onClick={toggleSidebar}><PanelLeftOpen size={16} /></button>}
        <h2><MailIcon size={16} /> Mail {google?.email && <span className="muted">· {google.email}</span>}</h2>
        {google?.connected && <div className="no-drag header-right">
          <button className="ghost-btn" onClick={startCompose}><SquarePen size={14} /> Compose</button>
          <label className="search">
            <Search size={14} />
            <input placeholder="Search mail (from:, subject:, …)" value={search} onChange={(e) => setSearch(e.target.value)} />
          </label>
          <button className="icon-btn" title="Refresh" aria-label="Refresh mail" onClick={() => void load(true)} disabled={loading}><RefreshCw size={15} className={loading ? 'spin' : ''} /></button>
        </div>}
        <AppSwitcher />
      </header>

      {/* Disconnected, the filters below have nothing to filter: one clear next step instead of a banner
          over a dead toolbar and a blank page. */}
      {!google?.connected && (
        <div className="empty-state">
          <MailIcon size={28} />
          <h2>Connect your inbox</h2>
          <p>Grain reads and triages Gmail once Google is connected. Nothing is sent without asking you first.</p>
          <button className="primary-btn" onClick={() => setSettingsOpen(true)}>Connect Google</button>
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
        <div className="toolbar-right">
          <select value={folder} onChange={(e) => setFolder(e.target.value)} title="Folder or label">
            <option value="inbox">Inbox</option>
            <option value="all">All mail</option>
            {userLabels.map((l) => <option key={l.id} value={l.name}>{l.name}</option>)}
          </select>
          <select value={range} onChange={(e) => setRange(e.target.value)} title="Time range">
            {RANGES.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
          </select>
        </div>
      </div>

      <div className="page-body wide">
        {google?.connected && !loading && messages.length === 0 && !error && (
          <div className="empty-hint big"><p>No mail matches these filters.</p></div>
        )}
        <div className="mail-list">
          {messages.map((m) => (
            <div key={m.id} className={`mail-row ${m.unread ? 'unread' : ''}`} onClick={() => openMessage(m)} role="button" tabIndex={0}>
              <button className={`icon-btn ghost sm star ${isStarred(m) ? 'on' : ''}`} title={isStarred(m) ? 'Unstar' : 'Star'}
                onClick={(e) => { e.stopPropagation(); void modify(m, { star: !isStarred(m) }) }}>
                <Star size={14} fill={isStarred(m) ? 'currentColor' : 'none'} />
              </button>
              <span className="mail-row-from" title={m.from ?? ''}>{fromName(m.from)}</span>
              <span className="mail-row-text">
                <span className="mail-row-subject">{m.subject || '(no subject)'}</span>
                <span className="mail-row-snippet"> — {m.snippet}</span>
              </span>
              <span className="mail-row-actions">
                <button className="icon-btn ghost sm" title={m.unread ? 'Mark read' : 'Mark unread'}
                  onClick={(e) => { e.stopPropagation(); void modify(m, { mark_read: m.unread }) }}>
                  {m.unread ? <MailOpen size={14} /> : <Mail size={14} />}
                </button>
                <button className="icon-btn ghost sm" title="Archive" onClick={(e) => { e.stopPropagation(); void modify(m, { archive: true }) }}><Archive size={14} /></button>
              </span>
              <span className="mail-row-date">{fmtDate(m.date)}</span>
            </div>
          ))}
        </div>
      </div>
      </>}

      {open && (
        <div className="modal-backdrop" onMouseDown={() => setOpen(null)}>
          <div className="modal wide" onMouseDown={(e) => e.stopPropagation()}>
            <header>
              <h3>{open.subject || '(no subject)'}</h3>
            </header>
            <section>
              <p className="mail-meta">
                <strong>{fromName(open.from)}</strong> <span className="muted">{open.from?.match(/<(.*)>/)?.[1] ?? ''}</span><br />
                {full?.to && <span className="muted">to {full.to}</span>}{full?.to && <br />}
                <span className="muted">{open.date ? new Date(open.date).toLocaleString() : ''}</span>
              </p>
              {full ? <pre className="doc-text">{full.body || '(no text content)'}</pre> : <p className="muted">Loading…</p>}
            </section>
            <footer>
              <button className="ghost-btn" onClick={() => startReply(open)}><Reply size={14} /> Reply</button>
              <button className="ghost-btn" onClick={() => void modify(open, { star: !isStarred(open) })}>
                <Star size={14} fill={isStarred(open) ? 'currentColor' : 'none'} /> {isStarred(open) ? 'Unstar' : 'Star'}
              </button>
              <button className="ghost-btn" onClick={() => void modify(open, { archive: true })}><Archive size={14} /> Archive</button>
              <button className="ghost-btn" onClick={() => { void modify(open, { mark_read: false }); setOpen(null) }}><Mail size={14} /> Mark unread</button>
              <a className="ghost-btn" href={`https://mail.google.com/mail/u/0/#all/${open.thread_id}`} target="_blank" rel="noreferrer"><ExternalLink size={14} /> Gmail</a>
              <button className="primary-btn" onClick={() => askAssistant(open)}><MessageSquare size={14} /> Ask assistant</button>
            </footer>
          </div>
        </div>
      )}

      {compose && (
        // No backdrop-click close: a half-written email should only leave via Discard, draft or send.
        <div className="modal-backdrop">
          <div className="modal wide mail-compose" onMouseDown={(e) => e.stopPropagation()}>
            <header>
              <h3>{compose.replyTo ? `Reply: ${compose.replyTo.subject || '(no subject)'}` : 'New message'}</h3>
            </header>
            <section>
              <input placeholder="To" value={compose.to} onChange={(e) => setCompose((c) => c && { ...c, to: e.target.value })} autoFocus={!compose.replyTo} />
              <input placeholder="Subject" value={compose.subject} onChange={(e) => setCompose((c) => c && { ...c, subject: e.target.value })} />
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
              <button className="ghost-btn" onClick={() => { setCompose(null); setReview(null) }}>Discard</button>
              <button className="ghost-btn" disabled={busy !== null || !compose.body.trim()} onClick={() => void doReview()}>
                <Sparkles size={14} /> {busy === 'review' ? 'Reviewing…' : 'AI review'}
              </button>
              <button className="ghost-btn" disabled={busy !== null || !composeValid} onClick={() => void doDraft()}>
                {busy === 'draft' ? 'Saving…' : 'Save draft'}
              </button>
              <button className="primary-btn" disabled={busy !== null || !composeValid} onClick={() => void doSend()}>
                <Send size={14} /> {busy === 'send' ? 'Sending…' : 'Send'}
              </button>
            </footer>
          </div>
        </div>
      )}
    </main>
  )
}
