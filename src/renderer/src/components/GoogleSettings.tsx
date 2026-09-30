import { useEffect } from 'react'
import { Unplug, Check, AlertTriangle, RefreshCw } from 'lucide-react'
import { useStore } from '../store'

export default function GoogleSettings({ clientId, clientSecret, onChange, onSaveCreds }: { clientId: string; clientSecret: string; onChange: (p: { googleClientId?: string; googleClientSecret?: string }) => void; onSaveCreds: () => Promise<void> }): JSX.Element {
  const google = useStore((s) => s.google)
  const { refreshGoogle, connectGoogle, disconnectGoogle } = useStore()
  useEffect(() => { void refreshGoogle() }, [refreshGoogle])

  // The app carries its own OAuth client (.env), so signing in is one click. A client typed
  // here (unsaved draft) or already saved in Settings overrides it.
  const draftHasClient = !!clientId && !!clientSecret
  const canSignIn = draftHasClient || !!google?.configured
  const needsClient = !google?.configured && !draftHasClient

  const signIn = async (): Promise<void> => {
    if (draftHasClient || clientId !== '' || clientSecret !== '') await onSaveCreds()
    await connectGoogle()
  }

  return (
    <div className="integration">
      <div className="integration-head">
        <span className="g-logo">G</span>
        <div>
          <b>Google Workspace</b>
          <small>{google?.connected ? <><Check size={11} /> Signed in as {google.email}</> : 'Calendar, Gmail, Tasks and Drive (Docs, Sheets, Slides) for Today and as tools for the assistant.'}</small>
        </div>
        {google?.connected ? (
          <>
            {google.needs_reauth && (
              <button className="primary-btn" onClick={() => void signIn()}><RefreshCw size={13} /> Reconnect</button>
            )}
            <button className="ghost-btn" onClick={() => void disconnectGoogle()}><Unplug size={13} /> Sign out</button>
          </>
        ) : (
          <button className="primary-btn" onClick={() => void signIn()} disabled={!canSignIn} title={canSignIn ? 'Opens Google sign-in in your browser' : 'No OAuth client configured yet'}>
            <span className="g-logo g-logo-sm">G</span> Sign in with Google
          </button>
        )}
      </div>
      {google?.connected && google.needs_reauth && (
        <p className="integration-warn"><AlertTriangle size={13} /> {google.reauth_reason ?? 'This connection needs to be renewed.'} Click Reconnect to sign in again.</p>
      )}
      {!google?.connected && (
        <details className="setup" open={needsClient}>
          <summary>{needsClient ? 'Setup needed: no OAuth client configured' : google?.source === 'settings' ? 'Using your own OAuth client' : 'Advanced: use your own OAuth client'}</summary>
          {needsClient ? (
            <p className="muted small">The quickest fix is to add <code>GOOGLE_CLIENT_ID</code> and <code>GOOGLE_CLIENT_SECRET</code> to the app's <code>.env</code> (see <code>.env.example</code>) and restart. Or paste a client below.</p>
          ) : (
            <p className="muted small">Sign-in uses the OAuth client from the app's <code>.env</code>. Paste your own below to override it; clear both fields to go back.</p>
          )}
          <ol className="muted small">
            <li>In <a href="https://console.cloud.google.com/apis/credentials" target="_blank" rel="noreferrer">Google Cloud Console</a>, create a project and enable the <b>Google Calendar API</b>, <b>Gmail API</b>, <b>Tasks API</b> and <b>Google Drive API</b> (APIs &amp; Services → Library).</li>
            <li>Configure the OAuth consent screen (External, add yourself as a test user).</li>
            <li>Create credentials → OAuth client ID → application type <b>Desktop app</b>.</li>
          </ol>
          <label><span>Client ID</span><input value={clientId} onChange={(e) => onChange({ googleClientId: e.target.value })} placeholder="…apps.googleusercontent.com" spellCheck={false} /></label>
          <label><span>Client secret</span><input type="password" value={clientSecret} onChange={(e) => onChange({ googleClientSecret: e.target.value })} placeholder="GOCSPX-…" spellCheck={false} /></label>
          <p className="muted small">Scopes requested: calendar, gmail.modify, tasks, drive.readonly, email. Tokens stay in the local database. Sending email is a separate tool you can keep off.</p>
        </details>
      )}
    </div>
  )
}
