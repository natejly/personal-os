import { useEffect, useRef, useState } from 'react'
import { Unplug, Check, AlertTriangle, RefreshCw, ExternalLink, Upload } from 'lucide-react'
import { useStore } from '../store'
import { api } from '../lib/api'
import type { GoogleTaskList } from '@shared/types'

/** Console pages, in the order the setup walks through them. */
const CONSOLE = {
  project: 'https://console.cloud.google.com/projectcreate',
  apis: [
    ['Calendar API', 'https://console.cloud.google.com/apis/library/calendar-json.googleapis.com'],
    ['Gmail API', 'https://console.cloud.google.com/apis/library/gmail.googleapis.com'],
    ['Tasks API', 'https://console.cloud.google.com/apis/library/tasks.googleapis.com'],
    ['Drive API', 'https://console.cloud.google.com/apis/library/drive.googleapis.com'],
    ['Docs API', 'https://console.cloud.google.com/apis/library/docs.googleapis.com'],
    ['Sheets API', 'https://console.cloud.google.com/apis/library/sheets.googleapis.com']
  ],
  consent: 'https://console.cloud.google.com/apis/credentials/consent',
  client: 'https://console.cloud.google.com/apis/credentials'
} as const

/** Opens in the real browser: the main process turns window.open into shell.openExternal. */
const openExternal = (url: string): void => void window.open(url, '_blank')

export default function GoogleSettings({ clientId, clientSecret, secretSaved = false, onChange, onSaveCreds }: { clientId: string; clientSecret: string; secretSaved?: boolean; onChange: (p: { googleClientId?: string; googleClientSecret?: string }) => void; onSaveCreds: () => Promise<void> }): JSX.Element {
  const google = useStore((s) => s.google)
  const tasksSync = useStore((s) => s.tasksSync)
  const { refreshGoogle, connectGoogle, disconnectGoogle, setTasksSync, runTasksSync, toast } = useStore()
  const [setupOpen, setSetupOpen] = useState(false)
  const [taskLists, setTaskLists] = useState<GoogleTaskList[]>([])
  const idRef = useRef<HTMLInputElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  useEffect(() => { void refreshGoogle() }, [refreshGoogle])
  // The list picker only matters once sync is on; fetch lazily so a plain settings open costs nothing.
  const syncEnabled = !!tasksSync?.config.enabled
  useEffect(() => {
    if (google?.connected && syncEnabled) void api.google.tasklists().then(setTaskLists).catch(() => undefined)
  }, [google?.connected, syncEnabled])

  // Google only runs a sign-in flow on behalf of a registered app, so there has to be an
  // OAuth client before the button can do anything: from .env, or pasted here.
  const draftHasClient = !!clientId && !!clientSecret
  const hasClient = draftHasClient || !!google?.configured

  const signIn = async (): Promise<void> => {
    if (!hasClient) {
      // Nothing to sign in with yet — walk them through making one instead of doing nothing.
      setSetupOpen(true)
      requestAnimationFrame(() => idRef.current?.scrollIntoView({ block: 'center', behavior: 'smooth' }))
      return
    }
    if (clientId !== '' || clientSecret !== '') await onSaveCreds()
    await connectGoogle()
  }

  /** The console hands you a client_secret_*.json; accept it whole so nothing has to be retyped. */
  const applyClientJson = (text: string): boolean => {
    try {
      const j = JSON.parse(text)
      const c = j.installed ?? j.web ?? j
      if (typeof c?.client_id !== 'string') return false
      onChange({ googleClientId: c.client_id, googleClientSecret: typeof c.client_secret === 'string' ? c.client_secret : '' })
      if (j.web) toast('That is a Web application client — create a Desktop app client instead', 'error')
      else toast('Client loaded — press Sign in with Google')
      return true
    } catch {
      return false
    }
  }

  const onPasteMaybeJson = (e: React.ClipboardEvent<HTMLInputElement>): void => {
    const text = e.clipboardData.getData('text')
    if (text.trim().startsWith('{') && applyClientJson(text)) e.preventDefault()
  }

  const loadFile = (f: File | undefined): void => {
    if (!f) return
    void f.text().then((t) => {
      if (!applyClientJson(t)) toast('That file is not a Google OAuth client JSON', 'error')
    })
  }

  const needsReauth = !!google?.connected && google.needs_reauth

  return (
    <div className="integration">
      <div className="integration-head">
        <span className="g-logo">G</span>
        <div>
          <b>Google Workspace</b>
          <small>
            {google?.connected
              ? <><Check size={11} /> Signed in as {google.email}</>
              : hasClient
                ? 'Sign in to let the app read your Calendar, Gmail, Tasks, Drive, Docs and Sheets. Signing in turns on two-way Todos ↔ Google Tasks sync, which can be switched off here in Settings → Integrations.'
                : 'Needs a one-time OAuth client (about two minutes) before the first sign-in.'}
          </small>
        </div>
        {google?.connected ? (
          <>
            {needsReauth && <button className="primary-btn" onClick={() => void signIn()}><RefreshCw size={13} /> Reconnect</button>}
            <button className="ghost-btn" onClick={() => void disconnectGoogle()}><Unplug size={13} /> Sign out</button>
          </>
        ) : (
          <button className="primary-btn" onClick={() => void signIn()} title={hasClient ? 'Opens Google sign-in in your browser' : 'Shows the one-time OAuth client setup'}>
            <span className="g-logo g-logo-sm">G</span> {hasClient ? 'Sign in with Google' : 'Set up Google sign-in'}
          </button>
        )}
      </div>

      {needsReauth && (
        <p className="integration-warn"><AlertTriangle size={13} /> {google?.reauth_reason ?? 'This connection needs to be renewed.'} Click Reconnect to sign in again.</p>
      )}

      {google?.connected && (
        <div className="tasks-sync">
          <label className="toggle-row plain">
            <span className="toggle-text"><b>Sync Todos with Google Tasks</b><small>Two-way: a change on either side shows up on the other.</small></span>
            <input type="checkbox" checked={syncEnabled} disabled={!tasksSync} onChange={(e) => void setTasksSync({ enabled: e.target.checked })} /><span className="switch" />
          </label>
          {syncEnabled && tasksSync && (
            <div className="tasks-sync-row">
              <select aria-label="Google Tasks list to sync with" value={tasksSync.config.tasklist} onChange={(e) => void setTasksSync({ tasklist: e.target.value })} title="Which Google Tasks list to sync with">
                <option value="@default">Default list</option>
                {taskLists.map((l) => <option key={l.id} value={l.id}>{l.title}</option>)}
              </select>
              <button className="ghost-btn" onClick={() => void runTasksSync()} disabled={tasksSync.syncing}>
                <RefreshCw size={13} className={tasksSync.syncing ? 'spin' : ''} /> {tasksSync.syncing ? 'Syncing…' : 'Sync now'}
              </button>
              <small className="muted">
                {tasksSync.last_error
                  ? `Last sync failed: ${tasksSync.last_error}`
                  : tasksSync.last_sync
                    ? `Synced ${new Date(tasksSync.last_sync * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}`
                    : 'Not synced yet — press Sync now'}
              </small>
            </div>
          )}
        </div>
      )}

      {!google?.connected && (
        <details className="setup" open={setupOpen || !hasClient} onToggle={(e) => setSetupOpen((e.target as HTMLDetailsElement).open)}>
          <summary>{hasClient ? (google?.source === 'settings' ? 'Using your own OAuth client' : 'Advanced: use your own OAuth client') : 'One-time setup: create an OAuth client'}</summary>

          {!hasClient && (
            <p className="muted small">
              Google will only run a sign-in flow on behalf of a registered app, so Grain needs an OAuth
              client of its own. There is no way around that, but you do it once and it stays on your account.
            </p>
          )}

          <ol className="setup-steps muted small">
            <li>
              Create or pick a Google Cloud project.
              <button className="link-btn" onClick={() => openExternal(CONSOLE.project)}><ExternalLink size={11} /> New project</button>
            </li>
            <li>
              Enable the APIs the app calls:
              {CONSOLE.apis.map(([label, url]) => (
                <button key={url} className="link-btn" onClick={() => openExternal(url)}><ExternalLink size={11} /> {label}</button>
              ))}
            </li>
            <li>
              Fill in the OAuth consent screen (<b>External</b>), then add your own Google account under
              <b> Test users</b> — Google blocks the sign-in otherwise.
              <button className="link-btn" onClick={() => openExternal(CONSOLE.consent)}><ExternalLink size={11} /> Consent screen</button>
            </li>
            <li>
              Create credentials → OAuth client ID → application type <b>Desktop app</b>, then download its JSON.
              A <i>Web application</i> client cannot work here: the callback lands on a loopback port that changes
              every launch, and only Desktop clients may vary the port.
              <button className="link-btn" onClick={() => openExternal(CONSOLE.client)}><ExternalLink size={11} /> Create client</button>
            </li>
            <li>Load that JSON below, then press the button above to sign in.</li>
          </ol>

          <div className="setup-drop" onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); loadFile(e.dataTransfer.files[0]) }}>
            <button className="ghost-btn" onClick={() => fileRef.current?.click()}><Upload size={13} /> Load client_secret….json</button>
            <span className="muted small">or drop the file here — you can also paste the JSON into Client ID</span>
            <input ref={fileRef} type="file" accept=".json,application/json" hidden onChange={(e) => loadFile(e.target.files?.[0] ?? undefined)} />
          </div>

          <label><span className="toggle-text"><b>Client ID</b></span>
            <input ref={idRef} value={clientId} onChange={(e) => onChange({ googleClientId: e.target.value })} onPaste={onPasteMaybeJson} placeholder="…apps.googleusercontent.com — or paste the whole JSON" spellCheck={false} />
          </label>
          <label><span className="toggle-text"><b>Client secret</b></span>
            <input type="password" value={clientSecret} onChange={(e) => onChange({ googleClientSecret: e.target.value })} onPaste={onPasteMaybeJson} placeholder={secretSaved ? 'Saved. Type to replace' : 'GOCSPX-…'} spellCheck={false} />
          </label>

          {draftHasClient && !google?.configured && (
            <button className="primary-btn" onClick={() => void signIn()}><span className="g-logo g-logo-sm">G</span> Save and sign in</button>
          )}

          <p className="muted small">
            {google?.source === 'env'
              ? <>Sign-in uses the client from the app&apos;s <code>.env</code>. Paste your own above to override it; clear both fields to go back.</>
              : <>Saved in this app&apos;s local database. The same pair can live in <code>.env</code> as <code>GOOGLE_CLIENT_ID</code> / <code>GOOGLE_CLIENT_SECRET</code> instead.</>}
          </p>
          <p className="muted small">Scopes requested: calendar, gmail.modify, tasks, drive.readonly, drive.file, documents, spreadsheets, email. Tokens stay in the local database. Sending email is a separate tool you can keep off.</p>
        </details>
      )}
    </div>
  )
}
