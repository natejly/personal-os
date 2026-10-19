import { useEffect, useState } from 'react'
import { Copy, ExternalLink } from '../icons'
import type { Settings, TelegramStatus } from '@shared/types'
import { api } from '../lib/api'
import { ApiError } from '../lib/apiError'
import NumberField from './settings/NumberField'
import { Sec } from './settings/shared'
import Switch from './settings/Switch'

type Tone = '' | 'needs-you' | 'working' | 'done' | 'failed'

function statusView(st: TelegramStatus | null): { tone: Tone; text: string } {
  if (!st) return { tone: '', text: 'Checking…' }
  switch (st.status) {
    case 'connected': return { tone: 'done', text: st.owner_name ? `Connected to ${st.owner_name}` : 'Connected' }
    case 'not_paired': return { tone: 'needs-you', text: 'Not paired' }
    case 'error': return { tone: 'failed', text: st.last_error ? `Polling error: ${st.last_error}` : 'Polling error' }
    case 'bad_token': return { tone: 'failed', text: 'Telegram rejected the token. Paste a new one.' }
    case 'conflict': return { tone: 'failed', text: 'Something else is reading this bot\'s updates: a webhook, another program, or a Grain on another Mac that does not know about this one. Give each Mac its own bot.' }
    case 'claimed_elsewhere': return { tone: 'needs-you', text: st.claimed_by ? `This bot is used by Grain on ${st.claimed_by}.` : 'This bot is used by Grain on another Mac.' }
    case 'locked': return { tone: 'working', text: 'Another Grain is polling this bot' }
    case 'no_token': return { tone: '', text: 'No bot token yet' }
    default: return { tone: '', text: 'Off' }
  }
}

function expiryLabel(expiresAt: number): string {
  const mins = Math.ceil((expiresAt - Date.now() / 1000) / 60)
  return mins > 0 ? `Expires in ${mins} min` : 'Expired'
}

/**
 * Settings > Telegram bot: control Grain from a private chat with your own Telegram bot. The token, the on/off switch, pairing, the test
 * message and the unpair/remove buttons act at once; the long-run notice goes through the modal's draft and Save.
 */
export default function TelegramSettings({ draft, patch }: { draft: Settings; patch: (p: Partial<Settings>) => void }): JSX.Element | null {
  const [st, setSt] = useState<TelegramStatus | null>(null)
  const [stErr, setStErr] = useState<string | null>(null)
  const [off, setOff] = useState(false)  // the extension is turned off: its routes answer 404
  const [token, setToken] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

  const load = (): void => {
    api.telegramStatus().then((s) => { setSt(s); setStErr(null); setOff(false) })
      .catch((e: Error) => { if (e instanceof ApiError && e.status === 404) setOff(true); else setStErr(e.message) })
  }
  const unpaired = !!st?.has_token && !st.paired
  useEffect(() => {
    load()
    const t = setInterval(load, unpaired ? 5_000 : 15_000)
    return () => clearInterval(t)
  }, [unpaired])

  const run = async (fn: () => Promise<TelegramStatus>): Promise<boolean> => {
    setBusy(true)
    setMsg(null)
    try { setSt(await fn()); setStErr(null); return true } catch (e) { setMsg((e as Error).message); return false } finally { setBusy(false) }
  }
  const saveToken = async (): Promise<void> => { if (await run(() => api.telegramSaveToken(token.trim()))) setToken('') }
  const removeToken = (): void => { if (window.confirm('Remove the bot token and unpair this chat?')) void run(api.telegramRemoveToken) }
  const takeOver = (): void => { if (window.confirm(`Take this bot over from Grain on ${st?.claimed_by ?? 'the other Mac'}? That Mac stops receiving its messages.`)) void run(api.telegramTakeOver) }
  const test = async (): Promise<void> => {
    setBusy(true)
    setMsg(null)
    try {
      const r = await api.telegramTest()
      setMsg(r.ok ? 'Sent. Check Telegram.' : r.error ?? 'Could not send')
    } catch (e) { setMsg((e as Error).message) } finally { setBusy(false) }
  }
  const copy = (code: string): void => {
    void navigator.clipboard.writeText(code).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500) }).catch(() => undefined)
  }

  if (off) return null
  const view = statusView(st)
  const pairing = st?.pairing ?? null
  const hasToken = !!st?.has_token

  return (
    <Sec id="texting">
      <h4>Telegram bot</h4>
      <div className="workspace-roots">
        {st?.status === 'claimed_elsewhere' && (
          <div className="workspace-roots-add">
            <button type="button" className="ghost-btn" disabled={busy} onClick={takeOver}>Take over this bot</button>
          </div>
        )}
        <div className="workspace-roots-add">
          <input type="password" value={token} autoComplete="off" spellCheck={false} aria-label="Bot token" title="Send /newbot to @BotFather in Telegram and paste the token it gives you. Each Mac needs its own bot" placeholder={hasToken ? 'Saved. Paste a new token to replace' : '123456789:ABC…'}
            onChange={(e) => setToken(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && token.trim()) { e.preventDefault(); void saveToken() } }} />
          <button type="button" className="ghost-btn" disabled={busy || !token.trim()} onClick={() => void saveToken()}>{busy ? 'Saving…' : 'Save token'}</button>
        </div>
        {st?.bot_username && <p className="muted small">Bot: <code>@{st.bot_username}</code></p>}
  
        <Switch title="Control Grain by Telegram" checked={!!st?.enabled} disabled={busy || !hasToken} onChange={(v) => void run(() => api.telegramSetEnabled(v))} />
  
        <p className="muted small" role="status" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span className={`inbox-dot ${view.tone}`} aria-hidden />
          <b>{stErr ?? view.text}</b>
          <button type="button" className="link small" onClick={load}>Re-check</button>
        </p>
  
        {unpaired && st?.enabled && (
          <div className="workspace-roots">
            <span><b>Pair your chat</b></span>
            {pairing ? (
              <>
                <div className="workspace-roots-add">
                  <code title={`Or send /start ${pairing.code} to the bot`}>{pairing.code}</code>
                  <button type="button" className="ghost-btn" onClick={() => copy(pairing.code)}><Copy size={12} /> {copied ? 'Copied' : 'Copy'}</button>
                  <a className="ghost-btn" href={pairing.link} target="_blank" rel="noreferrer"><ExternalLink size={12} /> Open in Telegram</a>
                </div>
                <p className="muted small">{expiryLabel(pairing.expires_at)}</p>
              </>
            ) : <p className="muted small">Code expired</p>}
            <div className="workspace-roots-add">
              <button type="button" className="ghost-btn" disabled={busy} onClick={() => void run(api.telegramNewCode)}>New code</button>
            </div>
          </div>
        )}
  
        {st?.paired && (
          <div className="workspace-roots-add">
            <span className="muted small">Paired with <b>{st.owner_name ?? 'your chat'}</b></span>
            <button type="button" className="ghost-btn" disabled={busy} onClick={() => void run(api.telegramUnpair)}>Unpair</button>
          </div>
        )}
  
        <Switch title="Message me when long runs finish or need approval" checked={!!draft.telegramNotifyLongRuns} onChange={(v) => patch({ telegramNotifyLongRuns: v })} />
        {draft.telegramNotifyLongRuns && (
          <label><span>Minutes before a run counts as long</span>
            <NumberField name="telegramLongRunMinutes" value={draft.telegramLongRunMinutes ?? 3} fallback={1} onChange={(telegramLongRunMinutes) => patch({ telegramLongRunMinutes })} />
          </label>
        )}
  
        <Switch title="Send worker results to Telegram" checked={!!draft.telegramPushWorkerResults} onChange={(v) => patch({ telegramPushWorkerResults: v })} />
  
        <div className="workspace-roots-add">
          <button type="button" className="ghost-btn" disabled={busy || !st?.paired} onClick={() => void test()}>Send test message</button>
          <button type="button" className="ghost-btn" disabled={busy || !hasToken} onClick={removeToken}>Remove token</button>
          {msg && <span className="muted small" role="status">{msg}</span>}
        </div>
      </div>
    </Sec>
  )
}
