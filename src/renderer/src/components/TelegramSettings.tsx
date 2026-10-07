import { useEffect, useState } from 'react'
import { Copy, ExternalLink } from 'lucide-react'
import type { Settings, TelegramStatus } from '@shared/types'
import { api } from '../lib/api'

type Tone = '' | 'needs-you' | 'working' | 'done' | 'failed'

function statusView(st: TelegramStatus | null): { tone: Tone; text: string } {
  if (!st) return { tone: '', text: 'Checking…' }
  switch (st.status) {
    case 'connected': return { tone: 'done', text: st.owner_name ? `Connected to ${st.owner_name}` : 'Connected' }
    case 'not_paired': return { tone: 'needs-you', text: 'Not paired. Send the code below to your bot.' }
    case 'error': return { tone: 'failed', text: st.last_error ? `Polling error: ${st.last_error}` : 'Polling error' }
    case 'bad_token': return { tone: 'failed', text: 'Telegram rejected the token. Paste a new one.' }
    case 'conflict': return { tone: 'failed', text: 'Another program is reading this bot\'s updates, or a webhook is set on it.' }
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
 * Settings > Texting: control Grain from a private chat with your own Telegram bot. The token, the on/off switch, pairing, the test
 * message and the unpair/remove buttons act at once; the long-run notice goes through the modal's draft and Save.
 */
export default function TelegramSettings({ draft, patch }: { draft: Settings; patch: (p: Partial<Settings>) => void }): JSX.Element {
  const [st, setSt] = useState<TelegramStatus | null>(null)
  const [stErr, setStErr] = useState<string | null>(null)
  const [token, setToken] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

  const load = (): void => { api.telegramStatus().then((s) => { setSt(s); setStErr(null) }).catch((e: Error) => setStErr(e.message)) }
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

  const view = statusView(st)
  const pairing = st?.pairing ?? null
  const hasToken = !!st?.has_token

  return (
    <div className="workspace-roots">
      <h4>Telegram</h4>
      <p className="muted small">Text Grain from your phone through a private bot that only you can use. Works while your Mac is awake and Grain is running.</p>
      <ol className="muted small">
        <li>In Telegram, open @BotFather and send /newbot.</li>
        <li>Pick a name, then a username that ends in "bot".</li>
        <li>Copy the token BotFather sends and paste it here.</li>
      </ol>
      <div className="workspace-roots-add">
        <input type="password" value={token} autoComplete="off" spellCheck={false} aria-label="Bot token" placeholder={hasToken ? 'Saved. Paste a new token to replace' : '123456789:ABC…'}
          onChange={(e) => setToken(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && token.trim()) { e.preventDefault(); void saveToken() } }} />
        <button type="button" className="ghost-btn" disabled={busy || !token.trim()} onClick={() => void saveToken()}>{busy ? 'Saving…' : 'Save token'}</button>
      </div>
      {st?.bot_username && <p className="muted small">Bot: <code>@{st.bot_username}</code></p>}

      <label className="toggle-row plain">
        <span className="toggle-text"><b>Control Grain by Telegram</b><small>Grain answers in your chat with the bot.</small></span>
        <input type="checkbox" checked={!!st?.enabled} disabled={busy || !hasToken} onChange={(e) => void run(() => api.telegramSetEnabled(e.target.checked))} /><span className="switch" />
      </label>

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
              <p className="muted small">Open this link in Telegram and press Start, or send <code>/start {pairing.code}</code> to the bot.</p>
              <div className="workspace-roots-add">
                <code>{pairing.code}</code>
                <button type="button" className="ghost-btn" onClick={() => copy(pairing.code)}><Copy size={12} /> {copied ? 'Copied' : 'Copy'}</button>
                <a className="ghost-btn" href={pairing.link} target="_blank" rel="noreferrer"><ExternalLink size={12} /> Open in Telegram</a>
              </div>
              <p className="muted small">{expiryLabel(pairing.expires_at)}. The code works once.</p>
            </>
          ) : <p className="muted small">The code has expired.</p>}
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

      <label className="toggle-row plain">
        <span className="toggle-text"><b>Message me when long runs finish or need approval</b><small>Covers chat runs you started in the app, once they take at least this long.</small></span>
        <input type="checkbox" checked={!!draft.telegramNotifyLongRuns} onChange={(e) => patch({ telegramNotifyLongRuns: e.target.checked })} /><span className="switch" />
      </label>
      {draft.telegramNotifyLongRuns && (
        <label><span>Minutes before a run counts as long</span>
          <input type="number" min={1} max={1440} value={draft.telegramLongRunMinutes ?? 3}
            onChange={(e) => patch({ telegramLongRunMinutes: Math.min(1440, Math.max(1, Math.round(Number(e.target.value) || 1))) })} />
        </label>
      )}

      <label className="toggle-row plain">
        <span className="toggle-text"><b>Send worker results to Telegram</b><small>When a background worker finishes, the assistant's reply to it is also sent to your chat.</small></span>
        <input type="checkbox" checked={!!draft.telegramPushWorkerResults} onChange={(e) => patch({ telegramPushWorkerResults: e.target.checked })} /><span className="switch" />
      </label>
      <p className="muted small">Replies, worker results and progress updates can include screenshots and files; photos you send the bot reach the chat.</p>

      <div className="workspace-roots-add">
        <button type="button" className="ghost-btn" disabled={busy || !st?.paired} onClick={() => void test()}>Send test message</button>
        <button type="button" className="ghost-btn" disabled={busy || !hasToken} onClick={removeToken}>Remove token</button>
        {msg && <span className="muted small" role="status">{msg}</span>}
      </div>
    </div>
  )
}
