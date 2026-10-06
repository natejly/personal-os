import { useEffect, useState } from 'react'
import { X } from 'lucide-react'
import type { Conversation, IMessageStatus, Settings } from '@shared/types'
import { api } from '../lib/api'
import { ageLabel } from '../lib/runningViews'
import { formatHandle, normalizeHandle } from '../lib/imessageHandles'

function statusLine(st: IMessageStatus | null): string {
  if (!st) return 'Checking…'
  switch (st.status) {
    case 'running': return 'Watching Messages'
    case 'needs_full_disk_access': return 'Needs Full Disk Access'
    case 'locked': return 'Another Grain backend is watching Messages'
    case 'error': return st.last_error ? `Error: ${st.last_error}` : 'Error'
    default: return 'Off'
  }
}

/**
 * Settings > Texting: control Grain by text from an allowlist of numbers. The toggle, handles, target chat and
 * notify option go through the modal's draft and Save; the status line and the test text act at once. `saved` is
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

  const load = (): void => { api.imessageStatus().then((s) => { setSt(s); setStErr(null) }).catch((e: Error) => setStErr(e.message)) }
  useEffect(() => {
    load()
    api.conversations.list('all').then((l) => setConvs(l.filter((c) => !c.archived_at && !c.settings?.deskId && !c.settings?.job_id).sort((a, b) => b.updated_at - a.updated_at).slice(0, 30))).catch(() => undefined)
  }, [])
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
      setTestMsg(r.ok ? 'Sent' : r.error ?? 'Could not send')
    } catch (e) { setTestMsg((e as Error).message) } finally { setTesting(false) }
  }
  const openFda = (): void => { void api.imessageOpenFda().catch(() => undefined) }

  const savedHandles = saved.imessageHandles ?? []
  const canTest = !!saved.imessageEnabled && savedHandles.length > 0 && !testing
  const unsaved = enabled !== !!saved.imessageEnabled || handles.join('|') !== savedHandles.join('|')
  const target = draft.imessageConversationId ?? ''

  return (
    <div className="workspace-roots">
      <h4>Texting (iMessage)</h4>
      <label className="toggle-row plain">
        <span className="toggle-text"><b>Control Grain by text</b><small>Text a message from an allowed number and Grain answers in the same thread.</small></span>
        <input type="checkbox" checked={enabled} onChange={(e) => patch({ imessageEnabled: e.target.checked })} /><span className="switch" />
      </label>
      <p className="muted small">Only works while your Mac is awake and Grain is running. Grain also needs permission to control Messages, and macOS asks the first time it sends.</p>

      <span><b>Allowed numbers and emails</b></span>
      <p className="muted small">Only these can control Grain. Texts from anyone else are ignored silently.</p>
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

      <label><span>Conversation for texts</span>
        <select value={target} onChange={(e) => patch({ imessageConversationId: e.target.value || null })}>
          <option value="">Texts (dedicated chat)</option>
          {target && !convs.some((c) => c.id === target) && <option value={target}>{st?.target_conversation?.id === target ? st.target_conversation.title : 'Selected chat'}</option>}
          {convs.map((c) => <option key={c.id} value={c.id}>{c.title || 'Untitled'}</option>)}
        </select>
      </label>

      <label className="toggle-row plain">
        <span className="toggle-text"><b>Text me when long runs finish or need approval</b><small>Sends a text when a run you started finishes or is waiting on you.</small></span>
        <input type="checkbox" checked={!!draft.imessageNotifyLongRuns} onChange={(e) => patch({ imessageNotifyLongRuns: e.target.checked })} /><span className="switch" />
      </label>

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
