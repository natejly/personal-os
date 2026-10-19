import { useEffect, useState, type KeyboardEvent } from 'react'
import type { PermissionPreset, PresetDiff } from '@shared/types'
import { ShieldAlert, ShieldCheck } from '../icons'
import { api } from '../lib/api'
import { diffRows } from '../lib/presets'
import { useStore } from '../store'
import PopMenu from './PopMenu'
import { MODES, modeOf, needsConfirm, pillLabel, pillTitle, type PermissionMode } from '../lib/permissionMode'

/** The description without its "Label — " lead, as a sentence of its own. */
const blurb = (d: string): string => { const s = d.replace(/^[^—]*— /, ''); return s.charAt(0).toUpperCase() + s.slice(1) }

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
            className={`mode-card ${m.id === 'allow_all' ? 'danger' : ''} ${mode === m.id ? 'on' : ''}`} onClick={() => pick(m.id)}>
            <b>{m.id === 'allow_all' ? <ShieldAlert size={14} /> : <ShieldCheck size={14} />} {m.label}</b>
            <span>{blurb(m.description)}</span>
          </button>
        ))}
      </div>
      {ask && (
        // Inside the settings dialog on purpose: a second focus trap would fight the first one.
        <div className="modal-backdrop" onKeyDown={(e) => { if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); setAsk(false) } }}>
          <div className="modal" role="alertdialog" aria-modal="true" aria-labelledby="allow-all-title">
            <header><h2 id="allow-all-title">Allow everything?</h2></header>
            <section>
              <p>Grain will not check actions with a second AI and will not show any approval card. It will run commands, delete files, send email, accept its own document edits and schedule tasks without asking.</p>
              <ul>
                <li>Deny rules still apply.</li>
                <li>Grain&apos;s own data and app stay off limits.</li>
                <li>Permanent deletes, disk wipes, force-pushes, credential access and sent email are not confirmed first.</li>
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

/** What the pill's menu offers a chat: each permission preset, applied to this chat alone. One that allows more than the chat has
    now lists what would change and applies on the second click. */
function ChatPresets({ conversationId, close, onApplied }: { conversationId: string; close: () => void; onApplied: () => void }): JSX.Element {
  const [presets, setPresets] = useState<PermissionPreset[]>([])
  const [held, setHeld] = useState<{ preset: PermissionPreset; diff: PresetDiff } | null>(null)
  const toast = useStore((s) => s.toast)
  useEffect(() => { api.presets.list().then(setPresets).catch(() => undefined) }, [])
  const apply = (preset: PermissionPreset, confirm?: string): void => {
    api.presets.apply(preset.id, 'chat', conversationId, confirm).then((r) => {
      if ('held' in r) { setHeld({ preset, diff: r.held }); return }
      toast(`${preset.name} applied to this chat`, 'info')
      onApplied()
      close()
    }).catch((e: Error) => toast(e.message, 'error'))
  }
  if (held) {
    return (
      <div role="alert">
        <p className="pop-menu-note"><b>{held.preset.name}</b> would allow more than this chat has now:</p>
        <ul className="pop-menu-note">{diffRows(held.diff).filter((r) => r.loosens).map((r) => <li key={r.key}>{r.label}: {r.old} to {r.next}</li>)}</ul>
        <button type="button" className="pop-menu-item" onClick={() => setHeld(null)}>Cancel</button>
        <button type="button" className="pop-menu-item" onClick={() => apply(held.preset, held.diff.diff_hash)}>Apply anyway</button>
      </div>
    )
  }
  return (
    <>
      <p className="pop-menu-title">Preset for this chat</p>
      {presets.map((p) => (
        <button key={p.id} type="button" className="pop-menu-item" onClick={() => apply(p)}>{p.name}{p.description && <small>{p.description}</small>}</button>
      ))}
    </>
  )
}

/** The composer pill: the current mode, in red text under Allow everything and otherwise plain. Its menu applies a preset to this
    chat and opens Settings on Permissions, which also holds the domains and MCP controls. */
export function PermissionModePill({ conversationId = null }: { conversationId?: string | null }): JSX.Element {
  const globalMode = useStore((s) => modeOf(s.settings))
  const [chatMode, setChatMode] = useState<PermissionMode | null>(null)
  const [applied, setApplied] = useState(0)
  // A preset applied to this chat can set its own mode, so the pill shows the chat's effective one.
  useEffect(() => {
    setChatMode(null)
    if (!conversationId) return
    let live = true
    api.config.effective(null, conversationId).then((c) => { if (live) setChatMode(modeOf({ permissionMode: c.values.permissionMode?.value })) }).catch(() => undefined)
    return () => { live = false }
  }, [conversationId, globalMode, applied])
  const mode = chatMode ?? globalMode
  const openSettings = useStore((s) => s.openSettings)
  const title = pillTitle(mode)
  return (
    <PopMenu className={`ghost-btn skip-perms ${mode === 'allow_all' ? 'on' : ''}`} title={title} aria-label={title} data-mode={mode}
      label={<><ShieldCheck size={13} aria-hidden /> {pillLabel(mode)}</>}>
      {(close) => (
        <>
          {conversationId && <ChatPresets conversationId={conversationId} close={close} onApplied={() => setApplied((n) => n + 1)} />}
          <button type="button" className="pop-menu-item" onClick={() => { close(); openSettings('permissions') }}>Permission settings</button>
        </>
      )}
    </PopMenu>
  )
}
