import { useEffect, useState } from 'react'
import { X } from 'lucide-react'
import type { Conversation, IMessageSelfChat, IMessageStatus, Settings } from '@shared/types'
import { api } from '../lib/api'
import { ageLabel } from '../lib/runningViews'
import { DEFAULT_REPLY_MARKER, formatHandle, normalizeHandle, replyMarker } from '../lib/imessageHandles'

const TEST_ERRORS: Record<string, string> = {
  send_failed: 'Could not send. Check that Messages is signed in and Grain may control it (Privacy & Security > Automation).',
  paused_loop_guard: 'Texting is paused by the loop guard. Turn it off and on first.'
}

function statusLine(st: IMessageStatus | null): string {
  if (!st) return 'Checking…'
  switch (st.status) {
    case 'running': return 'Watching Messages'
    case 'needs_full_disk_access': return 'Needs Full Disk Access'
    case 'paused_loop_guard': return 'Paused: Grain saw a burst of messages that looked like a reply loop. Turn texting off and on to resume.'
    case 'locked': return 'Another Grain backend is watching Messages'
    case 'error': return st.last_error ? `Error: ${st.last_error}` : 'Error'
    default: return 'Off'
  }
}

/**
 * Settings > Integrations: control Grain by texting your own note-to-self thread. The toggle, addresses, self chat, reply marker, target chat and
 * long-run options go through the modal's draft and Save; the status line and the test text act at once. `saved` is
 * what the backend has now, which is what a test text uses.
 */
export default function IMessageSettings({ draft, saved, patch }: { draft: Settings; saved: Settings; patch: (p: Partial<Settings>) => void }): JSX.Element {
  const enabled = !!draft.imessageEnabled
  const handles = draft.imessageHandles ?? []
  const [text, setText] = useState('')
  const [handleError, setHandleError] = useState<string | null>(null)
  const [st, setSt] = useState<IMessageStatus | null>(null)
  const [stErr, setStErr] = useState<string | null>(null)
  const [convs, setConvs] = useState<Conversation[]>([])
  const [testing, setTesting] = useState(false)
  const [testMsg, setTestMsg] = useState<string | null>(null)
  const [chats, setChats] = useState<IMessageSelfChat[] | null>(null)
  const [chatErr, setChatErr] = useState<string | null>(null)
  const [detecting, setDetecting] = useState(false)

  const load = (): void => { api.imessageStatus().then((s) => { setSt(s); setStErr(null) }).catch((e: Error) => setStErr(e.message)) }
  useEffect(() => {
    load()
    api.conversations.list('all').then((l) => setConvs(l.filter((c) => !c.archived_at && !c.settings?.deskId && !c.settings?.job_id).sort((a, b) => b.updated_at - a.updated_at).slice(0, 30))).catch(() => undefined)
  }, [])
  const detect = (): void => {
    setDetecting(true)
    api.imessageSelfChats().then((r) => { setChats(r.chats); setChatErr(null) }).catch((e: Error) => setChatErr(e.message)).finally(() => setDetecting(false))
  }
  useEffect(() => { if (enabled && chats === null) detect() }, [enabled])
  // Poll gently while the card is mounted and texting is on.
  useEffect(() => {
    if (!enabled) return
    const t = setInterval(load, 10_000)
    return () => clearInterval(t)
  }, [enabled])

  const add = (): void => {
    if (!text.trim()) return
    const h = normalizeHandle(text)
    if (!h) return setHandleError('Enter a phone number (like +1 555 123 4567) or an email address.')
    setHandleError(null)
    setText('')
    if (!handles.includes(h)) patch({ imessageHandles: [...handles, h] })
  }
  const test = async (): Promise<void> => {
    setTesting(true)
    setTestMsg(null)
    try {
      const r = await api.imessageTest()
      setTestMsg(r.ok ? (r.to === 'self_chat' ? 'Sent to your self chat' : 'Sent (no self chat picked — went to your first address)') : TEST_ERRORS[r.error ?? ''] ?? r.error ?? 'Could not send')
    } catch (e) { setTestMsg((e as Error).message) } finally { setTesting(false) }
  }
  const openFda = (): void => { void api.imessageOpenFda().catch(() => undefined) }

  const savedHandles = saved.imessageHandles ?? []
  const canTest = !!saved.imessageEnabled && (!!saved.imessageSelfChatGuid || savedHandles.length > 0) && !testing
  const selfChat = draft.imessageSelfChatGuid ?? ''
  const marker = draft.imessageReplyMarker ?? DEFAULT_REPLY_MARKER
  const unsaved = enabled !== !!saved.imessageEnabled || handles.join('|') !== savedHandles.join('|')
    || selfChat !== (saved.imessageSelfChatGuid ?? '') || replyMarker(marker) !== replyMarker(saved.imessageReplyMarker)
  const chatLabel = (c: IMessageSelfChat): string => `${c.handle}${c.last_activity ? ` · ${ageLabel(c.last_activity)} ago` : ''}${c.best ? ' (suggested)' : ''}`
  const target = draft.imessageConversationId ?? ''
  const longRun = Math.min(1440, Math.max(1, draft.imessageLongRunMinutes ?? 3))

  return (
    <div className="workspace-roots">
      <h4>Texting (iMessage)</h4>
      <label className="toggle-row plain">
        <span className="toggle-text"><b>Control Grain by text</b><small>Text yourself from your iPhone. Grain answers in that same thread.</small></span>
        <input type="checkbox" checked={enabled} onChange={(e) => patch({ imessageEnabled: e.target.checked })} /><span className="switch" />
      </label>
      <p className="muted small">Only works while your Mac is awake and Grain is running. Grain also needs permission to control Messages, and macOS asks the first time it sends.</p>

      <span><b>Your addresses</b></span>
      <p className="muted small">Your own phone number and Apple ID email. Texts from other numbers you add here also work; everyone else is ignored.</p>
      {handles.length > 0 && (
        <ul className="plain-list">
          {handles.map((h) => (
            <li key={h}>
              <code title={h}>{formatHandle(h)}</code>
              <button type="button" className="icon-btn sm" aria-label={`Remove ${formatHandle(h)}`} title="Remove" onClick={() => patch({ imessageHandles: handles.filter((x) => x !== h) })}><X size={12} /></button>
            </li>
          ))}
        </ul>
      )}
      <div className="workspace-roots-add">
        <input value={text} placeholder="+1 555 123 4567 or you@example.com" spellCheck={false} aria-label="Phone number or email to allow" aria-invalid={!!handleError}
          onChange={(e) => { setText(e.target.value); setHandleError(null) }} onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); add() } }} />
        <button type="button" className="ghost-btn" onClick={add} disabled={!text.trim()}>Add</button>
      </div>
      {handleError && <p className="muted small" role="alert">{handleError}</p>}

      <label><span>Self chat</span>
        <select value={selfChat} onChange={(e) => patch({ imessageSelfChatGuid: e.target.value || null })}>
          <option value="">None — pick your self chat</option>
          {selfChat && !chats?.some((c) => c.guid === selfChat) && <option value={selfChat}>Saved chat</option>}
          {chats?.map((c) => <option key={c.guid} value={c.guid}>{chatLabel(c)}</option>)}
        </select>
      </label>
      <div className="workspace-roots-add">
        <button type="button" className="ghost-btn" onClick={detect} disabled={detecting}>{detecting ? 'Detecting…' : 'Detect'}</button>
        {(chatErr || (chats && chats.length === 0)) && <span className="muted small" role="status">{chatErr === 'needs_full_disk_access' ? 'Grain needs Full Disk Access to look for your self chat.' : chatErr ?? 'No self chat found — add your own number or email above, Save, then text yourself once from your iPhone.'}</span>}
      </div>

      <label><span>Reply marker</span>
        <input value={marker} maxLength={16} spellCheck={false} onChange={(e) => patch({ imessageReplyMarker: e.target.value })}
          onBlur={() => { if (!marker.trim()) patch({ imessageReplyMarker: DEFAULT_REPLY_MARKER }) }} />
      </label>
      <p className="muted small">Every text Grain sends starts with this, so Grain never reads its own replies as commands.</p>

      <label><span>Conversation for texts</span>
        <select value={target} onChange={(e) => patch({ imessageConversationId: e.target.value || null })}>
          <option value="">Texts (dedicated chat)</option>
          {target && !convs.some((c) => c.id === target) && <option value={target}>{st?.target_conversation?.id === target ? st.target_conversation.title : 'Selected chat'}</option>}
          {convs.map((c) => <option key={c.id} value={c.id}>{c.title || 'Untitled'}</option>)}
        </select>
      </label>

      <label className="toggle-row plain">
        <span className="toggle-text"><b>Also text me when long runs finish or need approval</b></span>
        <input type="checkbox" checked={!!draft.imessageNotifyLongRuns} onChange={(e) => patch({ imessageNotifyLongRuns: e.target.checked })} /><span className="switch" />
      </label>
      {draft.imessageNotifyLongRuns && (
        <label className="setting-row"><span className="toggle-text"><b>Runs longer than</b></span>
          <span className="num-unit">
            <input type="number" min={1} max={1440} value={longRun} onChange={(e) => patch({ imessageLongRunMinutes: Math.min(1440, Math.max(1, Math.round(Number(e.target.value)) || 1)) })} />
            <em>minutes</em>
          </span>
        </label>
      )}

      <p className="muted small" role="status">
        <b>{stErr ?? statusLine(st)}</b>
        {st?.status === 'needs_full_disk_access' && <>{' '}<button type="button" className="link small" onClick={openFda}>Open Privacy settings</button></>}
        {' '}<button type="button" className="link small" onClick={load}>Re-check</button>
        {st && st.ignored_count > 0 && <><br />{st.ignored_count} {st.ignored_count === 1 ? 'text' : 'texts'} from unknown senders ignored{st.last_ignored_at ? `, last ${ageLabel(st.last_ignored_at)} ago` : ''}.</>}
      </p>
      <div className="workspace-roots-add">
        <button type="button" className="ghost-btn" disabled={!canTest} onClick={() => void test()}>{testing ? 'Sending…' : 'Send test text'}</button>
        {testMsg && <span className="muted small" role="status">{testMsg}</span>}
      </div>
      {unsaved && <p className="muted small">Save your changes first; the test text uses what is saved.</p>}
    </div>
  )
}
