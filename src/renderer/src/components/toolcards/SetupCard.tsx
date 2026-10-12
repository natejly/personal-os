import { useState } from 'react'
import { Check, Plug } from '../../icons'
import type { ToolEvent } from '@shared/types'
import { useStore, type View } from '../../store'
import { parseResult } from '../../lib/toolResult'

/** What the backend's setup_cards.card() puts under `setup` in a tool result (setup_request, or a Google tool that was not connected). */
export interface Setup { feature: string; title: string; why: string; action: 'google' | 'settings' | 'view'; tab?: string; view?: string; reauth?: boolean }

/** The setup card a finished call carries, if any. Reads the persisted result alone, so a reloaded reply shows it too. */
export function setupOf(event: ToolEvent): Setup | null {
  if (event.pending || !event.result_preview) return null
  const s = (parseResult(event.result_preview).data as { setup?: Setup } | null)?.setup
  return s && typeof s.feature === 'string' && typeof s.title === 'string' ? s : null
}

/** A small inline "set this up" card. The button is the only thing that acts: Google starts the sign-in, the rest open the right Settings tab or view. */
export default function SetupCard({ setup }: { setup: Setup }): JSX.Element {
  const google = useStore((s) => s.google)
  const [clicked, setClicked] = useState(false)
  const isGoogle = setup.action === 'google'
  // A reconnect is only done once the click led to a fresh sign-in; a first connect is done whenever the account is signed in.
  const done = isGoogle && !!google?.connected && !google.needs_reauth && (!setup.reauth || clicked)
  const go = (): void => {
    const s = useStore.getState()
    setClicked(true)
    if (setup.action === 'google') {
      if (s.google?.configured === false) s.openSettings('integrations') // no OAuth client yet: Settings walks through making one
      else void s.connectGoogle()
    } else if (setup.action === 'view') s.setView((setup.view ?? 'chat') as View)
    else s.openSettings((setup.tab ?? 'model') as Parameters<typeof s.openSettings>[0])
  }
  return (
    <div className={`setup-card ${done ? 'done' : ''}`} role="status">
      <span className="setup-icon">{done ? <Check size={14} /> : <Plug size={14} />}</span>
      <span className="setup-text"><b>{done ? `${setup.feature === 'google' ? 'Google' : setup.title} connected` : setup.title}</b>{!done && <small>{setup.why}</small>}</span>
      {done
        ? <span className="setup-ok">Connected ✓</span>
        : <button className="primary-btn sm" onClick={go}>{isGoogle ? (setup.reauth ? 'Reconnect' : 'Connect') : setup.action === 'view' ? 'Open' : 'Open settings'}</button>}
    </div>
  )
}
