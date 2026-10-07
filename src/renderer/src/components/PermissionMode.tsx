import { useState, type KeyboardEvent } from 'react'
import { ShieldAlert, ShieldCheck } from 'lucide-react'
import { useStore } from '../store'
import { ALL_CONNECTIONS_LABEL, MODES, allConnectionsOn, allConnectionsTitle, modeOf, needsConfirm, pillLabel, pillTitle, type PermissionMode } from '../lib/permissionMode'

/** Three radio cards for the global permission mode. Choosing Allow everything asks first; the choice saves at once. */
export function PermissionModeCards({ mode, onPick }: { mode: PermissionMode; onPick: (m: PermissionMode) => Promise<void> }): JSX.Element {
  const [ask, setAsk] = useState(false)
  const [busy, setBusy] = useState(false)
  const apply = async (m: PermissionMode): Promise<void> => {
    setBusy(true)
    try { await onPick(m) } finally { setBusy(false); setAsk(false) }
  }
  const pick = (m: PermissionMode): void => {
    if (m === mode || busy) return
    if (needsConfirm(mode, m)) setAsk(true)
    else void apply(m)
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
            className={`mode-card ${m.id === 'allow_all' ? 'danger' : ''} ${mode === m.id ? 'on' : ''}`} onClick={() => pick(m.id)}>
            <b>{m.id === 'allow_all' ? <ShieldAlert size={14} /> : <ShieldCheck size={14} />} {m.label}</b>
            <span>{m.description.replace(/^[^—]*— /, '')}</span>
          </button>
        ))}
      </div>
      {ask && (
        // Inside the settings dialog on purpose: a second focus trap would fight the first one.
        <div className="modal-backdrop" onKeyDown={(e) => { if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); setAsk(false) } }}>
          <div className="modal" role="alertdialog" aria-modal="true" aria-labelledby="allow-all-title">
            <header><h2 id="allow-all-title">Allow everything?</h2></header>
            <section>
              <p>Grain will not check actions with a second AI and will not show approval cards for ordinary actions. It will run commands, move files to the Trash, and schedule tasks without asking.</p>
              <ul>
                <li>Deny rules still apply.</li>
                <li>Grain&apos;s own data and app stay off limits. Credential stores (keys, passwords, sign-in files) and writes right after untrusted content still ask.</li>
                <li>Permanent deletes outside the Trash, disk wipes and git force-pushes still show an approval card.</li>
                <li>Sending email always shows the review card.</li>
                <li>Every action is still logged in approval history.</li>
              </ul>
            </section>
            <footer>
              <button className="ghost-btn" autoFocus onClick={() => setAsk(false)}>Cancel</button>
              <button className="ghost-btn danger" disabled={busy} onClick={() => void apply('allow_all')}>Allow everything</button>
            </footer>
          </div>
        </div>
      )}
    </>
  )
}

/** Composer pills: the current mode (red under Allow everything) and, beside it, a red "All domains + MCP" pill while
    that setting is on. These red pills are the only cues (no app-wide banner). Each opens Settings on Permissions. */
export function PermissionModePill(): JSX.Element {
  const mode = useStore((s) => modeOf(s.settings))
  const allConn = useStore((s) => allConnectionsOn(s.settings))
  const openSettings = useStore((s) => s.openSettings)
  const title = pillTitle(mode)
  return (
    <>
      <button type="button" className={`ghost-btn skip-perms ${mode === 'allow_all' ? 'on' : ''}`} title={title} aria-label={title}
        data-mode={mode} onClick={() => openSettings('permissions')}>
        {mode === 'allow_all' ? <ShieldAlert size={13} aria-hidden /> : <ShieldCheck size={13} aria-hidden />} {pillLabel(mode)}
      </button>
      {allConn && (
        <button type="button" className="ghost-btn skip-perms on" title={allConnectionsTitle} aria-label={allConnectionsTitle}
          data-allow-all-connections onClick={() => openSettings('permissions')}>
          <ShieldAlert size={13} aria-hidden /> {ALL_CONNECTIONS_LABEL}
        </button>
      )}
    </>
  )
}
