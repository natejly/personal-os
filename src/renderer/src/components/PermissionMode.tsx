import { useEffect, useState, type KeyboardEvent } from 'react'
import { ShieldAlert, ShieldCheck } from '../icons'
import { api } from '../lib/api'
import { useStore } from '../store'
import PopMenu from './PopMenu'
import { MODES, modeOf, needsConfirm, pillLabel, pillTitle, type PermissionMode } from '../lib/permissionMode'

/** Three radio cards for the global permission mode. Choosing Allow everything asks first; the choice goes into the Settings draft and applies on Save. */
export function PermissionModeCards({ mode, onPick }: { mode: PermissionMode; onPick: (m: PermissionMode) => void }): JSX.Element {
  const [ask, setAsk] = useState(false)
  const apply = (m: PermissionMode): void => { onPick(m); setAsk(false) }
  const pick = (m: PermissionMode): void => {
    if (m === mode) return
    if (needsConfirm(mode, m)) setAsk(true)
    else apply(m)
  }
  const onKey = (e: KeyboardEvent<HTMLDivElement>): void => {
    const d = e.key === 'ArrowDown' || e.key === 'ArrowRight' ? 1 : e.key === 'ArrowUp' || e.key === 'ArrowLeft' ? -1 : 0
    if (!d) return
    e.preventDefault()
    // Arrows move focus only; Space or Enter picks, so arrowing past Allow everything never opens its dialog.
    const at = MODES.findIndex((x) => x.id === (document.activeElement as HTMLElement | null)?.dataset?.mode)
    const next = MODES[((at < 0 ? MODES.findIndex((x) => x.id === mode) : at) + d + MODES.length) % MODES.length].id
    ;(e.currentTarget.querySelector(`[data-mode="${next}"]`) as HTMLElement | null)?.focus()
  }
  return (
    <>
      <div className="mode-cards" role="radiogroup" aria-label="Permission mode" onKeyDown={onKey}>
        {MODES.map((m) => (
          <button key={m.id} type="button" role="radio" data-mode={m.id} aria-checked={mode === m.id} tabIndex={mode === m.id ? 0 : -1}
            className={`mode-card ${m.id === 'allow_all' ? 'danger' : ''} ${mode === m.id ? 'on' : ''}`} title={m.description} onClick={() => pick(m.id)}>
            <b>{m.id === 'allow_all' ? <ShieldAlert size={14} /> : <ShieldCheck size={14} />} {m.label}</b>
          </button>
        ))}
      </div>
      {ask && (
        // Inside the settings dialog on purpose: a second focus trap would fight the first one.
        <div className="modal-backdrop" onKeyDown={(e) => { if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); setAsk(false) } }}>
          <div className="modal" role="alertdialog" aria-modal="true" aria-labelledby="allow-all-title">
            <header><h2 id="allow-all-title">Allow everything?</h2></header>
            <section>
              <p>Commands, deletes, email and edits run without asking.</p>
              <ul>
                <li>Deny rules still apply.</li>
                <li>Grain&apos;s own data and app stay off limits.</li>
                <li>Destructive actions are not confirmed first.</li>
                <li>Every action is still logged in approval history.</li>
              </ul>
            </section>
            <footer>
              <button className="ghost-btn" autoFocus onClick={() => setAsk(false)}>Cancel</button>
              <button className="ghost-btn danger" onClick={() => apply('allow_all')}>Allow everything</button>
            </footer>
          </div>
        </div>
      )}
    </>
  )
}

export function PermissionModePill({ conversationId = null }: { conversationId?: string | null }): JSX.Element {
  const globalMode = useStore((s) => modeOf(s.settings))
  const [chatMode, setChatMode] = useState<PermissionMode | null>(null)
  // A chat can carry its own mode, so the pill shows the chat's effective one.
  useEffect(() => {
    setChatMode(null)
    if (!conversationId) return
    let live = true
    api.config.effective(null, conversationId).then((c) => { if (live) setChatMode(modeOf({ permissionMode: c.values.permissionMode?.value })) }).catch(() => undefined)
    return () => { live = false }
  }, [conversationId, globalMode])
  const mode = chatMode ?? globalMode
  const openSettings = useStore((s) => s.openSettings)
  const title = pillTitle(mode)
  return (
    <PopMenu className={`ghost-btn skip-perms ${mode === 'allow_all' ? 'on' : ''}`} title={title} aria-label={title} data-mode={mode}
      label={<><ShieldCheck size={13} aria-hidden /> {pillLabel(mode)}</>}>
      {(close) => (
        <>
          <button type="button" className="pop-menu-item" onClick={() => { close(); openSettings('permissions') }}>Permission settings</button>
        </>
      )}
    </PopMenu>
  )
}
