import { useEffect, useRef, useState } from 'react'
import { Unplug, Check, AlertTriangle, RefreshCw, ExternalLink } from 'lucide-react'
import { useStore } from '../store'

const PORTAL = 'https://entra.microsoft.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade/quickStartType~/null/sourceType/Microsoft_AAD_IAM'
const SCOPES = 'User.Read, Mail.ReadWrite, Mail.Send, Calendars.ReadWrite, offline_access, openid, profile'
const logo = { color: '#00a4ef' } as const

/** Opens in the real browser: the main process turns window.open into shell.openExternal. */
const openExternal = (url: string): void => void window.open(url, '_blank')

/** Outlook mail and calendar through an Entra public client: PKCE, so a client ID is the whole credential. */
export default function MicrosoftSettings({ clientId, tenant, onChange, onSaveCreds, redirectUri = 'http://127.0.0.1/integrations/microsoft/callback' }: { clientId: string; tenant: string; onChange: (p: { microsoftClientId?: string; microsoftTenant?: string }) => void; onSaveCreds: () => Promise<void>; redirectUri?: string }): JSX.Element {
  const microsoft = useStore((s) => s.microsoft)
  const { refreshMicrosoft, connectMicrosoft, disconnectMicrosoft } = useStore()
  const [setupOpen, setSetupOpen] = useState(false)
  const idRef = useRef<HTMLInputElement>(null)
  useEffect(() => { void refreshMicrosoft() }, [refreshMicrosoft])

  const hasClient = !!clientId.trim() || !!microsoft?.configured

  const signIn = async (): Promise<void> => {
    if (!hasClient) {
      setSetupOpen(true)
      requestAnimationFrame(() => idRef.current?.scrollIntoView({ block: 'center', behavior: 'smooth' }))
      return
    }
    if (clientId !== '' || tenant !== '') await onSaveCreds()
    await connectMicrosoft()
  }

  const needsReauth = !!microsoft?.connected && microsoft.needs_reauth
  const missing = microsoft?.connected ? microsoft.missing_scopes : []

  return (
    <div className="integration">
      <div className="integration-head">
        <span className="g-logo" style={logo}>M</span>
        <div>
          <b>Microsoft 365 / Outlook</b>
          <small>
            {microsoft?.connected
              ? <><Check size={11} /> Signed in as {microsoft.email}</>
              : hasClient
                ? 'Sign in to let the app read and send Outlook mail and manage your calendar. Mail and Calendar use it when it is the active provider above.'
                : 'Needs a one-time app registration (about two minutes) before the first sign-in.'}
          </small>
        </div>
        {microsoft?.connected ? (
          <>
            {needsReauth && <button className="primary-btn" onClick={() => void signIn()}><RefreshCw size={13} /> Reconnect</button>}
            <button className="ghost-btn" onClick={() => void disconnectMicrosoft()}><Unplug size={13} /> Sign out</button>
          </>
        ) : (
          <button className="primary-btn" onClick={() => void signIn()} title={hasClient ? 'Opens Microsoft sign-in in your browser' : 'Shows the one-time app registration'}>
            <span className="g-logo g-logo-sm" style={logo}>M</span> {hasClient ? 'Sign in with Microsoft' : 'Set up Microsoft sign-in'}
          </button>
        )}
      </div>

      {needsReauth && (
        <p className="integration-warn"><AlertTriangle size={13} /> {microsoft?.reauth_reason ?? 'This connection needs to be renewed.'} Click Reconnect to sign in again.</p>
      )}
      {missing.length > 0 && !needsReauth && (
        <p className="integration-warn"><AlertTriangle size={13} /> Missing permissions: {missing.join(', ')}. Add them to the app registration and reconnect.</p>
      )}

      <details className="setup" open={setupOpen || !hasClient} onToggle={(e) => setSetupOpen((e.target as HTMLDetailsElement).open)}>
        <summary>{hasClient ? 'App registration' : 'One-time setup: register the app'}</summary>

        <label><span className="toggle-text"><b>Client ID</b></span>
          <input ref={idRef} value={clientId} onChange={(e) => onChange({ microsoftClientId: e.target.value.trim() })} placeholder="Application (client) ID, a GUID" spellCheck={false} />
        </label>
        <label><span className="toggle-text"><b>Tenant</b></span>
          <input value={tenant} onChange={(e) => onChange({ microsoftTenant: e.target.value.trim() })} placeholder="common" spellCheck={false} />
        </label>
        <p className="muted small">common for personal + work accounts, or your tenant id/domain.</p>
        <p className="muted small">No client secret: this is a public client signed in with PKCE.</p>

        {!microsoft?.connected && !microsoft?.configured && !!clientId.trim() && (
          <button className="primary-btn" onClick={() => void signIn()}><span className="g-logo g-logo-sm" style={logo}>M</span> Save and sign in</button>
        )}

        <details className="setup">
          <summary>How to register the app</summary>
          <ol className="setup-steps muted small">
            <li>
              Entra admin center → App registrations → <b>New registration</b>.
              <button className="link-btn" onClick={() => openExternal(PORTAL)}><ExternalLink size={11} /> App registrations</button>
            </li>
            <li>Supported account types: <b>Accounts in any organizational directory and personal Microsoft accounts</b>.</li>
            <li>Authentication → add platform <b>Mobile and desktop applications</b>. The portal box rejects a <code>127.0.0.1</code> address, so open <b>Manifest</b> and add <code>{redirectUri}</code> under <code>publicClient.redirectUris</code> (the port is ignored for loopback addresses).</li>
            <li>Authentication → <b>Allow public client flows</b>: Yes.</li>
            <li>API permissions → Microsoft Graph → <b>Delegated</b>: <code>{SCOPES}</code>.</li>
            <li>Copy the Application (client) ID into the field above, then sign in.</li>
          </ol>
        </details>

        <p className="muted small">
          {microsoft?.source === 'env'
            ? <>Sign-in uses the client from the app&apos;s <code>.env</code>. Enter your own above to override it; clear the field to go back.</>
            : <>Saved in this app&apos;s local database. It can live in <code>.env</code> as <code>MICROSOFT_CLIENT_ID</code> / <code>MICROSOFT_TENANT</code> instead.</>}
        </p>
        <p className="muted small">Tokens stay in the local keychain. Sending email is a separate tool you can keep off.</p>
      </details>
    </div>
  )
}
