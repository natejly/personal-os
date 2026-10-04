import { useEffect, useState } from 'react'
import { X } from 'lucide-react'
import type { Settings } from '@shared/types'
import { api } from '../lib/api'
import { useStore } from '../store'
import { clampSetting, hostError, networkMode, networkPatch, normalizeHost, sandboxNetMode, type NetworkMode, type SandboxNetMode } from '../lib/coworkSettings'

/**
 * The Cowork section of Settings: how long and how costly a desk may run, what its shell and browser may reach,
 * which model reads pictures, and the Python environment its code runs in. It edits the modal's `draft` through
 * `patch` like every other section, so nothing is saved until Save — except the environment build, which is an
 * action on the machine, not a setting.
 */

const Toggle = ({ title, help, checked, onChange }: { title: string; help: string; checked: boolean; onChange: (v: boolean) => void }): JSX.Element => (
  <label className="toggle-row plain">
    <span className="toggle-text"><b>{title}</b><small>{help}</small></span>
    <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} /><span className="switch" />
  </label>
)

/** A number the user can clear while typing; it is clamped into the backend's range once they leave the field. */
function NumField({ title, help, settingKey, value, fallback, step = 1, onCommit }: {
  title: string; help: string; settingKey: string; value: number | undefined; fallback: number; step?: number; onCommit: (n: number) => void
}): JSX.Element {
  const [text, setText] = useState(String(value ?? fallback))
  useEffect(() => setText(String(value ?? fallback)), [value, fallback])
  const commit = (): void => {
    const n = clampSetting(settingKey, text, value ?? fallback)
    setText(String(n))
    onCommit(n)
  }
  return (
    <label className="cowork-num">
      <span>{title}</span>
      <input type="number" min={0} step={step} value={text} onChange={(e) => setText(e.target.value)} onBlur={commit}
        onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); commit() } }} />
      <small className="muted">{help}</small>
    </label>
  )
}

/** A list of hostnames, following WorkspaceRoots: type, Enter or Add, remove with the x. An entry also allows its subdomains. */
function HostList({ title, help, value, onChange }: { title: string; help: string; value: string[]; onChange: (next: string[]) => void }): JSX.Element {
  const [text, setText] = useState('')
  const [error, setError] = useState<string | null>(null)
  const add = (): void => {
    const bad = hostError(text)
    if (bad) return setError(bad)
    const h = normalizeHost(text)
    setError(null)
    setText('')
    if (!value.includes(h)) onChange([...value, h])
  }
  return (
    <div className="workspace-roots">
      <span><b>{title}</b></span>
      <p className="muted small">{help}</p>
      {value.length > 0 && (
        <ul className="plain-list">
          {value.map((h) => (
            <li key={h} className="chip-check-row">
              <code>{h}</code>
              <button type="button" aria-label={`Remove ${h}`} onClick={() => onChange(value.filter((x) => x !== h))}><X size={12} /></button>
            </li>
          ))}
        </ul>
      )}
      <div className="workspace-roots-add">
        <input value={text} placeholder="example.com" spellCheck={false} aria-label={`${title}: add a hostname`} aria-invalid={error ? true : undefined}
          onChange={(e) => { setText(e.target.value); setError(null) }} onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); add() } }} />
        <button type="button" onClick={add} disabled={!text.trim()}>Add</button>
      </div>
      {error && <p className="cowork-error" role="alert">{error}</p>}
    </div>
  )
}

const NETWORK: { mode: NetworkMode; label: string }[] = [
  { mode: 'off', label: 'Off' },
  { mode: 'registries', label: 'Registries and allowed hosts' },
  { mode: 'open', label: 'Open' }
]
const NETWORK_HELP: Record<NetworkMode, string> = {
  off: 'Commands a desk runs have no network at all.',
  registries: 'Commands can reach package registries and the hosts listed below, and nothing else.',
  open: 'Commands can reach any address.'
}

const SANDBOX_NET: { mode: SandboxNetMode; label: string }[] = [
  { mode: 'off', label: 'Off' },
  { mode: 'proxy', label: 'Registries and allowed hosts' },
  { mode: 'open', label: 'Open' }
]
const SANDBOX_NET_HELP: Record<SandboxNetMode, string> = {
  off: 'The Linux sandbox has no network at all.',
  proxy: 'The sandbox can reach package registries and the allowed hosts above, through a proxy that is its only way out. Results count as untrusted once it reaches a host that is not a registry.',
  open: 'The sandbox can reach any address, and everything it returns counts as untrusted.'
}

/** What a build of the work environment is doing: nothing yet, running, or failed with the reason. */
function WorkEnv({ draft, saved, patch }: { draft: Settings; saved: Settings; patch: (p: Partial<Settings>) => void }): JSX.Element {
  const [st, setSt] = useState<{ ready: boolean; python: string | null; packages: string[]; error: string | null } | null>(null)
  const [busy, setBusy] = useState(false)
  const [fail, setFail] = useState<string | null>(null)
  const [all, setAll] = useState(false)
  useEffect(() => { api.cowork.env.status().then(setSt).catch((e: Error) => setFail(e.message)) }, [])
  const setup = async (): Promise<void> => {
    setBusy(true)
    setFail(null)
    try {
      const r = await api.cowork.env.setup()
      setSt(r)
      if (r.error) setFail(r.error)
    } catch (e) {
      setFail((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  const unsaved = JSON.stringify(draft.workEnvPackages ?? []) !== JSON.stringify(saved.workEnvPackages ?? [])
  const shown = all ? st?.packages ?? [] : (st?.packages ?? []).slice(0, 6)
  return (
    <div className="cowork-env">
      <div className="cowork-env-row">
        <b>{st === null ? 'Checking…' : st.ready ? 'Ready' : st.error ? 'Setup failed' : 'Not set up'}</b>
        <button type="button" className="ghost-btn" disabled={busy || unsaved} onClick={() => void setup()}>
          {busy ? 'Working, this can take a few minutes…' : st?.ready ? 'Update' : 'Set up'}
        </button>
      </div>
      <p className="muted small">
        One shared Python environment for code a desk runs, so installed packages survive between desks.
        {unsaved ? ' Save your settings first: the build reads the saved package list.' : ''}
      </p>
      {st && st.packages.length > 0 && (
        <p className="muted small">
          <code>{shown.join(', ')}</code>
          {st.packages.length > 6 && <> <button type="button" className="link small" onClick={() => setAll((v) => !v)}>{all ? 'show fewer' : `and ${st.packages.length - 6} more`}</button></>}
        </p>
      )}
      {fail && <p className="cowork-error" role="alert">{fail}</p>}
      <PackageList value={draft.workEnvPackages ?? []} onChange={(workEnvPackages) => patch({ workEnvPackages })} />
    </div>
  )
}

/** Extra packages by name; a pip requirement such as `polars` or `pandas>=2`. Not hostnames, so no hostname validation. */
function PackageList({ value, onChange }: { value: string[]; onChange: (next: string[]) => void }): JSX.Element {
  const [text, setText] = useState('')
  const add = (): void => {
    const p = text.trim()
    setText('')
    if (p && !value.includes(p)) onChange([...value, p])
  }
  return (
    <div className="workspace-roots">
      <span><b>Extra packages</b></span>
      <p className="muted small">Installed on top of the standard set when you set up or update the environment.</p>
      {value.length > 0 && (
        <ul className="plain-list">
          {value.map((p) => (
            <li key={p} className="chip-check-row">
              <code>{p}</code>
              <button type="button" aria-label={`Remove ${p}`} onClick={() => onChange(value.filter((x) => x !== p))}><X size={12} /></button>
            </li>
          ))}
        </ul>
      )}
      <div className="workspace-roots-add">
        <input value={text} placeholder="polars" spellCheck={false} aria-label="Extra packages: add a package"
          onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); add() } }} />
        <button type="button" onClick={add} disabled={!text.trim()}>Add</button>
      </div>
    </div>
  )
}

export default function CoworkSettings({ draft, patch }: { draft: Settings; patch: (p: Partial<Settings>) => void }): JSX.Element {
  const saved = useStore((s) => s.settings)
  const models = useStore((s) => s.models)
  const mode = networkMode(draft)
  const hosts = draft.shellAllowedDomains ?? []
  const pickMode = (m: NetworkMode): void => patch(networkPatch(m, m === 'off' ? [] : hosts))
  const sbxMode = sandboxNetMode(draft.sandboxNetwork)
  return (
    <div className="cowork-settings">
      <h4>Desks</h4>
      <NumField title="Turns per desk" settingKey="deskMaxTurns" value={draft.deskMaxTurns} fallback={12}
        help="How many chained replies a desk may take before it stops and asks. 0 means no limit." onCommit={(n) => patch({ deskMaxTurns: n })} />
      <NumField title="Spend per desk ($)" settingKey="deskMaxCost" value={draft.deskMaxCost} fallback={2} step={0.5}
        help="A desk stops when its cost passes this. 0 means no limit." onCommit={(n) => patch({ deskMaxCost: n })} />
      <NumField title="Desks working at once" settingKey="deskMaxLive" value={draft.deskMaxLive} fallback={4}
        help="More desks than this wait their turn. 0 means no limit." onCommit={(n) => patch({ deskMaxLive: n })} />
      <NumField title="Wait for an unwatched card (seconds)" settingKey="parkAfterSeconds" value={draft.parkAfterSeconds} fallback={180}
        help="How long a desk holds a question or approval nobody is looking at before it lets go. 0 waits forever." onCommit={(n) => patch({ parkAfterSeconds: n })} />
      <Toggle title="Notify me" help="A system notification when a desk needs you or finishes, while the window is not in front."
        checked={draft.deskNotify !== false} onChange={(deskNotify) => patch({ deskNotify })} />
      <Toggle title="Check before finishing" help="Don't let a desk finish with open steps or missing files."
        checked={draft.deskDoneGate !== false} onChange={(deskDoneGate) => patch({ deskDoneGate })} />
      <Toggle title="Review against the brief" help="Have a reviewer check the result against the brief before finishing."
        checked={draft.deskSelfReview !== false} onChange={(deskSelfReview) => patch({ deskSelfReview })} />

      <h4>Shell</h4>
      <Toggle title="Run sandboxed commands without asking" help="Run sandboxed commands inside a desk's own folder without asking."
        checked={draft.deskShellAuto !== false} onChange={(deskShellAuto) => patch({ deskShellAuto })} />
      <div className="send-hold cowork-net">
        <span className="toggle-text"><b>Network for commands</b><small>{NETWORK_HELP[mode]}</small></span>
        <div className="seg" role="group" aria-label="Network for commands">
          {NETWORK.map((n) => (
            <button key={n.mode} type="button" className={mode === n.mode ? 'on' : ''} aria-pressed={mode === n.mode} onClick={() => pickMode(n.mode)}>{n.label}</button>
          ))}
        </div>
        {mode === 'open' && <p className="cowork-error">Open network lets a command send files off this Mac, and whatever it downloads is untrusted text. Prefer allowed hosts.</p>}
      </div>
      {(mode !== 'off' || sbxMode === 'proxy') && (
        <HostList title="Allowed hosts" help="Hostnames a command may reach, such as pypi.org. A name also allows its subdomains. No scheme, path, wildcard or IP address."
          value={hosts} onChange={(shellAllowedDomains) => patch({ shellAllowedDomains })} />
      )}

      <h4>Sandbox</h4>
      <div className="send-hold cowork-net">
        <span className="toggle-text"><b>Network for the Linux sandbox</b><small>{SANDBOX_NET_HELP[sbxMode]} A change applies to new sandboxes; reset one to pick it up.</small></span>
        <div className="seg" role="group" aria-label="Network for the Linux sandbox">
          {SANDBOX_NET.map((n) => (
            <button key={n.mode} type="button" className={sbxMode === n.mode ? 'on' : ''} aria-pressed={sbxMode === n.mode} onClick={() => patch({ sandboxNetwork: n.mode })}>{n.label}</button>
          ))}
        </div>
      </div>

      <h4>Browser</h4>
      <Toggle title="Let desks use a browser" help="Gives desks a browser they can read and click in. It asks before submitting forms, entering passwords or uploading."
        checked={draft.browserEnabled !== false} onChange={(browserEnabled) => patch({ browserEnabled })} />
      <NumField title="Tabs per desk" settingKey="browserMaxTabs" value={draft.browserMaxTabs} fallback={4}
        help="Between 1 and 12. A desk past this has to close a tab first." onCommit={(n) => patch({ browserMaxTabs: n })} />
      <HostList title="Allowed sites" help="Sites a desk may open even when a link came from something it read, instead of being asked. A name also allows its subdomains."
        value={draft.browserAllowlist ?? []} onChange={(browserAllowlist) => patch({ browserAllowlist })} />

      <h4>Vision</h4>
      <label>
        <span>Model that reads pictures</span>
        <input list="cowork-vision-models" value={draft.visionModel ?? ''} placeholder="Same as the chat model" spellCheck={false}
          onChange={(e) => patch({ visionModel: e.target.value })} />
        <datalist id="cowork-vision-models">{models.map((m) => <option key={m.id} value={m.id} />)}</datalist>
        <small className="muted">Empty uses the chat model when it can read images; otherwise pictures are read with OCR only.</small>
      </label>

      <h4>Work environment</h4>
      <WorkEnv draft={draft} saved={saved} patch={patch} />
    </div>
  )
}
