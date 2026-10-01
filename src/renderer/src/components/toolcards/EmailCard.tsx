import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { KeyboardEvent, ClipboardEvent, ReactNode } from 'react'
import { AlertTriangle, ArrowLeft, Check, ChevronRight, ExternalLink, FileText, Mail, Paperclip, Pencil, Reply, Send, ShieldCheck, Trash2, Undo2, X } from 'lucide-react'
import type { PendingSend, ToolEvent, Verification } from '@shared/types'
import { api, verificationMessage } from '../../lib/api'
import { useStore } from '../../store'
import {
  clock, composeArgs, composeProblem, editedFields, gmailLink, looksMarkdown, markdownToPlain, parseAddressList,
  parseMailMessage, parseMailRows, readPreview, recipientsFromArg, senderInitial, senderName, shortTime,
  type ComposeDraft, type Recipient, type MailRow
} from '../../lib/mailCompose'
import { registerToolCard, type ToolCardProps } from './registry'
import '../../styles/mailcard.css'

const pretty = (v: unknown): string => JSON.stringify(v, null, 2)

/** The raw call, folded away: the card is the product, this is for the curious. */
function Details({ event }: { event: ToolEvent }): JSX.Element {
  return (
    <details className="mc-details">
      <summary>Details</summary>
      <div className="mc-details-body">
        <h6>Arguments{event.edited_arguments ? ' (as approved)' : ''}</h6>
        <pre>{pretty(event.arguments)}</pre>
        {event.original_arguments && <><h6>Proposed by the assistant</h6><pre>{pretty(event.original_arguments)}</pre></>}
        {!event.pending && <><h6>{event.error ? 'Error' : 'Result'}</h6><pre>{event.error ?? event.result_preview}</pre></>}
      </div>
    </details>
  )
}

function VerifiedBadge({ v }: { v: Verification | null | undefined }): JSX.Element | null {
  if (!v) return null
  const ok = v.status === 'verified'
  return (
    <span className={`mc-badge ${ok ? 'ok' : 'bad'}`} title={ok ? `${v.what} · compared ${v.compared.join(', ') || 'existence'}` : verificationMessage(v)}>
      {ok ? <ShieldCheck size={11} /> : <AlertTriangle size={11} />} {ok ? 'verified' : 'unverified'}
    </span>
  )
}

// ---------------------------------------------------------------- recipients

function RecipientField({ label, chips, bad, onChange, onSubmit, disabled }: {
  label: string
  chips: Recipient[]
  bad: string[]
  onChange: (chips: Recipient[], bad: string[]) => void
  onSubmit: () => void
  disabled: boolean
}): JSX.Element {
  const [text, setText] = useState('')
  const input = useRef<HTMLInputElement>(null)

  const commit = (raw: string): void => {
    const { ok, invalid } = parseAddressList(raw)
    if (!ok.length && !invalid.length) { setText(''); return }
    const seen = new Set(chips.map((c) => c.email.toLowerCase()))
    const fresh = ok.filter((r) => !seen.has(r.email.toLowerCase()) && seen.add(r.email.toLowerCase()))
    onChange([...chips, ...fresh], [...bad, ...invalid])
    setText('')
  }
  const onKey = (e: KeyboardEvent<HTMLInputElement>): void => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); if (text.trim()) commit(text); onSubmit(); return }
    if (e.key === 'Enter' || e.key === ',' || e.key === ';' || (e.key === 'Tab' && text.trim())) {
      if (text.trim()) { e.preventDefault(); commit(text) }
    } else if (e.key === 'Backspace' && !text) {
      if (bad.length) onChange(chips, bad.slice(0, -1))
      else if (chips.length) onChange(chips.slice(0, -1), bad)
    }
  }
  const onPaste = (e: ClipboardEvent<HTMLInputElement>): void => {
    const t = e.clipboardData.getData('text')
    if (/[,;\n]/.test(t)) { e.preventDefault(); commit(text + t) }
  }
  return (
    <div className="mc-row" onClick={() => input.current?.focus()}>
      <span className="mc-label">{label}</span>
      <div className="mc-chips">
        {chips.map((c) => (
          <span key={c.email} className="mc-chip" title={c.email}>
            {c.name || c.email}
            <button type="button" className="mc-chip-x" disabled={disabled} aria-label={`Remove ${c.email}`}
              onClick={(e) => { e.stopPropagation(); onChange(chips.filter((x) => x !== c), bad) }}><X size={10} /></button>
          </span>
        ))}
        {bad.map((b) => (
          <span key={b} className="mc-chip invalid" title="Not a valid email address">
            <AlertTriangle size={10} /> {b}
            <button type="button" className="mc-chip-x" disabled={disabled} aria-label={`Remove ${b}`}
              onClick={(e) => { e.stopPropagation(); onChange(chips, bad.filter((x) => x !== b)) }}><X size={10} /></button>
          </span>
        ))}
        <input ref={input} className="mc-chip-input" value={text} disabled={disabled} aria-label={label}
          placeholder={chips.length || bad.length ? '' : 'name@example.com'}
          onChange={(e) => setText(e.target.value)} onKeyDown={onKey} onPaste={onPaste} onBlur={() => commit(text)} />
      </div>
    </div>
  )
}

// ---------------------------------------------------------------- thread context

function ReplyContext({ messageId, subject }: { messageId: string; subject: string }): JSX.Element {
  const [who, setWho] = useState<string | null>(null)
  useEffect(() => {
    let dead = false
    api.google.gmailGet(messageId).then((m) => { if (!dead && m.from) setWho(senderName(m.from)) }).catch(() => undefined)
    return () => { dead = true }
  }, [messageId])
  return (
    <div className="mc-reply"><Reply size={12} /> <span>{subject ? <>Re: <b>{subject.replace(/^re:\s*/i, '')}</b></> : 'Reply in thread'}{who ? <>, replying to <b>{who}</b></> : ''}</span></div>
  )
}

// ---------------------------------------------------------------- send status (the undo window)

function useOutboxRow(id: string | null): { row: PendingSend | null; missing: boolean; refresh: () => Promise<void>; set: (r: PendingSend) => void } {
  const [row, setRow] = useState<PendingSend | null>(null)
  const [missing, setMissing] = useState(false)
  const refresh = useCallback(async (): Promise<void> => {
    if (!id) return
    try {
      const r = await api.outbox.list()
      const hit = r.sends.find((s) => s.id === id) ?? null
      setRow(hit)
      setMissing(!hit)
    } catch { /* offline: keep what we have */ }
  }, [id])
  useEffect(() => { void refresh() }, [refresh])
  const live = row ? row.status === 'holding' || row.status === 'sending' : false
  useEffect(() => {
    if (!live) return
    const t = setInterval(() => {
      setRow((r) => (r && r.status === 'holding' ? { ...r, seconds_left: Math.max(0, r.seconds_left - 1) } : r))
    }, 1000)
    const sync = setInterval(() => void refresh(), 3000)
    return () => { clearInterval(t); clearInterval(sync) }
  }, [live, refresh])
  // The hold ran out: ask the server what happened instead of waiting for the next sync.
  const out = row?.status === 'holding' && row.seconds_left === 0
  useEffect(() => { if (out) void refresh() }, [out, refresh])
  return { row, missing, refresh, set: setRow }
}

function SendStatus({ id, immediate }: { id: string | null; immediate: Record<string, unknown> | null }): JSX.Element {
  const { row, missing, refresh, set } = useOutboxRow(id)
  const toast = useStore((s) => s.toast)
  const [busy, setBusy] = useState(false)
  const act = async (what: 'cancel' | 'sendNow'): Promise<void> => {
    if (!id) return
    setBusy(true)
    try {
      set(what === 'cancel' ? await api.outbox.cancel(id) : await api.outbox.sendNow(id))
    } catch (e) {
      toast((e as Error).message, 'error')
      await refresh()
    } finally { setBusy(false) }
  }

  // The hold was off, so the send already happened inside the tool call.
  if (immediate) {
    const v = (immediate.verification as Verification | undefined) ?? null
    const ok = !v || v.status === 'verified'
    return <Status tone={ok ? 'ok' : 'bad'} icon={ok ? <Check size={14} /> : <AlertTriangle size={14} />}
      text={ok ? 'Sent' : 'Sent, but unconfirmed'} badge={<VerifiedBadge v={v} />}
      right={ok ? <a className="mc-link" href={gmailLink('message', String(immediate.message_id ?? ''))} target="_blank" rel="noreferrer"><ExternalLink size={11} /> Open in Gmail</a> : undefined}
      why={!ok && v ? verificationMessage(v) : undefined} />
  }
  if (!row) {
    return <Status tone="muted" icon={<Send size={14} />}
      text={missing ? 'Queued to send' : 'Checking the send…'}
      why={missing ? 'This send is no longer tracked here. Open Gmail to confirm it went out.' : undefined}
      right={missing ? <a className="mc-link" href="https://mail.google.com/mail/u/0/#sent" target="_blank" rel="noreferrer"><ExternalLink size={11} /> Sent mail</a> : undefined} />
  }
  switch (row.status) {
    case 'holding':
      return <Status tone="pending" icon={<Send size={14} />} text={<>Sending in <b className="mc-count">{clock(row.seconds_left)}</b></>}
        right={<>
          <button className="mc-btn danger" disabled={busy} onClick={() => void act('cancel')}><Undo2 size={12} /> Undo</button>
          <button className="mc-btn" disabled={busy} onClick={() => void act('sendNow')}>Send now</button>
        </>} />
    case 'sending':
      return <Status tone="pending" icon={<Send size={14} />} text="Sending…" />
    case 'sent': {
      const ok = row.verified !== false
      return <Status tone={ok ? 'ok' : 'bad'} icon={ok ? <Check size={14} /> : <AlertTriangle size={14} />}
        text={ok ? 'Sent' : 'Sent, but unconfirmed'} badge={<VerifiedBadge v={row.verification} />}
        why={!ok ? (row.error ?? (row.verification ? verificationMessage(row.verification) : undefined)) : undefined}
        right={<a className="mc-link" href={gmailLink('message', row.message_id)} target="_blank" rel="noreferrer"><ExternalLink size={11} /> Open in Gmail</a>} />
    }
    case 'cancelled':
      return <Status tone="muted" icon={<Undo2 size={14} />} text="Undone. It was never sent." />
    case 'failed':
      return <Status tone="bad" icon={<AlertTriangle size={14} />} text="Send failed" why={row.error ?? undefined} />
    default:
      return <Status tone="bad" icon={<AlertTriangle size={14} />} text="Never sent" why={row.error ?? 'The app closed before the hold ran out.'}
        right={<button className="mc-btn" disabled={busy} onClick={() => void act('sendNow')}>Send now</button>} />
  }
}

function Status({ tone, icon, text, badge, right, why }: {
  tone: 'ok' | 'bad' | 'pending' | 'muted'
  icon: JSX.Element
  text: ReactNode
  badge?: JSX.Element | null
  right?: ReactNode
  why?: string
}): JSX.Element {
  return (
    <div className={`mc-status ${tone}`} role="status">
      <div className="mc-status-main">
        <span className="mc-status-icon">{icon}</span>
        <span className="mc-status-text">{text}</span>
        {badge}
        <span className="mc-status-right">{right}</span>
      </div>
      {why && <div className="mc-status-why">{why}</div>}
    </div>
  )
}

// ---------------------------------------------------------------- the compose card

function ComposeCard({ event, pending, decide }: ToolCardProps): JSX.Element {
  const isSend = event.name === 'gmail_send'
  const original = event.arguments
  const account = useStore((s) => s.google?.email ?? null)
  const init = useMemo(() => recipientsFromArg(original.to), [original.to])
  const [to, setTo] = useState<Recipient[]>(init.ok)
  const [bad, setBad] = useState<string[]>(init.invalid)
  const [subject, setSubject] = useState(String(original.subject ?? ''))
  const [body, setBody] = useState(String(original.body ?? ''))
  const [busy, setBusy] = useState<'send' | 'draft' | 'discard' | null>(null)
  const [showMore, setShowMore] = useState(false)
  // What was just approved, so the card does not flash the model's original while the result is on its way.
  const [submitted, setSubmitted] = useState<Record<string, unknown> | null>(null)
  const bodyRef = useRef<HTMLTextAreaElement>(null)

  const draft: ComposeDraft = { to, subject, body }
  const changed = useMemo(() => editedFields(original, { to, subject, body }), [original, to, subject, body])
  const problem = composeProblem(draft, bad)
  const replyId = typeof original.reply_to_message_id === 'string' ? original.reply_to_message_id : ''
  const attachments = Array.isArray(original.attachments) ? (original.attachments as unknown[]).map((a) => (typeof a === 'string' ? a : String((a as { name?: unknown })?.name ?? 'attachment'))) : []

  useEffect(() => {  // grow with the text, within reason
    const el = bodyRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(Math.max(el.scrollHeight, 120), 360)}px`
  }, [body, pending])

  const go = async (kind: 'send' | 'draft'): Promise<void> => {
    if (problem || busy) return
    setBusy(kind)
    try {
      const args = composeArgs(original, draft, kind === 'draft' && isSend)
      // Only attach edits when there are some (or the send became a draft): an untouched card approves the
      // model's own call, exactly as the generic Allow button would.
      const edited = changed.length > 0 || (kind === 'draft' && isSend)
      setSubmitted(edited ? args : null)
      await decide(true, edited ? args : undefined)
    } catch (e) {
      setSubmitted(null)
      throw e
    } finally { setBusy(null) }
  }
  const discard = async (): Promise<void> => {
    if (busy) return
    setBusy('discard')
    try { await decide(false) } finally { setBusy(null) }
  }
  const revert = (): void => {
    const r = recipientsFromArg(original.to)
    setTo(r.ok); setBad(r.invalid); setSubject(String(original.subject ?? '')); setBody(String(original.body ?? ''))
  }
  const onKeyDown = (e: KeyboardEvent<HTMLElement>): void => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); void go(isSend ? 'send' : 'draft') }
  }

  if (!pending) return <ComposeResolved event={event} submitted={submitted} />

  const off = busy !== null
  return (
    <section className="mc mc-compose" aria-label={isSend ? 'Email to send' : 'Email draft'} onKeyDown={onKeyDown}>
      <header className="mc-head">
        <Mail size={14} />
        <b>{isSend ? 'New message' : 'Draft'}</b>
        <span className="mc-tag ask">{isSend ? 'waiting for you to send' : 'waiting for you to save'}</span>
        {changed.length > 0 && (
          <span className="mc-tag edited" title={`You changed: ${changed.join(', ')}`}><Pencil size={10} /> Edited
            <button type="button" className="mc-revert" onClick={revert} disabled={off}>Revert</button></span>
        )}
      </header>
      {replyId && <ReplyContext messageId={replyId} subject={subject} />}
      <div className="mc-fields">
        <div className="mc-row">
          <span className="mc-label">From</span>
          <span className="mc-from">{account ?? 'your Gmail account'}</span>
          <button type="button" className="mc-more" onClick={() => setShowMore((v) => !v)} aria-expanded={showMore}>
            <ChevronRight size={11} className={showMore ? 'rot90' : ''} /> Cc/Bcc
          </button>
        </div>
        <RecipientField label="To" chips={to} bad={bad} disabled={off} onSubmit={() => void go(isSend ? 'send' : 'draft')}
          onChange={(c, b) => { setTo(c); setBad(b) }} />
        {showMore && <p className="mc-note">Cc and Bcc are not supported by the send tool yet; add them as recipients in To.</p>}
        <div className="mc-row">
          <span className="mc-label">Subject</span>
          <input className="mc-subject" value={subject} disabled={off} aria-label="Subject" placeholder="(no subject)"
            onChange={(e) => setSubject(e.target.value.replace(/[\r\n]+/g, ' '))} />
        </div>
      </div>
      <textarea ref={bodyRef} className="mc-body" value={body} disabled={off} aria-label="Message body" spellCheck
        onChange={(e) => setBody(e.target.value)} />
      {attachments.length > 0 && (
        <div className="mc-attach">{attachments.map((a) => <span key={a} className="mc-chip"><Paperclip size={10} /> {a}</span>)}</div>
      )}
      {looksMarkdown(body) && (
        <div className="mc-hint">This looks like Markdown, and email shows it as plain text.
          <button type="button" className="mc-link-btn" disabled={off} onClick={() => setBody(markdownToPlain(body))}>Clean it up</button></div>
      )}
      {problem && (to.length > 0 || bad.length > 0 || changed.length > 0) && <div className="mc-problem" role="alert">{problem}</div>}
      {event.forced && <div className="mc-hint">Approval is required because this chat read content from outside.</div>}
      <footer className="mc-foot">
        <button type="button" className="mc-send" disabled={!!problem || off} onClick={() => void go(isSend ? 'send' : 'draft')}
          title={problem ?? (isSend ? 'Send (⌘↵)' : 'Save to Drafts (⌘↵)')}>
          {isSend ? <><Send size={13} /> {busy === 'send' ? 'Sending…' : 'Send'}</> : <><FileText size={13} /> {busy === 'draft' ? 'Saving…' : 'Save draft'}</>}
        </button>
        {isSend && (
          <button type="button" className="mc-btn" disabled={!!problem || off} onClick={() => void go('draft')} title="Write it to Drafts without sending">
            <FileText size={12} /> {busy === 'draft' ? 'Saving…' : 'Save as draft'}
          </button>
        )}
        <button type="button" className="mc-btn danger" disabled={off} onClick={() => void discard()}><Trash2 size={12} /> Discard</button>
        <span className="mc-kbd">⌘↵ {isSend ? 'send' : 'save'}</span>
      </footer>
      <Details event={event} />
    </section>
  )
}

/** The card once the person (or a plan, or a standing grant) has answered. Reads only persisted event data. */
function ComposeResolved({ event, submitted }: { event: ToolEvent; submitted: Record<string, unknown> | null }): JSX.Element {
  const isSend = event.name === 'gmail_send'
  const args = event.edited_arguments ?? submitted ?? event.arguments
  const result = useMemo(() => readPreview(event.result_preview), [event.result_preview])
  const denied = event.approval === 'deny' || (!!event.error && /declined by the user/.test(event.error))
  const running = Boolean(event.pending)
  const to = recipientsFromArg(args.to)
  const subject = String(args.subject ?? '')
  const body = String(args.body ?? '')
  const edited = event.edited_by === 'user' || (submitted !== null && !event.edited_arguments)
  const asDraft = result ? Boolean(result.draft_id) : Boolean(args.as_draft)
  const v = (result?.verification as Verification | undefined) ?? null

  let status: JSX.Element
  if (denied) {
    status = <Status tone="muted" icon={<Trash2 size={14} />} text="Discarded. Nothing was sent or saved." />
  } else if (running) {
    status = <Status tone="pending" icon={<Send size={14} />} text={asDraft ? 'Saving to Drafts…' : 'Queueing…'} />
  } else if (event.error) {
    status = <Status tone="bad" icon={<AlertTriangle size={14} />} text={asDraft || !isSend ? 'Draft not saved' : 'Not sent'} why={event.error} />
  } else if (result?.draft_id) {
    const ok = !v || v.status === 'verified'
    status = <Status tone={ok ? 'ok' : 'bad'} icon={ok ? <Check size={14} /> : <AlertTriangle size={14} />}
      text="Saved to Drafts" badge={<VerifiedBadge v={v} />}
      right={<a className="mc-link" href={gmailLink('drafts')} target="_blank" rel="noreferrer"><ExternalLink size={11} /> Open in Gmail</a>} />
  } else if (isSend && result?.queued) {
    status = <SendStatus id={String(result.queued)} immediate={null} />
  } else if (isSend && result && (result.status === 'sent' || result.sent)) {
    status = <SendStatus id={null} immediate={{ ...result, message_id: result.message_id ?? result.sent }} />
  } else {
    status = <Status tone="muted" icon={<Check size={14} />} text="Approved" />
  }

  return (
    <section className={`mc mc-resolved ${denied ? 'denied' : ''}`} aria-label={isSend ? 'Email' : 'Email draft'}>
      <header className="mc-head">
        <Mail size={14} />
        <b>{asDraft ? 'Draft' : 'Message'}</b>
        {edited && <span className="mc-tag edited" title="You changed the assistant's draft before approving"><Pencil size={10} /> Edited by you</span>}
        {event.plan && <span className="mc-tag" title={`Approved in the plan "${event.plan.title || 'untitled'}"`}>in plan</span>}
      </header>
      <dl className="mc-summary">
        <dt>To</dt><dd>{to.ok.map((r) => r.name || r.email).concat(to.invalid).join(', ') || '—'}</dd>
        <dt>Subject</dt><dd><b>{subject || '(no subject)'}</b></dd>
      </dl>
      <pre className="mc-preview">{body}</pre>
      {status}
      <Details event={event} />
    </section>
  )
}

// ---------------------------------------------------------------- search / read results

function MailRows({ rows, more, open }: { rows: MailRow[]; more: number; open: (r: MailRow) => void }): JSX.Element {
  if (!rows.length) return <p className="mc-empty">No messages matched.</p>
  return (
    <ul className="mc-list">
      {rows.map((r) => (
        <li key={r.id}>
          <button type="button" className={`mc-mail ${r.unread ? 'unread' : ''}`} onClick={() => open(r)}>
            <span className="mc-avatar" aria-hidden>{senderInitial(r.from)}</span>
            <span className="mc-mail-main">
              <span className="mc-mail-top"><span className="mc-from-name">{senderName(r.from)}</span><span className="mc-time">{shortTime(r.date)}</span></span>
              <span className="mc-mail-subject">{r.subject || '(no subject)'}</span>
              <span className="mc-mail-snippet">{r.snippet}</span>
            </span>
            {r.unread && <span className="mc-dot" aria-label="Unread" />}
          </button>
        </li>
      ))}
      {more > 0 && <li className="mc-more-rows">+{more} more not shown</li>}
    </ul>
  )
}

function ReadMessage({ m, link }: { m: { from: string; to: string; subject: string; date: string | null; body: string; clipped: boolean }; link?: string | null }): JSX.Element {
  return (
    <article className="mc-read">
      <h4 className="mc-read-subject">{m.subject || '(no subject)'}</h4>
      <div className="mc-read-meta">
        <span className="mc-avatar" aria-hidden>{senderInitial(m.from)}</span>
        <div>
          <div><b>{senderName(m.from)}</b> <span className="mc-faint">{m.from.includes('<') ? m.from.slice(m.from.indexOf('<')) : ''}</span></div>
          <div className="mc-faint">to {m.to || 'me'}{m.date ? ` · ${new Date(m.date).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}` : ''}</div>
        </div>
        {link && <a className="mc-link" href={link} target="_blank" rel="noreferrer"><ExternalLink size={11} /> Gmail</a>}
      </div>
      {/* Plain text on purpose: this is third-party content, never rendered as HTML. */}
      <pre className="mc-read-body">{m.body}{m.clipped ? '\n…' : ''}</pre>
    </article>
  )
}

function SearchCard({ event }: ToolCardProps): JSX.Element {
  const { rows, more } = useMemo(() => parseMailRows(event.result_preview), [event.result_preview])
  const [openRow, setOpenRow] = useState<MailRow | null>(null)
  const [full, setFull] = useState<ReturnType<typeof parseMailMessage>>(null)
  const query = String(event.arguments.query ?? '')

  useEffect(() => {
    if (!openRow) { setFull(null); return }
    let dead = false
    setFull({ id: openRow.id, from: openRow.from, to: '', subject: openRow.subject, date: openRow.date, body: openRow.snippet, clipped: true })
    api.google.gmailGet(openRow.id).then((m) => {
      if (!dead) setFull({ id: m.id, from: m.from ?? openRow.from, to: m.to ?? '', subject: m.subject ?? openRow.subject, date: m.date ?? openRow.date, body: m.body, clipped: false })
    }).catch(() => undefined)
    return () => { dead = true }
  }, [openRow])

  return (
    <section className="mc mc-results" aria-label="Mail search results">
      <header className="mc-head">
        {openRow ? <button type="button" className="mc-back" onClick={() => setOpenRow(null)}><ArrowLeft size={13} /> Results</button> : <><Mail size={14} /><b>Inbox</b></>}
        {!openRow && query && <span className="mc-query">{query}</span>}
        {!openRow && !event.pending && !event.error && <span className="mc-faint mc-count-label">{rows.length}{more ? `+${more}` : ''}</span>}
      </header>
      {event.pending ? <p className="mc-empty">Searching…</p>
        : event.error ? <p className="mc-problem">{event.error}</p>
        : openRow && full ? <ReadMessage m={full} link={gmailLink('message', openRow.id)} />
        : <MailRows rows={rows} more={more} open={setOpenRow} />}
      <Details event={event} />
    </section>
  )
}

function ReadCard({ event }: ToolCardProps): JSX.Element {
  const m = useMemo(() => parseMailMessage(event.result_preview), [event.result_preview])
  return (
    <section className="mc mc-results" aria-label="Email">
      <header className="mc-head"><Mail size={14} /><b>Message</b></header>
      {event.pending ? <p className="mc-empty">Reading…</p>
        : event.error ? <p className="mc-problem">{event.error}</p>
        : m ? <ReadMessage m={m} link={m.id ? gmailLink('message', m.id) : null} />
        : <p className="mc-empty">Nothing to show.</p>}
      <Details event={event} />
    </section>
  )
}

// ---------------------------------------------------------------- outbox tool

function OutboxCard({ event }: ToolCardProps): JSX.Element {
  const result = useMemo(() => readPreview(event.result_preview), [event.result_preview])
  const cancelled = result && typeof result.cancelled === 'string' ? result : null
  const waiting = result && Array.isArray(result.waiting) ? (result.waiting as { id: string; to: string; subject: string; sends_in_seconds: number }[]) : []
  return (
    <section className="mc mc-results" aria-label="Send queue">
      <header className="mc-head"><Send size={14} /><b>{cancelled ? 'Send cancelled' : 'Waiting to send'}</b></header>
      {event.pending ? <p className="mc-empty">Checking…</p>
        : event.error ? <p className="mc-problem">{event.error}</p>
        : cancelled ? <Status tone="muted" icon={<Undo2 size={14} />} text={<>Undone: <b>{String(cancelled.subject || '(no subject)')}</b> to {String(cancelled.to ?? '')} was never sent.</>} />
        : waiting.length === 0 ? <p className="mc-empty">Nothing is waiting to send.</p>
        : <ul className="mc-list">{waiting.map((w) => (
            <li key={w.id} className="mc-queue-row"><Send size={12} /> <b>{w.subject || '(no subject)'}</b> <span className="mc-faint">to {w.to} · in {clock(w.sends_in_seconds)}</span></li>
          ))}</ul>}
      <Details event={event} />
    </section>
  )
}

registerToolCard('gmail_send', ComposeCard)
registerToolCard('gmail_draft', ComposeCard)
registerToolCard('gmail_outbox', OutboxCard)
registerToolCard('gmail_search', SearchCard)
registerToolCard('gmail_read', ReadCard)
