import { useEffect, useMemo, useRef, useState } from 'react'
import { Mail as MailIcon, PanelLeftOpen, RefreshCw, Search, Star, Archive, MailOpen, Mail, ExternalLink, MessageSquare, Paperclip } from 'lucide-react'
import { useStore } from '../store'
import { api } from '../lib/api'
import type { GmailFullMessage, GmailLabel, GmailMessage } from '@shared/types'

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
const RANGES = [
  { value: '', label: 'Any time' },
  { value: '1d', label: 'Past day' },
  { value: '7d', label: 'Past week' },
  { value: '30d', label: 'Past month' }
]

export default function MailView(): JSX.Element {
  const sidebarOpen = useStore((s) => s.sidebarOpen)
  const google = useStore((s) => s.google)
  const { toggleSidebar, setSettingsOpen, toast, newChat, send } = useStore()
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

  // Each fetch is N+1 Gmail calls on the backend, so typing is debounced into `q`.
  useEffect(() => {
    const t = setTimeout(() => setQ(search), 400)
    return (): void => clearTimeout(t)
  }, [search])

  const load = async (): Promise<void> => {
    if (!google?.connected) return
    const mine = ++seq.current
    setLoading(true)
    setError(null)
    try {
      const out = await api.google.gmail(query, 30)
      if (seq.current === mine) setMessages(out)
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
    setFull(null)
    api.google.gmailGet(m.id).then(setFull).catch((e) => toast((e as Error).message, 'error'))
    if (m.unread) void modify(m, { mark_read: true })
  }

  const askAssistant = (m: GmailMessage): void => {
    setOpen(null)
    newChat(null)
    void send(`Summarize this email and suggest a reply if one is needed. Gmail message id: ${m.id} (subject: ${m.subject})`)
  }

  const isStarred = (m: GmailMessage): boolean => m.labels.includes('STARRED')

  return (
    <main className="page mail-page">
      <header className="page-header drag">
        {!sidebarOpen && <button className="icon-btn no-drag" title="Show sidebar (⌘B)" onClick={toggleSidebar}><PanelLeftOpen size={16} /></button>}
        <h2><MailIcon size={16} /> Mail {google?.email && <span className="muted">· {google.email}</span>}</h2>
        <div className="no-drag header-right">
          <label className="search">
            <Search size={14} />
            <input placeholder="Search mail (from:, subject:, …)" value={search} onChange={(e) => setSearch(e.target.value)} />
          </label>
          <button className="icon-btn" title="Refresh" onClick={() => void load()} disabled={loading}><RefreshCw size={15} className={loading ? 'spin' : ''} /></button>
        </div>
      </header>

      {!google?.connected && (
        <div className="notice-bar">
          Mail needs a Google connection. <button className="link" onClick={() => setSettingsOpen(true)}>Connect Google</button>
        </div>
      )}
      {error && <div className="notice-bar error">{error}</div>}

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
    </main>
  )
}
