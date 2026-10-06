import { useEffect, useState } from 'react'
import { X } from 'lucide-react'
import type { AgentBrowserSignIn, Settings } from '@shared/types'
import { api } from '../lib/api'
import { useStore } from '../store'
import { hostError, networkMode, networkPatch, normalizeHost, sandboxNetMode, type NetworkMode } from '../lib/coworkSettings'

/**
 * Settings > Advanced > Desks and background: which model reads pictures, and the Python environment its
 * code runs in. What a desk's shell and browser may reach, and its finishing checks, are permissions: ShellNetwork,
 * BrowserAccess and DeskGates below are mounted in Settings > Advanced. Everything edits the modal's `draft` through
 * `patch`, so nothing is saved until Save — except the environment build and browser sign-ins, which are actions on
 * the machine, not settings.
 */

const Toggle = ({ title, help, checked, onChange }: { title: string; help: string; checked: boolean; onChange: (v: boolean) => void }): JSX.Element => (
  <label className="toggle-row plain">
    <span className="toggle-text"><b>{title}</b><small>{help}</small></span>
    <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} /><span className="switch" />
  </label>
)

/**
 * What the agent's browser remembers: sites with saved cookies, mostly sign-ins made while the user took over.
 * An action on the machine like the environment build, so it applies at once rather than on Save.
 */
function SignIns(): JSX.Element | null {
  const ab = window.os?.agentBrowser
  const [rows, setRows] = useState<AgentBrowserSignIn[] | null>(null)
  const [error, setError] = useState('')
  const load = (): void => { ab?.signIns().then(setRows, (e: Error) => setError(e.message)) }
  useEffect(load, [ab])
  if (!ab) return null
  const clear = (domain?: string): void => {
    if (!domain && !confirm('Sign the agent browser out of every site? Open agent browsers close.')) return
    setError('')
    ab.clearSignIns(domain).then(load, (e: Error) => setError(e.message))
  }
  return (
    <div className="workspace-roots">
      <span><b>Browser sign-ins</b></span>
      <p className="muted small">Sites the agent's browser keeps cookies for, mostly from when you took over to sign in. Page reads use the same store, so removing a site signs both out.</p>
      {rows && rows.length === 0 && <p className="muted small">Nothing saved.</p>}
      {rows && rows.length > 0 && (
        <ul className="plain-list">
          {rows.map((r) => (
            <li key={r.domain} className="chip-check-row">
              <code>{r.domain}</code> <span className="muted small">{r.count} cookie{r.count === 1 ? '' : 's'}</span>
              <button type="button" aria-label={`Remove ${r.domain}`} title="Remove" onClick={() => clear(r.domain)}><X size={12} /></button>
            </li>
          ))}
        </ul>
      )}
      {rows && rows.length > 0 && <div className="workspace-roots-add"><button type="button" onClick={() => clear()}>Clear all</button></div>}
      {error && <p className="cowork-error" role="alert">{error}</p>}
    </div>
  )
}

/** A list of hostnames, type, Enter or Add, remove with the x. An entry also allows its subdomains. */
export function HostList({ title, help, value, onChange }: { title: string; help: string; value: string[]; onChange: (next: string[]) => void }): JSX.Element {
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

/** Network for shell commands (the egress proxy), and the hosts it lets through. The sandbox's own network is one
 *  control in SandboxSettings; the host list shows when either one uses it. */
export function ShellNetwork({ draft, patch }: { draft: Settings; patch: (p: Partial<Settings>) => void }): JSX.Element {
  const mode = networkMode(draft)
  const hosts = draft.shellAllowedDomains ?? []
  const pickMode = (m: NetworkMode): void => patch(networkPatch(m, m === 'off' ? [] : hosts))
  return (
    <div className="cowork-settings">
      <div className="send-hold cowork-net">
        <span className="toggle-text"><b>Network for commands</b><small>{NETWORK_HELP[mode]}</small></span>
        <div className="seg" role="group" aria-label="Network for commands">
          {NETWORK.map((n) => (
            <button key={n.mode} type="button" className={mode === n.mode ? 'on' : ''} aria-pressed={mode === n.mode} onClick={() => pickMode(n.mode)}>{n.label}</button>
          ))}
        </div>
        {mode === 'open' && <p className="cowork-error">Open network lets a command send files off this Mac, and whatever it downloads is untrusted text. Prefer allowed hosts.</p>}
      </div>
      {(mode !== 'off' || sandboxNetMode(draft.sandboxNetwork) === 'proxy') && (
        <HostList title="Allowed hosts" help="Hostnames a command or the Linux sandbox may reach, such as pypi.org. A name also allows its subdomains. No scheme, path, wildcard or IP address."
          value={hosts} onChange={(shellAllowedDomains) => patch({ shellAllowedDomains })} />
      )}
    </div>
  )
}

/** The agent's browser: whether desks get one, the sites it may open after reading untrusted content, its sign-ins. */
export function BrowserAccess({ draft, patch }: { draft: Settings; patch: (p: Partial<Settings>) => void }): JSX.Element {
  return (
    <div className="cowork-settings">
      <Toggle title="Let desks use a browser" help="Gives desks a browser they can read and click in. It asks before submitting forms, entering passwords or uploading."
        checked={draft.browserEnabled !== false} onChange={(browserEnabled) => patch({ browserEnabled })} />
      <HostList title="Allowed sites" help="Sites the browser may open even when a link came from something it read, instead of being asked. The fetch allowlist above counts too. A name also allows its subdomains."
        value={draft.browserAllowlist ?? []} onChange={(browserAllowlist) => patch({ browserAllowlist })} />
      <SignIns />
    </div>
  )
}

/** What a desk may do without a card, and what it must pass before it may finish. */
export function DeskGates({ draft, patch }: { draft: Settings; patch: (p: Partial<Settings>) => void }): JSX.Element {
  return (
    <div className="cowork-settings">
      <Toggle title="Run sandboxed commands without asking" help="Run sandboxed commands inside a desk's own folder without asking."
        checked={draft.deskShellAuto !== false} onChange={(deskShellAuto) => patch({ deskShellAuto })} />
      <Toggle title="Check before finishing" help="Don't let a desk finish with open steps or missing files."
        checked={draft.deskDoneGate !== false} onChange={(deskDoneGate) => patch({ deskDoneGate })} />
      <Toggle title="Review against the brief" help="Have a reviewer check the result against the brief before finishing."
        checked={draft.deskSelfReview !== false} onChange={(deskSelfReview) => patch({ deskSelfReview })} />
    </div>
  )
}

export default function CoworkSettings({ draft, patch }: { draft: Settings; patch: (p: Partial<Settings>) => void }): JSX.Element {
  const saved = useStore((s) => s.settings)
  const models = useStore((s) => s.models)
  return (
    <div className="cowork-settings">
      <h4>Desks</h4>
      <Toggle title="Start new chats working autonomously" help="A new chat hands its task to a desk that works on its own at Ask as it goes, until it is done or needs you. Switch it off per chat under the composer."
        checked={draft.autonomousByDefault !== false} onChange={(autonomousByDefault) => patch({ autonomousByDefault })} />
      <Toggle title="Resume desks after a restart" help="Carry on desks the app was running when it quit. A desk with an action whose outcome is unknown, or one waiting on your approval or plan, still waits for you."
        checked={draft.deskAutoResume === true} onChange={(deskAutoResume) => patch({ deskAutoResume })} />
      <Toggle title="Notify me" help="A system notification when a desk needs you or finishes, while the window is not in front."
        checked={draft.deskNotify !== false} onChange={(deskNotify) => patch({ deskNotify })} />

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

/** The desk sandbox folder switch, under Settings > Advanced > Desks and background. */
export function CoworkAdvanced({ draft, patch }: { draft: Settings; patch: (p: Partial<Settings>) => void }): JSX.Element {
  return (
    <div className="cowork-settings">
      <label className="toggle-row plain">
        <span className="toggle-text"><b>Share the desk folder with its sandbox</b><small>A desk's Linux sandbox sees that desk's workspace at /workspace/desk. Nothing else of your Mac is shared.</small></span>
        <input type="checkbox" checked={draft.sandboxMountDesk !== false} onChange={(e) => patch({ sandboxMountDesk: e.target.checked })} /><span className="switch" />
      </label>
    </div>
  )
}
